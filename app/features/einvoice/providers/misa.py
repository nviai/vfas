"""MISA meInvoice adapter – REST/JSON "API-CLOUD" v3, HSM signing.

Follows MISA's published documentation (doc.meinvoice.vn/api):

- token: POST /auth/token {appid, taxcode, username, password}; the
  JWT is in `Data`; calls send `Authorization: Bearer` and
  `CompanyTaxCode`;
- invoices with a tax authority code use the `/code/...` paths
  (series starting with C), the others the plain paths (K);
- issue: POST .../itg/invoicepublishing/publishhsm with a list of
  {RefID, OriginalInvoiceData, ...}; `RefID` is MISA's duplicate
  guard; `Data` in the answer is a JSON string to parse again and
  every item carries its own ErrorCode;
- lookup by RefID: .../invoicepublished/invoice-status/refid;
  status (tax authority code) by TransactionID:
  .../itg/invoicepublished/invoicestatus;
- files: .../itg/invoicepublished/downloadinvoice?downloadDataType=.

Adjustment invoices are not offered: MISA documents the reference
fields but not how a decrease is expressed (see docs/EINVOICE.md).
"""

from __future__ import annotations

import base64
import json
import time
import uuid

from app.features.einvoice.providers.base import (
    ISSUED,
    SIGNED,
    CanonicalInvoice,
    ConfigField,
    EInvoiceProvider,
    IssueResult,
    ProviderRejectedError,
    ProviderUnavailableError,
    vat_rate_label,
)

AUTH_ERRORS = ("TokenExpiredCode", "InvalidTokenCode", "UnAuthorize")
# SendTaxStatus of invoices with a code: 2 = code assigned,
# 3 = refused.
TAX_CODE_ASSIGNED = 2
TAX_CODE_REFUSED = 3


def _guid(key: str) -> str:
    """MISA recommends GUIDs as RefID."""
    try:
        return str(uuid.UUID(key))
    except ValueError:
        return key


def _parse(data):
    """`Data` is often JSON encoded inside a string."""
    if isinstance(data, str) and data.strip()[:1] in ("[", "{"):
        try:
            return json.loads(data)
        except ValueError:
            return data
    return data


def _items(data) -> list[dict]:
    data = _parse(data)
    if isinstance(data, dict):
        return [data]
    return [x for x in data or [] if isinstance(x, dict)]


class MisaProvider(EInvoiceProvider):
    code = "misa"
    name = "MISA meInvoice"
    fields = (
        ConfigField(
            "appid",
            "AppID do MISA cấp",
            secret=True,
            hint="Mã ứng dụng tích hợp MISA cấp cho phần mềm",
        ),
        ConfigField("username", "Tài khoản meInvoice"),
        ConfigField("password", "Mật khẩu meInvoice", secret=True),
    )
    default_urls = {
        "test": "https://testapi.meinvoice.vn/api/v3",
        "production": "https://api.meinvoice.vn/api/v3",
    }
    supports_adjustment = False
    timeout = 90.0

    _token: str | None = None
    _token_until: float = 0.0

    # ------------------------------------------------------------ auth

    def _login(self, force: bool = False) -> str:
        if self._token and not force and time.monotonic() < self._token_until:
            return self._token
        resp = self._request(
            "POST",
            f"{self.base_url}/auth/token",
            json={
                "appid": self.value("appid"),
                "taxcode": self.config.tax_code,
                "username": self.value("username"),
                "password": self.value("password"),
            },
        )
        body = self._json(resp)
        token = body.get("Data")
        if not body.get("Success") or not isinstance(token, str) or not token:
            raise ProviderRejectedError(
                "Đăng nhập meInvoice thất bại: "
                + str(body.get("ErrorCode") or body.get("Errors") or "")
            )
        self._token = token
        # Lifetime is documented as 14–15 days; refresh daily.
        self._token_until = time.monotonic() + 23 * 3600
        return token

    def _prefix(self) -> str:
        # Second character of the series: C = with a tax authority code.
        return "/code" if self.config.series[:1] == "C" else ""

    def _post(self, path: str, payload, *, params=None) -> dict:
        for attempt in (0, 1):
            token = self._login(force=attempt == 1)
            resp = self._request(
                "POST",
                f"{self.base_url}{path}",
                json=payload,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "CompanyTaxCode": self.config.tax_code,
                },
            )
            body = self._json(resp)
            code = body.get("ErrorCode") or ""
            if code in AUTH_ERRORS and attempt == 0:
                continue
            if resp.status_code >= 400 or not body.get("Success") or code:
                detail = code or body.get("Errors") or resp.status_code
                raise ProviderRejectedError(
                    f"meInvoice: {detail}",
                    code=str(code),
                    raw=body,
                )
            return body
        raise ProviderRejectedError("meInvoice: phiên đăng nhập hết hạn")

    @staticmethod
    def _json(resp) -> dict:
        try:
            data = resp.json()
        except ValueError as e:
            raise ProviderUnavailableError(
                "meInvoice trả về dữ liệu không đọc được",
                raw=resp.text[:2000],
            ) from e
        return data if isinstance(data, dict) else {"Data": data}

    # ---------------------------------------------------------- payload

    def invoice_data(self, inv: CanonicalInvoice) -> dict:
        details = []
        for n, ln in enumerate(inv.lines, start=1):
            qty = float(ln.quantity) if ln.quantity is not None else 1.0
            price = (
                float(ln.unit_price)
                if ln.unit_price is not None
                else float(ln.gross)
            )
            details.append(
                {
                    "ItemType": 1,
                    "LineNumber": n,
                    "SortOrder": n,
                    "ItemCode": ln.code,
                    "ItemName": ln.name,
                    "UnitName": ln.unit,
                    "Quantity": qty,
                    "UnitPrice": price,
                    "DiscountRate": 0.0,
                    "DiscountAmountOC": float(ln.discount),
                    "DiscountAmount": float(ln.discount),
                    "AmountOC": float(ln.gross),
                    "Amount": float(ln.gross),
                    "AmountWithoutVATOC": float(ln.amount),
                    "AmountWithoutVAT": float(ln.amount),
                    "VATRateName": vat_rate_label(ln.vat_rate),
                    "VATAmountOC": float(ln.vat_amount),
                    "VATAmount": float(ln.vat_amount),
                }
            )
        sale = float(inv.total_before_tax + inv.total_discount)
        data = {
            "RefID": _guid(inv.key),
            "InvSeries": f"{inv.template}{inv.series}",
            "InvoiceName": "Hóa đơn giá trị gia tăng"
            if inv.template == "1"
            else "Hóa đơn bán hàng",
            "InvDate": inv.issue_date.isoformat() + "T00:00:00+07:00",
            "CurrencyCode": inv.currency,
            "ExchangeRate": 1.0,
            "PaymentMethodName": inv.payment_method,
            "IsInvoiceSummary": False,
            "BuyerLegalName": inv.buyer_legal_name,
            "BuyerTaxCode": inv.buyer_tax_code,
            "BuyerAddress": inv.buyer_address,
            "BuyerCode": inv.buyer_code,
            "BuyerFullName": inv.buyer_name,
            "BuyerEmail": inv.buyer_email,
            "TotalSaleAmountOC": sale,
            "TotalSaleAmount": sale,
            "TotalDiscountAmountOC": float(inv.total_discount),
            "TotalDiscountAmount": float(inv.total_discount),
            "TotalAmountWithoutVATOC": float(inv.total_before_tax),
            "TotalAmountWithoutVAT": float(inv.total_before_tax),
            "TotalVATAmountOC": float(inv.total_vat),
            "TotalVATAmount": float(inv.total_vat),
            "TotalAmountOC": float(inv.total),
            "TotalAmount": float(inv.total),
            "TotalAmountInWords": inv.amount_in_words,
            "OriginalInvoiceDetail": details,
            "TaxRateInfo": [
                {
                    "VATRateName": vat_rate_label(g.vat_rate),
                    "AmountWithoutVATOC": float(g.base),
                    "VATAmountOC": float(g.vat),
                }
                for g in inv.tax_groups
            ],
            "OptionUserDefined": {
                "MainCurrency": "VND",
                "AmountDecimalDigits": "0",
                "AmountOCDecimalDigits": "0",
                "UnitPriceOCDecimalDigits": "2",
                "UnitPriceDecimalDigits": "2",
                "QuantityDecimalDigits": "3",
                "CoefficientDecimalDigits": "2",
                "ExchangRateDecimalDigits": "0",
            },
        }
        if inv.note:
            data["InvoiceNote"] = inv.note[:255]
        if any(ln.vat_rate == "8" for ln in inv.lines):
            # MISA's validation requires this flag on invoices with the
            # reduced 8% rate.
            data["IsTaxReduction43"] = True
        return data

    # ---------------------------------------------------------- results

    def _result(self, inv: CanonicalInvoice, item: dict) -> IssueResult:
        number = str(item.get("InvNo") or "").lstrip("0") or ""
        code = (item.get("InvoiceCode") or item.get("InvCode") or "").strip()
        return IssueResult(
            status=ISSUED if code else SIGNED,
            number=number,
            template=inv.template,
            series=inv.series,
            issue_date=inv.issue_date,
            provider_ref=str(item.get("TransactionID") or ""),
            lookup_code=str(item.get("TransactionID") or ""),
            tax_authority_code=code,
            raw=item,
        )

    # -------------------------------------------------------------- API

    def test_connection(self) -> str:
        self._login(force=True)
        return "Kết nối meInvoice thành công"

    def issue(self, invoice: CanonicalInvoice) -> IssueResult:
        body = self._post(
            f"{self._prefix()}/itg/invoicepublishing/publishhsm",
            [
                {
                    "RefID": _guid(invoice.key),
                    "OriginalInvoiceData": self.invoice_data(invoice),
                    "IsSendEmail": False,
                    "ReceiverName": "",
                    "ReceiverEmail": "",
                    "IsInvoiceSummary": False,
                }
            ],
        )
        items = _items(body.get("Data"))
        if not items:
            raise ProviderUnavailableError(
                "meInvoice không trả kết quả phát hành", raw=body
            )
        item = items[0]
        error = item.get("ErrorCode") or ""
        if error == "DuplicateInvoiceRefID":
            found = self.find(invoice)
            if found is None:
                raise ProviderUnavailableError(
                    "meInvoice báo trùng RefID, chưa tra được hóa đơn"
                )
            return found
        if error and error != "InvoiceDuplicated":
            raise ProviderRejectedError(
                f"meInvoice: {error}", code=error, raw=item
            )
        if not item.get("InvNo"):
            raise ProviderUnavailableError(
                "meInvoice chưa trả số hóa đơn, sẽ tra cứu lại", raw=item
            )
        return self._result(invoice, item)

    def find(self, invoice: CanonicalInvoice) -> IssueResult | None:
        body = self._post(
            f"{self._prefix()}/invoicepublished/invoice-status/refid",
            [_guid(invoice.key)],
        )
        items = [i for i in _items(body.get("Data")) if i.get("InvNo")]
        return self._result(invoice, items[0]) if items else None

    def status(
        self, invoice: CanonicalInvoice, result: IssueResult
    ) -> IssueResult:
        if not result.provider_ref:
            return self.find(invoice) or result
        body = self._post(
            f"{self._prefix()}/itg/invoicepublished/invoicestatus",
            [result.provider_ref],
        )
        items = _items(body.get("Data"))
        if not items:
            return result
        item = items[0]
        sent = item.get("SendTaxStatus")
        if self._prefix() and sent == TAX_CODE_REFUSED:
            raise ProviderRejectedError(
                "Cơ quan thuế từ chối cấp mã hóa đơn", raw=item
            )
        code = (item.get("InvoiceCode") or "").strip()
        if code or (not self._prefix() and sent == TAX_CODE_ASSIGNED):
            result.status = ISSUED
            result.tax_authority_code = code
        result.raw = item
        return result

    def download(
        self, invoice: CanonicalInvoice, result: IssueResult, kind: str
    ) -> bytes:
        body = self._post(
            f"{self._prefix()}/itg/invoicepublished/downloadinvoice",
            [result.provider_ref],
            params={"downloadDataType": "PDF" if kind == "pdf" else "XML"},
        )
        items = _items(body.get("Data"))
        content = (
            items[0].get("Data") or items[0].get("data") if items else None
        )
        if not content:
            raise ProviderUnavailableError("meInvoice chưa có file hóa đơn")
        if kind == "xml":
            return content.encode("utf-8")
        return base64.b64decode(content)
