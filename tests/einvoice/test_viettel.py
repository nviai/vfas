"""Viettel S-Invoice adapter against a fake HTTP server.

Checks the request shape (paths, auth cookie, payload fields, tax-rate
codes, adjustment fields) and the answer handling (number parsing,
asynchronous numbering, lookup by UUID, errors, file download).
"""

import base64
import io
import json
import zipfile
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.features.einvoice.providers.base import (
    ISSUED,
    SIGNED,
    CanonicalInvoice,
    InvoiceLine,
    OriginalInvoice,
    ProviderConfig,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from app.features.einvoice.providers.viettel import ViettelProvider

BASE = "https://api-vinvoice.viettel.vn"
API = BASE + "/services/einvoiceapplication/api/"


def _invoice(**kw) -> CanonicalInvoice:
    lines = [
        InvoiceLine(
            1,
            "Hàng hóa A",
            "Cái",
            "SP001",
            Decimal("2"),
            Decimal("1000000"),
            2_000_000,
            0,
            "10",
            200_000,
        ),
        InvoiceLine(
            2, "Dịch vụ B", "", "", None, None, 500_000, 0, "8", 40_000
        ),
        InvoiceLine(3, "Sách", "Cuốn", "", None, None, 100_000, 0, "KCT", 0),
    ]
    data = dict(
        key="0b6c1f0e8a7d4c1e9d0b3f2a9c41d7e5",
        seller_tax_code="0101234567",
        template="1",
        series="C26TAA",
        issue_date=date(2026, 10, 14),
        lines=lines,
        buyer_legal_name="Công ty TNHH Khách Hàng",
        buyer_name="Nguyễn Văn A",
        buyer_tax_code="0109876543",
        buyer_address="12 Phố Huế, Hà Nội",
        amount_in_words="Hai triệu tám trăm bốn mươi nghìn đồng",
    )
    data.update(kw)
    return CanonicalInvoice(**data)


class FakeViettel:
    def __init__(self):
        self.calls = []
        self.created = {}
        self.async_numbering = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = str(request.url)
        self.calls.append(request)
        if path == BASE + "/auth/login":
            body = json.loads(request.content)
            if body["password"] != "dung":
                return httpx.Response(401, json={"error": "invalid_grant"})
            return httpx.Response(
                200, json={"access_token": "tok", "expires_in": 300}
            )
        assert request.headers["cookie"] == "access_token=tok"
        if "createInvoice/0101234567" in path:
            payload = json.loads(request.content)
            uuid = payload["generalInvoiceInfo"]["transactionUuid"]
            if payload["generalInvoiceInfo"]["invoiceSeries"] == "C26XXX":
                return httpx.Response(
                    200,
                    json={
                        "code": 400,
                        "message": "BAD_REQUEST_SERIES",
                        "data": "Ký hiệu không tồn tại",
                    },
                )
            self.created[uuid] = payload
            no = "" if self.async_numbering else f"C26TAA{len(self.created)}"
            return httpx.Response(
                200,
                json={
                    "errorCode": None,
                    "result": {
                        "invoiceNo": no,
                        "transactionID": "T1",
                        "reservationCode": "ABCD1234",
                    },
                },
            )
        if path.endswith("searchInvoiceByTransactionUuid"):
            form = dict(
                x.split("=") for x in request.content.decode().split("&")
            )
            if form["transactionUuid"] not in self.created:
                return httpx.Response(200, json={"result": []})
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "invoiceNo": "C26TAA1",
                            "reservationCode": "ABCD1234",
                            "issueDate": 1791946800000,
                            "exchangeStatus": "INVOICE_HAS_CODE_APPROVED",
                            "codeOfTax": "00A1B2C3",
                        }
                    ]
                },
            )
        if path.endswith("getInvoiceRepresentationFile"):
            body = json.loads(request.content)
            if body["fileType"] == "PDF":
                data = b"%PDF-1.4 test"
            else:
                inner = io.BytesIO()
                with zipfile.ZipFile(inner, "w") as z:
                    z.writestr("C26TAA1.xml", "<HDon/>")
                outer = io.BytesIO()
                with zipfile.ZipFile(outer, "w") as z:
                    z.writestr("inner.zip", inner.getvalue())
                data = outer.getvalue()
            return httpx.Response(
                200,
                json={"fileToBytes": base64.b64encode(data).decode()},
            )
        if path.endswith("getAllInvoiceTemplates"):
            return httpx.Response(
                200,
                json={
                    "template": [
                        {"templateCode": "1/001", "invoiceSeri": "C26TAA"}
                    ]
                },
            )
        return httpx.Response(404, json={"errorCode": "NOT_FOUND"})


@pytest.fixture()
def fake():
    return FakeViettel()


def _provider(fake, password="dung", series="C26TAA") -> ViettelProvider:
    cfg = ProviderConfig(
        environment="test",
        base_url="",
        tax_code="0101234567",
        template="1",
        series=series,
        values={
            "username": "0101234567",
            "password": password,
            "template_code": "1/001",
        },
    )
    return ViettelProvider(
        cfg, httpx.Client(transport=httpx.MockTransport(fake))
    )


def test_issue_payload_and_result(fake):
    p = _provider(fake)
    result = p.issue(_invoice())
    assert result.status == SIGNED and result.number == "1"
    assert result.lookup_code == "ABCD1234"
    payload = next(iter(fake.created.values()))
    g = payload["generalInvoiceInfo"]
    assert g["templateCode"] == "1/001" and g["invoiceSeries"] == "C26TAA"
    assert g["adjustmentType"] == "1" and g["paymentStatus"] is False
    assert g["transactionUuid"] == "0b6c1f0e8a7d4c1e9d0b3f2a9c41d7e5"
    assert payload["buyerInfo"]["buyerLegalName"] == "Công ty TNHH Khách Hàng"
    items = payload["itemInfo"]
    assert [i["taxPercentage"] for i in items] == [10, 8, -2]
    assert items[0]["unitPrice"] == 1_000_000 and items[0]["quantity"] == 2
    assert items[1]["quantity"] == 1 and items[1]["unitPrice"] == 500_000
    assert sorted(
        (b["taxPercentage"], b["taxableAmount"], b["taxAmount"])
        for b in payload["taxBreakdowns"]
    ) == [(-2, 100_000, 0), (8, 500_000, 40_000), (10, 2_000_000, 200_000)]
    s = payload["summarizeInfo"]
    assert (
        s["totalAmountWithTax"] == 2_840_000 and s["totalTaxAmount"] == 240_000
    )
    assert payload["payments"][0]["paymentMethodName"] == "TM/CK"
    # One login for several calls
    p.find(_invoice())
    logins = [c for c in fake.calls if str(c.url).endswith("/auth/login")]
    assert len(logins) == 1


def test_lookup_and_tax_code(fake):
    p = _provider(fake)
    assert p.find(_invoice()) is None
    p.issue(_invoice())
    found = p.find(_invoice())
    assert found.status == ISSUED and found.tax_authority_code == "00A1B2C3"
    assert found.number == "1" and found.issue_date == date(2026, 10, 14)


def test_async_numbering_is_unknown(fake):
    fake.async_numbering = True
    with pytest.raises(ProviderUnavailableError):
        _provider(fake).issue(_invoice())


def test_errors(fake):
    with pytest.raises(ProviderRejectedError, match="Ký hiệu không tồn tại"):
        _provider(fake).issue(_invoice(series="C26XXX"))
    with pytest.raises(ProviderRejectedError, match="Đăng nhập"):
        _provider(fake, password="sai").issue(_invoice())

    def down(request):
        raise httpx.ConnectTimeout("timeout")

    p = ViettelProvider(
        _provider(fake).config,
        httpx.Client(transport=httpx.MockTransport(down)),
    )
    with pytest.raises(ProviderUnavailableError):
        p.issue(_invoice())


def test_adjustment_payload(fake):
    p = _provider(fake)
    inv = _invoice(
        kind="adjust_down",
        original=OriginalInvoice("1", "C26TAA", "7", date(2026, 10, 1)),
        reason="Giảm giá theo biên bản",
    )
    p.issue(inv)
    payload = next(iter(fake.created.values()))
    g = payload["generalInvoiceInfo"]
    assert g["adjustmentType"] == "5" and g["adjustmentInvoiceType"] == "1"
    assert g["originalInvoiceId"] == "C26TAA7"
    assert g["originalTemplateCode"] == "1/001"
    # Noon of 01/10/2026 in Vietnam time
    assert g["originalInvoiceIssueDate"] == 1790830800000
    assert all(i["isIncreaseItem"] is False for i in payload["itemInfo"])
    assert payload["summarizeInfo"]["isTotalAmountPos"] is False


def test_download_and_connection(fake):
    p = _provider(fake)
    res = p.issue(_invoice())
    assert p.download(_invoice(), res, "pdf").startswith(b"%PDF")
    assert p.download(_invoice(), res, "xml") == b"<HDon/>"
    assert "thành công" in p.test_connection()
    with pytest.raises(ProviderRejectedError, match="Không thấy mẫu"):
        _provider(fake, series="C26TBB").test_connection()
