"""VNPT Invoice adapter against a fake SOAP server."""

import base64
from datetime import date
from decimal import Decimal

import httpx
import pytest
from defusedxml import ElementTree as SafeET

from app.features.einvoice.providers.base import (
    ISSUED,
    CanonicalInvoice,
    InvoiceLine,
    ProviderConfig,
    ProviderRejectedError,
    ProviderUnavailableError,
)
from app.features.einvoice.providers.vnpt import VnptProvider

HOST = "https://0101234567-tt78democadmin.vnpt-invoice.com.vn"
KEY = "6b1f2c1e3d4a4f5b9c8d7e6f5a4b3c2d"
NS = "{http://tempuri.org/}"


def _invoice(**kw):
    data = dict(
        key=KEY,
        seller_tax_code="0101234567",
        template="1",
        series="C26TAA",
        issue_date=date(2026, 10, 14),
        lines=[
            InvoiceLine(
                1,
                "Hàng hóa A & B",
                "Cái",
                "",
                Decimal("2"),
                Decimal("1000000"),
                2_000_000,
                0,
                "10",
                200_000,
            ),
            InvoiceLine(
                2, "Sách", "Cuốn", "", None, None, 100_000, 0, "KCT", 0
            ),
        ],
        buyer_legal_name="Công ty TNHH Khách Hàng",
        buyer_tax_code="0109876543",
        amount_in_words="Hai triệu ba trăm nghìn đồng",
    )
    data.update(kw)
    return CanonicalInvoice(**data)


def _soap(op: str, result: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=(
            '<?xml version="1.0"?><soap:Envelope xmlns:soap='
            '"http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
            f'<{op}Response xmlns="http://tempuri.org/">'
            f"<{op}Result>{result}</{op}Result></{op}Response>"
            "</soap:Body></soap:Envelope>"
        ).encode(),
    )


class FakeVnpt:
    def __init__(self):
        self.calls = []
        self.issued = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        op = request.headers["soapaction"].strip('"').rsplit("/", 1)[1]
        root = SafeET.fromstring(request.content)
        params = {
            c.tag.replace(NS, ""): c.text or ""
            for c in root.find(f".//{NS}{op}")
        }
        self.calls.append((op, params))
        if op == "ImportAndPublishInv":
            if params["ACpass"] != "ac":
                return _soap(op, "ERR:1")
            inv = SafeET.fromstring(params["xmlInvData"])
            key = inv.find("Inv/key").text
            self.issued[key] = inv
            no = len(self.issued)
            return _soap(
                op, f"OK:{params['pattern']};{params['serial']}-{key}_{no}"
            )
        if op == "GetMCCQThueByFkeys":
            xml = (
                "<DSHDon><HDon><MCCQThue>00ABC123</MCCQThue>"
                "<TThai>1</TThai></HDon></DSHDon>"
            )
            return _soap(op, base64.b64encode(xml.encode()).decode())
        if op == "listInvByCusFkey":
            if params["key"] not in self.issued:
                return _soap(op, "ERR:6")
            data = (
                "&lt;Data&gt;&lt;Item&gt;&lt;invNum&gt;0000001&lt;/invNum&gt;"
                "&lt;serial&gt;C26TAA&lt;/serial&gt;&lt;/Item&gt;&lt;/Data&gt;"
            )
            return _soap(op, data)
        if op == "downloadInvPDFFkeyNoPay":
            return _soap(op, base64.b64encode(b"%PDF").decode())
        if op == "downloadInvFkeyNoPay":
            return _soap(op, "&lt;HDon/&gt;")
        return _soap(op, "ERR:3")


def _provider(fake, base=HOST, acpass="ac"):
    cfg = ProviderConfig(
        "test",
        base,
        "0101234567",
        "1",
        "C26TAA",
        {
            "account": "0101234567_admin",
            "account_password": acpass,
            "username": "0101234567service",
            "password": "pw",
            "pattern": "1/001",
        },
    )
    return VnptProvider(cfg, httpx.Client(transport=httpx.MockTransport(fake)))


def test_issue_payload_and_tax_code():
    fake = FakeVnpt()
    res = _provider(fake).issue(_invoice())
    assert res.status == ISSUED and res.number == "1"
    assert res.tax_authority_code == "00ABC123"
    op, params = fake.calls[0]
    assert op == "ImportAndPublishInv"
    assert params["pattern"] == "1/001" and params["serial"] == "C26TAA"
    inv = fake.issued[KEY].find("Inv/Invoice")
    assert inv.find("CusName").text == "Công ty TNHH Khách Hàng"
    p1, p2 = inv.findall("Products/Product")
    assert p1.find("ProdName").text == "Hàng hóa A & B"
    # Amount includes VAT, Total does not
    assert (p1.find("Total").text, p1.find("Amount").text) == (
        "2000000",
        "2200000",
    )
    assert p2.find("VATRate").text == "-1"  # KCT
    assert inv.find("GrossValue").text == "100000"
    assert inv.find("GrossValue10").text == "2000000"
    assert inv.find("Amount").text == "2300000"
    assert inv.find("ArisingDate").text == "14/10/2026"


def test_lookup_download_and_errors():
    fake = FakeVnpt()
    p = _provider(fake)
    assert p.find(_invoice()) is None
    res = p.issue(_invoice())
    found = p.find(_invoice())
    assert found.number == "1" and found.status == ISSUED
    assert p.download(_invoice(), res, "pdf") == b"%PDF"
    assert p.download(_invoice(), res, "xml") == b"<HDon/>"
    with pytest.raises(ProviderRejectedError, match="sai tài khoản"):
        _provider(FakeVnpt(), acpass="sai").issue(_invoice())
    with pytest.raises(ProviderRejectedError, match="địa chỉ web service"):
        _provider(fake, base="").issue(_invoice())

    def down(request):
        raise httpx.ReadTimeout("timeout")

    p2 = VnptProvider(
        p.config, httpx.Client(transport=httpx.MockTransport(down))
    )
    with pytest.raises(ProviderUnavailableError):
        p2.issue(_invoice())
    assert VnptProvider.supports_adjustment is False
