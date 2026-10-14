"""MISA meInvoice and BKAV eHoadon adapters against fake servers."""

import base64
import gzip
import json
import os
from datetime import date
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from defusedxml import ElementTree as SafeET

from app.features.einvoice.providers import bkav
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
from app.features.einvoice.providers.misa import MisaProvider

KEY = "6b1f2c1e3d4a4f5b9c8d7e6f5a4b3c2d"


def _invoice(**kw) -> CanonicalInvoice:
    data = dict(
        key=KEY,
        seller_tax_code="0101234567",
        template="1",
        series="C26TAA",
        issue_date=date(2026, 10, 14),
        lines=[
            InvoiceLine(
                1,
                "Hàng hóa A",
                "Cái",
                "SP01",
                Decimal("2"),
                Decimal("1000000"),
                2_000_000,
                0,
                "10",
                200_000,
            ),
            InvoiceLine(
                2,
                "Hàng hóa B",
                "Cái",
                "SP02",
                None,
                None,
                500_000,
                0,
                "8",
                40_000,
            ),
        ],
        buyer_legal_name="Công ty cổ phần MISA",
        buyer_tax_code="0101243150",
        buyer_address="Cầu Giấy, Hà Nội",
        amount_in_words="Hai triệu bảy trăm bốn mươi nghìn đồng",
    )
    data.update(kw)
    return CanonicalInvoice(**data)


def _config(values, series="C26TAA") -> ProviderConfig:
    return ProviderConfig("test", "", "0101234567", "1", series, values)


# ----------------------------------------------------------------- MISA


class FakeMisa:
    BASE = "https://testapi.meinvoice.vn/api/v3"

    def __init__(self):
        self.published = {}
        self.token_calls = 0
        self.expire_once = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == self.BASE + "/auth/token":
            self.token_calls += 1
            body = json.loads(request.content)
            ok = body["appid"] == "APP" and body["password"] == "pw"
            return httpx.Response(
                200,
                json={
                    "Success": ok,
                    "ErrorCode": None if ok else "UnAuthorize",
                    "Data": f"jwt{self.token_calls}" if ok else None,
                },
            )
        assert request.headers["companytaxcode"] == "0101234567"
        if self.expire_once:
            self.expire_once = False
            return httpx.Response(
                200, json={"Success": False, "ErrorCode": "TokenExpiredCode"}
            )
        body = json.loads(request.content)
        if url.endswith("/code/itg/invoicepublishing/publishhsm"):
            item = body[0]
            data = item["OriginalInvoiceData"]
            ref = item["RefID"]
            if data["InvSeries"] != "1C26TAA":
                result = [{"RefID": ref, "ErrorCode": "InvalidInvoiceData"}]
            elif ref in self.published:
                result = [{"RefID": ref, "ErrorCode": "DuplicateInvoiceRefID"}]
            else:
                self.published[ref] = data
                result = [
                    {
                        "RefID": ref,
                        "TransactionID": "QWIGTN63E_",
                        "InvSeries": "C26TAA",
                        "InvNo": "00000321",
                        "ErrorCode": "",
                    }
                ]
            return httpx.Response(
                200, json={"Success": True, "Data": json.dumps(result)}
            )
        if url.endswith("/code/invoicepublished/invoice-status/refid"):
            rows = [
                {
                    "RefID": r,
                    "TransactionID": "QWIGTN63E_",
                    "InvNo": "00000321",
                }
                for r in body
                if r in self.published
            ]
            return httpx.Response(
                200, json={"Success": True, "Data": json.dumps(rows)}
            )
        if url.endswith("/code/itg/invoicepublished/invoicestatus"):
            row = {
                "TransactionID": body[0],
                "SendTaxStatus": 2,
                "InvoiceCode": "21B02D9C84A1BC4019A8BFC1AB2F686709",
            }
            return httpx.Response(
                200, json={"Success": True, "Data": json.dumps([row])}
            )
        if "/code/itg/invoicepublished/downloadinvoice" in url:
            kind = request.url.params["downloadDataType"]
            content = (
                "<HDon/>"
                if kind == "XML"
                else base64.b64encode(b"%PDF-1.4").decode()
            )
            return httpx.Response(
                200,
                json={
                    "Success": True,
                    "Data": [{"TransactionID": body[0], "Data": content}],
                },
            )
        return httpx.Response(404)


def _misa(fake, **values) -> MisaProvider:
    v = {"appid": "APP", "username": "u", "password": "pw"} | values
    return MisaProvider(
        _config(v), httpx.Client(transport=httpx.MockTransport(fake))
    )


def test_misa_issue_status_download():
    fake = FakeMisa()
    p = _misa(fake)
    res = p.issue(_invoice())
    assert res.status == SIGNED and res.number == "321"
    assert res.provider_ref == "QWIGTN63E_"
    data = fake.published["6b1f2c1e-3d4a-4f5b-9c8d-7e6f5a4b3c2d"]
    assert data["InvSeries"] == "1C26TAA" and data["IsTaxReduction43"] is True
    assert (
        data["TotalAmount"] == 2_740_000 and data["TotalVATAmount"] == 240_000
    )
    assert [d["VATRateName"] for d in data["OriginalInvoiceDetail"]] == [
        "10%",
        "8%",
    ]
    assert {t["VATRateName"] for t in data["TaxRateInfo"]} == {"10%", "8%"}
    res = p.status(_invoice(), res)
    assert res.status == ISSUED and res.tax_authority_code.startswith("21B0")
    assert p.download(_invoice(), res, "xml") == b"<HDon/>"
    assert p.download(_invoice(), res, "pdf") == b"%PDF-1.4"
    # A repeated RefID returns the existing invoice
    again = p.issue(_invoice())
    assert again.number == "321"
    assert p.find(_invoice(key="00000000000000000000000000000000")) is None


def test_misa_errors_and_token_refresh():
    fake = FakeMisa()
    with pytest.raises(ProviderRejectedError, match="InvalidInvoiceData"):
        _misa(fake).issue(_invoice(template="2"))
    with pytest.raises(ProviderRejectedError, match="Đăng nhập"):
        _misa(fake, password="sai").issue(_invoice())
    p = _misa(fake)
    p.issue(_invoice())
    fake.expire_once = True
    assert p.find(_invoice()).number == "321"  # logged in again
    assert MisaProvider.supports_adjustment is False


# ----------------------------------------------------------------- BKAV

TOKEN = (
    base64.b64encode(os.urandom(32)).decode()
    + ":"
    + base64.b64encode(os.urandom(16)).decode()
)


def _decode_command(text: str) -> dict:
    key, iv = bkav.parse_token(TOKEN)
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    raw = dec.update(base64.b64decode(text)) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    data = unpadder.update(raw) + unpadder.finalize()
    return json.loads(gzip.decompress(data))


class FakeBkav:
    URL = "https://wsdemo.ehoadon.vn/WSPublicEHoaDon.asmx"

    def __init__(self):
        self.invoices = {}
        self.commands = []
        self.refuse_sign = False

    def reply(self, body) -> httpx.Response:
        """Encrypt `body` the way eHoadon does and wrap it in SOAP."""
        key, iv = bkav.parse_token(TOKEN)
        padder = padding.PKCS7(128).padder()
        data = (
            padder.update(gzip.compress(json.dumps(body).encode()))
            + padder.finalize()
        )
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        text = base64.b64encode(enc.update(data) + enc.finalize()).decode()
        return httpx.Response(
            200,
            content=(
                '<?xml version="1.0"?><soap:Envelope xmlns:soap='
                '"http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
                '<ExecCommandResponse xmlns="http://tempuri.org/">'
                f"<ExecCommandResult>{text}</ExecCommandResult>"
                "</ExecCommandResponse></soap:Body></soap:Envelope>"
            ).encode(),
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == self.URL
        assert (
            request.headers["soapaction"] == '"http://tempuri.org/ExecCommand"'
        )
        root = SafeET.fromstring(request.content)
        ns = "{http://tempuri.org/}"
        assert root.find(f".//{ns}partnerGUID").text == "GUID-1"
        cmd = _decode_command(root.find(f".//{ns}CommandData").text)
        self.commands.append(cmd)
        t, obj = cmd["CmdType"], cmd["CommandObject"]
        if t in (112, 124):
            ws = obj[0]
            key = ws["PartnerInvoiceStringID"]
            if key in self.invoices:
                item = {
                    "Status": 1,
                    "MessLog": "Đã tồn tại Hóa đơn có "
                    f"PartnerInvoiceStringID: {key}",
                }
            else:
                no = len(self.invoices) + 1
                self.invoices[key] = {
                    "InvoiceGUID": f"guid-{no}",
                    "InvoiceNo": no,
                    "InvoiceStatusID": 11,
                    "ws": ws,
                }
                item = {
                    "Status": 0,
                    "InvoiceGUID": f"guid-{no}",
                    "InvoiceNo": no,
                    "MTC": "RTO9YKG38",
                    "MessLog": "",
                }
            return self.reply({"Status": 0, "Object": json.dumps([item])})
        if t == 205:
            if self.refuse_sign:
                return self.reply({"Status": 1, "Object": "HSM lỗi"})
            for inv in self.invoices.values():
                if inv["InvoiceGUID"] == obj:
                    inv["InvoiceStatusID"] = 2
            return self.reply({"Status": 0, "Object": ""})
        if t == 850:
            signed = any(
                i["InvoiceGUID"] == obj and i["InvoiceStatusID"] == 2
                for i in self.invoices.values()
            )
            row = {
                "BkavStatus": 2,
                "TaxStatus": 33 if signed else 36,
                "TaxAuthorityCode": "00968F6CEAA91E4B74AA0FEFF255D701A6"
                if signed
                else "",
            }
            return self.reply({"Status": 0, "Object": [row]})
        if t == 800:
            inv = self.invoices.get(obj)
            if inv is None:
                return self.reply(
                    {"Status": 1, "Object": "Hóa đơn không tồn tại"}
                )
            data = {
                "Invoice": {
                    "InvoiceGUID": inv["InvoiceGUID"],
                    "InvoiceNo": inv["InvoiceNo"],
                    "InvoiceStatusID": inv["InvoiceStatusID"],
                    "InvoiceCode": "RTO9YKG38",
                }
            }
            return self.reply({"Status": 0, "Object": json.dumps(data)})
        if t in (808, 809):
            data = {
                "PDF": base64.b64encode(b"%PDF").decode()
                if t == 808
                else None,
                "XML": base64.b64encode(b"<HDon/>").decode()
                if t == 809
                else None,
            }
            return self.reply({"Status": 0, "Object": json.dumps(data)})
        if t == 904:
            return self.reply({"Status": 0, "Object": {"MaSoThue": obj}})
        return self.reply({"Status": 1, "Object": "CommandType is not valid"})


def _bkav(fake) -> bkav.BkavProvider:
    return bkav.BkavProvider(
        _config({"partner_guid": "GUID-1", "partner_token": TOKEN}),
        httpx.Client(transport=httpx.MockTransport(fake)),
    )


def test_bkav_codec_round_trip():
    text = bkav.encode_command(112, [{"a": "Tiếng Việt"}], TOKEN)
    assert _decode_command(text) == {
        "CmdType": 112,
        "CommandObject": [{"a": "Tiếng Việt"}],
    }
    assert bkav.decode_result("[MessageForUser] lỗi", TOKEN) == (
        "[MessageForUser] lỗi"
    )
    with pytest.raises(ProviderRejectedError):
        bkav.parse_token("khong-hop-le")


def test_bkav_issue_sign_and_status():
    fake = FakeBkav()
    p = _bkav(fake)
    res = p.issue(_invoice())
    assert res.status == ISSUED and res.number == "1"
    assert res.tax_authority_code.startswith("00968F")
    assert [c["CmdType"] for c in fake.commands] == [112, 205, 850]
    ws = fake.commands[0]["CommandObject"][0]
    assert ws["PartnerInvoiceStringID"] == KEY
    assert ws["Invoice"]["InvoiceSerial"] == "C26TAA"
    rates = [
        (d["TaxRateID"], d["TaxRate"]) for d in ws["ListInvoiceDetailsWS"]
    ]
    assert rates == [(3, 10.0), (9, 8.0)]
    # Repeated key: the existing invoice is found, not created again
    assert p.issue(_invoice()).number == "1"
    assert len(fake.invoices) == 1
    assert p.download(_invoice(), res, "pdf") == b"%PDF"
    assert p.download(_invoice(), res, "xml") == b"<HDon/>"
    assert "thành công" in p.test_connection()


def test_bkav_unsigned_is_retried_by_lookup():
    fake = FakeBkav()
    fake.refuse_sign = True
    p = _bkav(fake)
    with pytest.raises(ProviderUnavailableError, match="HSM"):
        p.issue(_invoice())
    fake.refuse_sign = False
    found = p.find(_invoice())
    assert found.status == ISSUED and found.number == "1"
    assert p.find(_invoice(key="khac")) is None


def test_bkav_adjustment():
    fake = FakeBkav()
    p = _bkav(fake)
    inv = _invoice(
        kind="adjust_down",
        original=OriginalInvoice("1", "C26TAA", "7", date(2026, 10, 1)),
        reason="Trả lại hàng",
    )
    p.issue(inv)
    cmd = fake.commands[0]
    assert cmd["CmdType"] == 124
    ws = cmd["CommandObject"][0]
    assert ws["Invoice"]["OriginalInvoiceIdentify"] == "[1]_[C26TAA]_[0000007]"
    details = ws["ListInvoiceDetailsWS"]
    assert details[0]["ItemTypeID"] == 4
    assert all(d["IsIncrease"] is False for d in details[1:])
