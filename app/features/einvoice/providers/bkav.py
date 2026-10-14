"""BKAV eHoadon adapter – SOAP `ExecCommand`, encrypted JSON commands.

Follows Bkav's integration guide and WSDL (see docs/EINVOICE.md):

- every call is `ExecCommand(partnerGUID, CommandData)` where
  CommandData = base64(AES-256-CBC(gzip(json))) with the key and IV
  from the PartnerToken "base64(key):base64(iv)";
- the decrypted answer is {"Status": 0|1, "Object": ..., ...}; batch
  items carry their own Status/MessLog;
- 112 creates an invoice on the configured form/serial and Bkav
  assigns the number; 205 signs it with the account's HSM; 124 creates
  an adjustment (lines carry IsIncrease); 800 reads an invoice by the
  partner id; 850 returns the tax authority code; 808/809 return the
  PDF/XML.

`PartnerInvoiceStringID` (our idempotency key) is Bkav's duplicate
guard. HSM signing is required: invoices signed with a USB token in
the Bkav web app cannot be completed from here.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import json
from datetime import datetime
from xml.sax.saxutils import escape

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
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
# Our rate code -> (TaxRateID, TaxRate)
TAX_RATES = {
    "0": (1, 0.0),
    "5": (2, 5.0),
    "10": (3, 10.0),
    NOT_SUBJECT: (4, -1.0),
    NOT_DECLARED: (5, -2.0),
    "8": (9, 8.0),
}
PAY_METHODS = {"TM": 1, "CK": 2, "TM/CK": 3}
# InvoiceStatusID
PUBLISHED = 2
NUMBERED_UNSIGNED = 11
# TaxStatus (850)
TAX_APPROVED = 33
TAX_REFUSED = (35, 38)
CREATE = 112
CREATE_ADJUSTMENT = 124
SIGN = 205
GET = 800
GET_TAX_STATUS = 850
GET_PDF = 808
GET_XML = 809
DUPLICATE = "Đã tồn tại Hóa đơn có PartnerInvoice"


def parse_token(token: str) -> tuple[bytes, bytes]:
    try:
        key_b64, iv_b64 = token.strip().split(":", 1)
        key = base64.b64decode(key_b64.strip(), validate=True)
        iv = base64.b64decode(iv_b64.strip(), validate=True)
    except (ValueError, binascii.Error) as e:
        raise ProviderRejectedError(
            "PartnerToken phải có dạng base64(key):base64(IV)"
        ) from e
    if len(key) != 32 or len(iv) != 16:
        raise ProviderRejectedError(
            "PartnerToken phải gồm khóa 32 byte và IV 16 byte"
        )
    return key, iv


def encode_command(cmd_type: int, obj, token: str) -> str:
    key, iv = parse_token(token)
    plain = json.dumps(
        {"CmdType": cmd_type, "CommandObject": obj}, ensure_ascii=False
    ).encode("utf-8")
    padder = padding.PKCS7(128).padder()
    data = padder.update(gzip.compress(plain)) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


def decode_result(text: str, token: str):
    """Decrypt a result; plain-text refusals are returned unchanged."""
    key, iv = parse_token(token)
    try:
        raw = base64.b64decode(text, validate=True)
        if not raw or len(raw) % 16:
            raise ValueError("not AES")
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        unpadder = padding.PKCS7(128).unpadder()
        data = unpadder.update(dec.update(raw) + dec.finalize())
        data += unpadder.finalize()
    except (ValueError, binascii.Error):
        data = text.encode("utf-8")
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    try:
        body = json.loads(data.decode("utf-8"))
    except ValueError:
        return data.decode("utf-8", "replace")
    if isinstance(body, dict):
        obj = body.get("Object")
        if isinstance(obj, str) and obj.strip()[:1] in ("[", "{"):
            try:
                body["Object"] = json.loads(obj)
            except ValueError:
                pass
    return body


class BkavProvider(EInvoiceProvider):
    code = "bkav"
    name = "BKAV eHoadon"
    fields = (
        ConfigField(
            "partner_guid", "PartnerGUID", hint="Bkav cấp cho điểm gửi"
        ),
        ConfigField(
            "partner_token",
            "PartnerToken",
            secret=True,
            hint="Dạng base64(key):base64(IV) do Bkav cấp",
        ),
    )
    default_urls = {
        "test": "https://wsdemo.ehoadon.vn/WSPublicEHoaDon.asmx",
        "production": "https://ws.ehoadon.vn/WSPublicEHoaDon.asmx",
    }
    timeout = 90.0

    # ------------------------------------------------------- transport

    def command(self, cmd_type: int, obj):
        token = self.value("partner_token")
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/'
            'envelope/"><soap:Body>'
            f'<ExecCommand xmlns="{TNS}">'
            f"<partnerGUID>{escape(self.value('partner_guid'))}</partnerGUID>"
            f"<CommandData>{encode_command(cmd_type, obj, token)}"
            "</CommandData></ExecCommand></soap:Body></soap:Envelope>"
        )
        resp = self._request(
            "POST",
            self.base_url,
            content=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"{TNS}ExecCommand"',
            },
        )
        try:
            root = SafeET.fromstring(resp.content)
        except Exception as e:  # noqa: BLE001 - any parse error
            raise ProviderUnavailableError(
                "eHoadon trả về dữ liệu không đọc được",
                raw=resp.text[:2000],
            ) from e
        node = root.find(f".//{{{TNS}}}ExecCommandResult")
        if node is None:
            fault = root.find(".//faultstring")
            raise ProviderRejectedError(
                "eHoadon: "
                + (fault.text if fault is not None else "lỗi SOAP"),
                raw=resp.text[:2000],
            )
        body = decode_result(node.text or "", token)
        if not isinstance(body, dict):
            raise ProviderRejectedError(f"eHoadon: {body}"[:500], raw=body)
        if body.get("Status") not in (0, None) or body.get("isError"):
            raise ProviderRejectedError(
                f"eHoadon: {body.get('Object')}"[:500],
                code=str(body.get("Code") or ""),
                raw=body,
            )
        return body.get("Object")

    # ---------------------------------------------------------- payload

    def invoice_ws(self, inv: CanonicalInvoice) -> dict:
        details = []
        if inv.is_adjustment and inv.original:
            o = inv.original
            details.append(
                {
                    "ItemTypeID": 4,
                    "ItemName": (
                        f"Điều chỉnh giảm cho hóa đơn mẫu số {o.template} ký "
                        f"hiệu {o.series} số {o.number} ngày "
                        f"{o.issue_date:%d/%m/%Y}"
                    ),
                }
            )
        for ln in inv.lines:
            rate_id, rate = TAX_RATES.get(ln.vat_rate, (3, 10.0))
            row = {
                "ItemTypeID": 0,
                "IsDiscount": False,
                "ItemCode": ln.code,
                "ItemName": ln.name,
                "UnitName": ln.unit,
                "Qty": float(ln.quantity) if ln.quantity is not None else 1.0,
                "Price": float(ln.unit_price)
                if ln.unit_price is not None
                else float(ln.gross),
                "Amount": float(ln.amount),
                "TaxRateID": rate_id,
                "TaxRate": rate,
                "TaxAmount": float(ln.vat_amount),
                "DiscountRate": 0.0,
                "DiscountAmount": float(ln.discount),
                "UserDefineDetails": "",
            }
            if inv.is_adjustment:
                row["IsIncrease"] = inv.kind == "adjust_up"
            details.append(row)
        invoice = {
            "InvoiceTypeID": 1 if inv.template == "1" else 2,
            "InvoiceDate": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            + "+07:00",
            "BuyerName": inv.buyer_name,
            "BuyerTaxCode": inv.buyer_tax_code,
            "BuyerUnitName": inv.buyer_legal_name,
            "BuyerAddress": inv.buyer_address,
            "BuyerBankAccount": "",
            "PayMethodID": PAY_METHODS.get(inv.payment_method, 3),
            "ReceiveTypeID": 1,
            "ReceiverEmail": inv.buyer_email,
            "ReceiverMobile": "",
            "ReceiverAddress": "",
            "ReceiverName": "",
            "Note": inv.note[:500],
            "BillCode": "",
            "CurrencyID": inv.currency,
            "ExchangeRate": 1.0,
            "InvoiceForm": inv.template,
            "InvoiceSerial": inv.series,
            "InvoiceNo": 0,
            "UserDefine": "",
        }
        if inv.is_adjustment and inv.original:
            o = inv.original
            invoice["OriginalInvoiceIdentify"] = (
                f"[{o.template}]_[{o.series}]_[{o.number.zfill(7)}]"
            )
            invoice["Reason"] = inv.reason
        return {
            "Invoice": invoice,
            "ListInvoiceDetailsWS": details,
            "ListInvoiceAttachFileWS": [],
            "PartnerInvoiceID": 0,
            "PartnerInvoiceStringID": inv.key,
        }

    # ---------------------------------------------------------- results

    def _sign_and_check(
        self, inv: CanonicalInvoice, guid: str, number: str, lookup: str
    ) -> IssueResult:
        try:
            self.command(SIGN, guid)
        except ProviderRejectedError as e:
            # The number exists but is unsigned: look it up again later
            # (signing is retried by find()).
            raise ProviderUnavailableError(
                f"Chưa ký được hóa đơn bằng HSM: {e.message}"
            ) from e
        result = IssueResult(
            status=SIGNED,
            number=number,
            template=inv.template,
            series=inv.series,
            issue_date=inv.issue_date,
            provider_ref=guid,
            lookup_code=lookup,
        )
        return self.status(inv, result)

    # -------------------------------------------------------------- API

    def test_connection(self) -> str:
        self.command(904, self.config.tax_code)
        return "Kết nối eHoadon thành công"

    def issue(self, invoice: CanonicalInvoice) -> IssueResult:
        cmd = CREATE_ADJUSTMENT if invoice.is_adjustment else CREATE
        items = self.command(cmd, [self.invoice_ws(invoice)])
        item = (items or [{}])[0] if isinstance(items, list) else {}
        if item.get("Status") not in (0, None):
            mess = str(item.get("MessLog") or "")
            if mess.startswith(DUPLICATE):
                found = self.find(invoice)
                if found is not None:
                    return found
            raise ProviderRejectedError(
                f"eHoadon: {mess or 'tạo hóa đơn thất bại'}", raw=item
            )
        guid = str(item.get("InvoiceGUID") or "")
        number = str(item.get("InvoiceNo") or "").lstrip("0")
        if not guid or not number:
            raise ProviderUnavailableError(
                "eHoadon chưa trả số hóa đơn, sẽ tra cứu lại", raw=item
            )
        return self._sign_and_check(
            invoice, guid, number, str(item.get("MTC") or "")
        )

    def find(self, invoice: CanonicalInvoice) -> IssueResult | None:
        try:
            data = self.command(GET, invoice.key)
        except ProviderRejectedError as e:
            if "không tồn tại" in e.message:
                return None
            raise
        if not isinstance(data, dict):
            return None
        inv = data.get("Invoice") if "Invoice" in data else data
        guid = str(inv.get("InvoiceGUID") or "")
        number = str(inv.get("InvoiceNo") or "").lstrip("0")
        if not guid or not number:
            return None
        lookup = str(inv.get("InvoiceCode") or inv.get("MTC") or "")
        if inv.get("InvoiceStatusID") == NUMBERED_UNSIGNED:
            return self._sign_and_check(invoice, guid, number, lookup)
        result = IssueResult(
            status=SIGNED,
            number=number,
            template=invoice.template,
            series=invoice.series,
            issue_date=invoice.issue_date,
            provider_ref=guid,
            lookup_code=lookup,
        )
        return self.status(invoice, result)

    def status(
        self, invoice: CanonicalInvoice, result: IssueResult
    ) -> IssueResult:
        rows = self.command(GET_TAX_STATUS, result.provider_ref)
        row = (rows or [{}])[0] if isinstance(rows, list) else rows or {}
        tax_status = row.get("TaxStatus")
        if tax_status in TAX_REFUSED:
            raise ProviderRejectedError(
                "Cơ quan thuế không cấp mã: "
                + str(row.get("ErrorContent") or tax_status),
                raw=row,
            )
        code = str(row.get("TaxAuthorityCode") or "").strip()
        if code:
            result.status = ISSUED
            result.tax_authority_code = code
        result.raw = row
        return result

    def download(
        self, invoice: CanonicalInvoice, result: IssueResult, kind: str
    ) -> bytes:
        data = self.command(GET_PDF if kind == "pdf" else GET_XML, invoice.key)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError as e:
                raise ProviderError("eHoadon trả file không đúng dạng") from e
        content = (data or {}).get("PDF" if kind == "pdf" else "XML")
        if not content:
            raise ProviderUnavailableError("eHoadon chưa có file hóa đơn")
        return base64.b64decode(content)
