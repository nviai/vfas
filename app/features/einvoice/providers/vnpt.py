"""VNPT Invoice adapter – SOAP 1.1 web services, HSM signing.

Follows the live WSDLs of the VNPT TT78 hosts and the invoice XML that
VNPT's own Excel add-in and open-source clients send (see
docs/EINVOICE.md):

- every company has its own host (https://<MST>-tt78cadmin.vnpt-invoice
  .com.vn, demo hosts are assigned by VNPT): enter it as the API
  address;
- credentials travel in every call: Account/ACpass (company admin) and
  username/password (web-service user);
- issue: PublishService.ImportAndPublishInv with <Invoices><Inv><key>
  (our idempotency key = VNPT "Fkey"); answer "OK:pattern;serial-key_no"
  or "ERR:n";
- lookup: PortalService.listInvByCusFkey; tax authority code:
  PublishService.GetMCCQThueByFkeys (base64 XML);
- files: PortalService.downloadInvPDFFkeyNoPay / downloadInvFkeyNoPay.

VNPT does not confirm that a repeated Fkey is refused, so an invoice
whose answer was lost is always looked up before it is sent again.
Adjustment invoices are not offered (the TT78 adjustment XML is not
published).
"""

from __future__ import annotations

import base64
import binascii
from datetime import timedelta
from xml.sax.saxutils import escape

from defusedxml import ElementTree as SafeET

from app.features.einvoice.providers.base import (
    ISSUED,
    NOT_DECLARED,
    NOT_SUBJECT,
    SIGNED,
    CanonicalInvoice,
    ConfigField,
    EInvoiceProvider,
    IssueResult,
    ProviderError,
    ProviderRejectedError,
    ProviderUnavailableError,
)

TNS = "http://tempuri.org/"
VAT_RATES = {NOT_SUBJECT: "-1", NOT_DECLARED: "-2"}
ERRORS = {
    "1": "sai tài khoản hoặc không có quyền",
    "3": "dữ liệu hóa đơn không hợp lệ",
    "4": "mẫu số không tồn tại",
    "5": "không phát hành được hóa đơn",
    "6": "không đủ số hóa đơn hoặc không tìm thấy hóa đơn",
    "7": "người dùng không thuộc công ty",
    "10": "lô hóa đơn quá lớn",
    "20": "mẫu số/ký hiệu không khớp, chưa đăng ký hoặc hết số",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _x(tag: str, value) -> str:
    if value is None or value == "":
        return f"<{tag} />"
    return f"<{tag}>{escape(str(value))}</{tag}>"


def _n(value) -> str:
    if value is None:
        return ""
    f = float(value)
    return str(int(f)) if f == int(f) else f"{f:.4f}".rstrip("0")


class VnptProvider(EInvoiceProvider):
    code = "vnpt"
    name = "VNPT Invoice"
    fields = (
        ConfigField(
            "account",
            "Tài khoản quản trị (Account)",
            hint="Dạng <MST>_admin",
        ),
        ConfigField(
            "account_password", "Mật khẩu quản trị (ACpass)", secret=True
        ),
        ConfigField(
            "username",
            "Tài khoản web service",
            hint="Do VNPT cấp, ví dụ <MST>service",
        ),
        ConfigField("password", "Mật khẩu web service", secret=True),
        ConfigField(
            "pattern",
            "Mẫu số trên VNPT (pattern)",
            hint="Ví dụ 1/001",
            placeholder="1/001",
        ),
    )
    default_urls = {}
    supports_adjustment = False
    timeout = 90.0

    # ------------------------------------------------------- transport

    def _call(self, service: str, op: str, params: list[tuple[str, str]]):
        if not self.base_url.startswith("https://"):
            raise ProviderRejectedError(
                "Nhập địa chỉ web service VNPT của công ty "
                "(https://<MST>-tt78cadmin.vnpt-invoice.com.vn)"
            )
        body = "".join(
            f"<{k}>{v}</{k}>" if k == "xmlInvData" else _x(k, v)
            for k, v in params
        )
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/'
            'envelope/"><soap:Body>'
            f'<{op} xmlns="{TNS}">{body}</{op}>'
            "</soap:Body></soap:Envelope>"
        )
        resp = self._request(
            "POST",
            f"{self.base_url}/{service}",
            content=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"{TNS}{op}"',
            },
        )
        try:
            root = SafeET.fromstring(resp.content)
        except Exception as e:  # noqa: BLE001 - any parse error
            raise ProviderUnavailableError(
                "VNPT trả về dữ liệu không đọc được", raw=resp.text[:2000]
            ) from e
        for el in root.iter():
            if _local(el.tag) == f"{op}Result":
                return el.text or "", el
        fault = next(
            (e.text for e in root.iter() if _local(e.tag) == "faultstring"),
            None,
        )
        raise ProviderRejectedError(
            f"VNPT: {fault or 'lỗi SOAP'}", raw=resp.text[:2000]
        )

    def _publish_auth(self) -> list[tuple[str, str]]:
        return [
            ("Account", self.value("account")),
            ("ACpass", self.value("account_password")),
        ]

    @staticmethod
    def _check(text: str) -> str:
        text = (text or "").strip()
        if text.startswith("ERR:"):
            code = text[4:].split(":")[0].strip()
            raise ProviderRejectedError(
                f"VNPT: {ERRORS.get(code, 'lỗi')} ({text})", code=code
            )
        return text

    # ---------------------------------------------------------- payload

    def invoice_xml(self, inv: CanonicalInvoice) -> str:
        products = []
        for ln in inv.lines:
            products.append(
                "<Product>"
                + _x("ProdName", ln.name)
                + _x("ProdUnit", ln.unit)
                + _x(
                    "ProdQuantity",
                    _n(ln.quantity) if ln.quantity is not None else "1",
                )
                + _x(
                    "ProdPrice",
                    _n(ln.unit_price)
                    if ln.unit_price is not None
                    else ln.amount,
                )
                + _x("Amount", ln.amount + ln.vat_amount)
                + _x("Total", ln.amount)
                + _x("VATRate", VAT_RATES.get(ln.vat_rate, ln.vat_rate))
                + _x("VATAmount", ln.vat_amount)
                + _x("IsSum", 0)
                + "</Product>"
            )
        groups = {g.vat_rate: g for g in inv.tax_groups}

        def gross(rate: str, vat: bool = False) -> str:
            g = groups.get(rate)
            return "" if g is None else str(g.vat if vat else g.base)

        first_rate = inv.lines[0].vat_rate if inv.lines else "10"
        body = (
            "<Invoices><Inv>"
            + _x("key", inv.key)
            + "<Invoice>"
            + _x("CusCode", inv.buyer_code)
            + _x("CusName", inv.buyer_legal_name)
            + _x("CusAddress", inv.buyer_address)
            + _x("CusPhone", "")
            + _x("CusTaxCode", inv.buyer_tax_code)
            + _x("PaymentMethod", inv.payment_method)
            + "<Products>"
            + "".join(products)
            + "</Products>"
            + _x("Total", inv.total_before_tax)
            + _x("VATRate", VAT_RATES.get(first_rate, first_rate))
            + _x("VATAmount", inv.total_vat)
            + _x("Amount", inv.total)
            + _x("AmountInWords", inv.amount_in_words)
            + _x("ArisingDate", inv.issue_date.strftime("%d/%m/%Y"))
            + _x("GrossValue", gross(NOT_SUBJECT))
            + _x("GrossValue_NonTax", gross(NOT_DECLARED))
            + _x("GrossValue0", gross("0"))
            + _x("VatAmount0", gross("0", True))
            + _x("GrossValue5", gross("5"))
            + _x("VatAmount5", gross("5", True))
            + _x("GrossValue8", gross("8"))
            + _x("VatAmount8", gross("8", True))
            + _x("GrossValue10", gross("10"))
            + _x("VatAmount10", gross("10", True))
            + _x("Buyer", inv.buyer_name)
            + _x("CurrencyUnit", inv.currency)
            + _x("EmailDeliver", inv.buyer_email)
            + "</Invoice></Inv></Invoices>"
        )
        # The payload is itself XML: send it as CDATA.
        return "<![CDATA[" + body.replace("]]>", "]]]]><![CDATA[>") + "]]>"

    # -------------------------------------------------------------- API

    def test_connection(self) -> str:
        text, _ = self._call(
            "PortalService.asmx",
            "listInvByCusFkey",
            [
                ("key", "VFAS-KIEM-TRA"),
                ("fromDate", ""),
                ("toDate", ""),
                ("userName", self.value("username")),
                ("userPass", self.value("password")),
            ],
        )
        if text.strip().startswith("ERR:1"):
            self._check(text)
        return "Kết nối VNPT Invoice thành công"

    def issue(self, invoice: CanonicalInvoice) -> IssueResult:
        text, _ = self._call(
            "PublishService.asmx",
            "ImportAndPublishInv",
            self._publish_auth()
            + [
                ("xmlInvData", self.invoice_xml(invoice)),
                ("username", self.value("username")),
                ("password", self.value("password")),
                ("pattern", self.value("pattern")),
                ("serial", invoice.series),
                ("convert", "0"),
            ],
        )
        text = self._check(text)
        if not text.startswith("OK:"):
            raise ProviderUnavailableError(
                f"VNPT trả kết quả không rõ: {text[:200]}"
            )
        # OK:pattern;serial-key_no
        rest = text[3:].split(";", 1)[-1]
        items = rest.split("-", 1)[-1]
        number = ""
        for item in items.split(","):
            key, _sep, no = item.rpartition("_")
            if key == invoice.key:
                number = no.strip().lstrip("0")
        if not number:
            raise ProviderUnavailableError(
                "VNPT không trả số hóa đơn, sẽ tra cứu lại", raw={"raw": text}
            )
        result = IssueResult(
            status=SIGNED,
            number=number,
            template=invoice.template,
            series=invoice.series,
            issue_date=invoice.issue_date,
            provider_ref=invoice.key,
            raw={"raw": text},
        )
        try:
            return self.status(invoice, result)
        except ProviderError:
            return result  # the code is fetched by the next refresh

    def find(self, invoice: CanonicalInvoice) -> IssueResult | None:
        d = invoice.issue_date
        text, _ = self._call(
            "PortalService.asmx",
            "listInvByCusFkey",
            [
                ("key", invoice.key),
                ("fromDate", (d - timedelta(days=1)).strftime("%d/%m/%Y")),
                ("toDate", (d + timedelta(days=3)).strftime("%d/%m/%Y")),
                ("userName", self.value("username")),
                ("userPass", self.value("password")),
            ],
        )
        text = (text or "").strip()
        if not text or text.startswith("ERR:6"):
            return None
        self._check(text)
        try:
            root = SafeET.fromstring(text)
        except Exception:  # noqa: BLE001 - not a list
            return None
        for item in root.iter():
            if _local(item.tag) != "Item":
                continue
            fields = {_local(c.tag): (c.text or "").strip() for c in item}
            number = fields.get("invNum", "").lstrip("0")
            if number:
                result = IssueResult(
                    status=SIGNED,
                    number=number,
                    template=invoice.template,
                    series=fields.get("serial") or invoice.series,
                    issue_date=invoice.issue_date,
                    provider_ref=invoice.key,
                    raw=fields,
                )
                try:
                    return self.status(invoice, result)
                except ProviderError:
                    return result
        return None

    def status(
        self, invoice: CanonicalInvoice, result: IssueResult
    ) -> IssueResult:
        text, _ = self._call(
            "PublishService.asmx",
            "GetMCCQThueByFkeys",
            self._publish_auth()
            + [
                ("username", self.value("username")),
                ("password", self.value("password")),
                ("pattern", self.value("pattern")),
                ("fkeys", invoice.key),
            ],
        )
        text = self._check(text)
        try:
            xml = base64.b64decode(text, validate=True)
            root = SafeET.fromstring(xml)
        except (ValueError, binascii.Error, SafeET.ParseError):
            return result
        for el in root.iter():
            if _local(el.tag) == "MCCQThue" and (el.text or "").strip():
                result.status = ISSUED
                result.tax_authority_code = el.text.strip()
        return result

    def download(
        self, invoice: CanonicalInvoice, result: IssueResult, kind: str
    ) -> bytes:
        op = (
            "downloadInvPDFFkeyNoPay"
            if kind == "pdf"
            else ("downloadInvFkeyNoPay")
        )
        text, _ = self._call(
            "PortalService.asmx",
            op,
            [
                ("fkey", invoice.key),
                ("userName", self.value("username")),
                ("userPass", self.value("password")),
            ],
        )
        text = self._check(text)
        if not text:
            raise ProviderUnavailableError("VNPT chưa có file hóa đơn")
        if kind == "xml":
            return text.encode("utf-8")
        return base64.b64decode(text)
