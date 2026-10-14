"""Viettel S-Invoice (vInvoice) adapter – REST/JSON, HSM signing.

Field names and flows follow Odoo's maintained `l10n_vn_edi_viettel`
module and Viettel's published web-service description (see
docs/EINVOICE.md for the sources):

- login: POST /auth/login {username, password} -> access_token, sent
  back as the cookie `access_token`;
- issue/adjust: POST .../InvoiceWS/createInvoice/{tax code}; the
  idempotency key is `transactionUuid` (Viettel returns the same
  invoice for a repeated UUID within 3 days);
- lookup: POST .../InvoiceWS/searchInvoiceByTransactionUuid (form);
- files: POST .../InvoiceUtilsWS/getInvoiceRepresentationFile, PDF or
  ZIP (the signed XML is inside the ZIP).

`invoiceNo` in Viettel's answers is series + number ("C26TAA12").
"""

from __future__ import annotations

import base64
import io
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

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

API = "/services/einvoiceapplication/api"
VN_TZ = timezone(timedelta(hours=7))
TAX_PERCENT = {NOT_SUBJECT: -2, NOT_DECLARED: -1}
PAYMENT_CODES = {"TM": "1", "CK": "2", "TM/CK": "3"}


def _ms(d: date, hour: int = 12) -> int:
    """Epoch milliseconds of `d` at `hour` o'clock Vietnam time."""
    return (
        int(datetime(d.year, d.month, d.day, hour, tzinfo=VN_TZ).timestamp())
        * 1000
    )


def _tax_percent(rate: str) -> int | float:
    return TAX_PERCENT[rate] if rate in TAX_PERCENT else _num(rate)


def _num(value) -> int | float:
    if value is None:
        return 0
    f = float(value)
    return int(f) if f == int(f) else f


class ViettelProvider(EInvoiceProvider):
    code = "viettel"
    name = "Viettel S-Invoice"
    fields = (
        ConfigField(
            "username",
            "Tên đăng nhập API",
            hint="Thường là mã số thuế của công ty",
        ),
        ConfigField("password", "Mật khẩu API", secret=True),
        ConfigField(
            "template_code",
            "Mã mẫu hóa đơn trên S-Invoice",
            hint="Dạng 1/001 – xem trong danh sách mẫu trên S-Invoice",
            placeholder="1/001",
        ),
    )
    default_urls = {
        "test": "https://api-vinvoice.viettel.vn",
        "production": "https://api-vinvoice.viettel.vn",
    }
    timeout = 90.0

    _token: str | None = None
    _token_until: float = 0.0

    # ------------------------------------------------------------ auth

    def _login(self) -> str:
        if self._token and time.monotonic() < self._token_until:
            return self._token
        resp = self._request(
            "POST",
            f"{self.base_url}/auth/login",
            json={
                "username": self.value("username"),
                "password": self.value("password"),
            },
        )
        data = self._json(resp)
        token = data.get("access_token")
        if resp.status_code >= 400 or not token:
            raise ProviderRejectedError(
                "Đăng nhập S-Invoice thất bại: "
                + str(
                    data.get("message")
                    or data.get("error_description")
                    or data.get("error")
                    or f"HTTP {resp.status_code}"
                ),
                raw=data,
            )
        lifetime = float(data.get("expires_in") or 300)
        self._token = token
        self._token_until = time.monotonic() + max(30.0, lifetime - 30)
        return token

    def _call(self, path: str, *, json=None, data=None) -> dict:
        token = self._login()
        resp = self._request(
            "POST",
            f"{self.base_url}{API}/{path}",
            json=json,
            data=data,
            headers={"Cookie": f"access_token={token}"},
        )
        body = self._json(resp)
        error = (
            body.get("errorCode")
            or body.get("error")
            or (
                body.get("code")
                if body.get("code") not in (None, 0, 200)
                else None
            )
        )
        if resp.status_code >= 400 or error:
            message = (
                body.get("data")
                or body.get("description")
                or body.get("message")
                or error
                or f"HTTP {resp.status_code}"
            )
            raise ProviderRejectedError(
                f"S-Invoice: {message}", code=str(error or ""), raw=body
            )
        return body

    @staticmethod
    def _json(resp) -> dict:
        try:
            data = resp.json()
        except ValueError as e:
            raise ProviderUnavailableError(
                "S-Invoice trả về dữ liệu không đọc được",
                raw=resp.text[:2000],
            ) from e
        return data if isinstance(data, dict) else {"result": data}

    # ---------------------------------------------------------- payload

    def _template_code(self) -> str:
        return self.value("template_code") or f"{self.config.template}/001"

    def payload(self, inv: CanonicalInvoice) -> dict:
        decrease = inv.kind == "adjust_down"
        general = {
            "invoiceType": inv.template,
            "templateCode": self._template_code(),
            "invoiceSeries": inv.series,
            "invoiceIssuedDate": int(time.time()) * 1000,
            "currencyCode": inv.currency,
            "adjustmentType": "5" if inv.is_adjustment else "1",
            "paymentStatus": False,
            "cusGetInvoiceRight": True,
            "transactionUuid": inv.key,
            "validation": 1,
        }
        if inv.is_adjustment and inv.original:
            general |= {
                "adjustmentInvoiceType": "1",
                "originalInvoiceId": inv.original.series + inv.original.number,
                "originalInvoiceIssueDate": _ms(inv.original.issue_date),
                "originalTemplateCode": self._template_code(),
                "originalInvoiceType": "1",
                "adjustedNote": inv.reason[:255],
            }
        has_org = bool(inv.buyer_tax_code)
        buyer = {
            "buyerName": inv.buyer_name
            or ("" if has_org else inv.buyer_legal_name),
            "buyerLegalName": inv.buyer_legal_name if has_org else "",
            "buyerTaxCode": inv.buyer_tax_code,
            "buyerAddressLine": inv.buyer_address,
            "buyerEmail": inv.buyer_email,
            "buyerCode": inv.buyer_code,
            "buyerNotGetInvoice": 0,
        }
        items = []
        for ln in inv.lines:
            item = {
                "lineNumber": ln.line_no,
                "selection": 1,
                "itemCode": ln.code,
                "itemName": ln.name,
                "unitName": ln.unit,
                "unitPrice": _num(ln.unit_price)
                if ln.unit_price is not None
                else ln.gross,
                "quantity": _num(ln.quantity)
                if ln.quantity is not None
                else 1,
                "itemTotalAmountWithoutTax": ln.gross,
                "taxPercentage": _tax_percent(ln.vat_rate),
                "taxAmount": ln.vat_amount,
                "itemTotalAmountAfterDiscount": ln.amount,
                "itemTotalAmountWithTax": ln.amount + ln.vat_amount,
            }
            if ln.discount:
                item["itemDiscount"] = ln.discount
            if inv.is_adjustment:
                item["isIncreaseItem"] = not decrease
            items.append(item)
        breakdowns = []
        for g in inv.tax_groups:
            row = {
                "taxPercentage": _tax_percent(g.vat_rate),
                "taxableAmount": g.base,
                "taxAmount": g.vat,
            }
            if inv.is_adjustment:
                row |= {
                    "taxableAmountPos": not decrease,
                    "taxAmountPos": not decrease,
                }
            breakdowns.append(row)
        summary = {
            "sumOfTotalLineAmountWithoutTax": inv.total_before_tax
            + inv.total_discount,
            "totalAmountWithoutTax": inv.total_before_tax,
            "totalTaxAmount": inv.total_vat,
            "totalAmountWithTax": inv.total,
            "totalAmountAfterDiscount": inv.total_before_tax,
            "discountAmount": inv.total_discount,
            "totalAmountWithTaxInWords": inv.amount_in_words,
        }
        if inv.is_adjustment:
            summary |= {
                "isTotalAmountPos": not decrease,
                "isTotalTaxAmountPos": not decrease,
                "isTotalAmtWithoutTaxPos": not decrease,
            }
        return {
            "generalInvoiceInfo": general,
            "buyerInfo": buyer,
            "payments": [
                {
                    "paymentMethod": PAYMENT_CODES.get(
                        inv.payment_method, "3"
                    ),
                    "paymentMethodName": inv.payment_method,
                }
            ],
            "itemInfo": items,
            "taxBreakdowns": breakdowns,
            "summarizeInfo": summary,
        }

    # ---------------------------------------------------------- results

    def _number(self, invoice_no: str, series: str) -> str:
        no = (invoice_no or "").strip()
        return no[len(series) :] if series and no.startswith(series) else no

    def _result(self, inv: CanonicalInvoice, row: dict) -> IssueResult:
        issued = row.get("issueDate")
        issue_date = inv.issue_date
        if isinstance(issued, (int, float)) and issued > 0:
            issue_date = datetime.fromtimestamp(issued / 1000, VN_TZ).date()
        code = (row.get("codeOfTax") or "").strip()
        return IssueResult(
            status=ISSUED if code else SIGNED,
            number=self._number(row.get("invoiceNo"), inv.series),
            template=inv.template,
            series=inv.series,
            issue_date=issue_date,
            provider_ref=str(
                row.get("transactionID") or row.get("invoiceId") or inv.key
            ),
            lookup_code=row.get("reservationCode") or "",
            tax_authority_code=code,
            raw=row,
        )

    # -------------------------------------------------------------- API

    def test_connection(self) -> str:
        self._login()
        body = self._call(
            "InvoiceAPI/InvoiceUtilsWS/getAllInvoiceTemplates",
            json={"taxCode": self.config.tax_code, "invoiceType": "all"},
        )
        templates = body.get("template") or []
        codes = {
            (t.get("templateCode"), t.get("invoiceSeri")) for t in templates
        }
        if (
            templates
            and (self._template_code(), self.config.series) not in codes
        ):
            raise ProviderRejectedError(
                f"Không thấy mẫu {self._template_code()} ký hiệu "
                f"{self.config.series} trên S-Invoice"
            )
        return "Kết nối S-Invoice thành công"

    def issue(self, invoice: CanonicalInvoice) -> IssueResult:
        body = self._call(
            f"InvoiceAPI/InvoiceWS/createInvoice/{self.config.tax_code}",
            json=self.payload(invoice),
        )
        row = body.get("result") or {}
        if not row.get("invoiceNo"):
            # Numbering can be asynchronous: look it up by UUID later.
            raise ProviderUnavailableError(
                "S-Invoice chưa trả số hóa đơn, sẽ tra cứu lại", raw=body
            )
        return self._result(invoice, row)

    def find(self, invoice: CanonicalInvoice) -> IssueResult | None:
        try:
            body = self._call(
                "InvoiceAPI/InvoiceWS/searchInvoiceByTransactionUuid",
                data={
                    "supplierTaxCode": self.config.tax_code,
                    "transactionUuid": invoice.key,
                },
            )
        except ProviderRejectedError as e:
            if "NOT_FOUND" in (e.code or "") or "NOT_FOUND" in e.message:
                return None
            raise
        rows = body.get("result") or []
        if isinstance(rows, dict):
            rows = [rows]
        rows = [r for r in rows if r.get("invoiceNo")]
        return self._result(invoice, rows[0]) if rows else None

    def download(
        self, invoice: CanonicalInvoice, result: IssueResult, kind: str
    ) -> bytes:
        body = self._call(
            "InvoiceAPI/InvoiceUtilsWS/getInvoiceRepresentationFile",
            json={
                "supplierTaxCode": self.config.tax_code,
                "templateCode": self._template_code(),
                "invoiceNo": f"{result.series}{result.number}",
                "fileType": "PDF" if kind == "pdf" else "ZIP",
            },
        )
        raw = body.get("fileToBytes")
        if not raw:
            raise ProviderUnavailableError("S-Invoice chưa có file hóa đơn")
        data = base64.b64decode(raw)
        return data if kind == "pdf" else _xml_from_zip(data)


def _xml_from_zip(data: bytes, depth: int = 0) -> bytes:
    """Return the first XML file in a (possibly nested) ZIP."""
    if depth > 2:
        raise ProviderError("File ZIP lồng nhau quá sâu")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.file_size > 10 * 1024 * 1024:
                continue
            name = info.filename.lower()
            if name.endswith(".xml"):
                return zf.read(info)
            if name.endswith(".zip"):
                return _xml_from_zip(zf.read(info), depth + 1)
    raise ProviderError("Không thấy file XML trong gói hóa đơn")
