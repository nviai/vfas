"""03/TNDN XML for HTKK (form 892, version 2.9.4)."""

from datetime import date

from defusedxml import ElementTree

from app.features.org.models import Company
from app.features.vat import cit, htkk_cit
from tests.conftest import post
from tests.htkk_schema import validate

NS = {"t": "http://kekhaithue.gdt.gov.vn/TKhaiThue"}


def _v(root, tag: str) -> int:
    return int(root.find(f".//t:{tag}", NS).text)


def test_cit_xml(db, ctx, company, web, users):
    post(db, ctx, date(2026, 1, 5), "1111", "4111", 10_000_000, "PT")
    post(db, ctx, date(2026, 2, 1), "1388", "5113", 100_000_000, "PK")
    post(db, ctx, date(2026, 2, 2), "6422", "3388", 40_000_000, "PK")
    post(db, ctx, date(2026, 2, 3), "6421", "3388", 10_000_000, "PK")
    post(db, ctx, date(2026, 4, 30), "3334", "1111", 2_000_000, "PC")
    v = cit.compute(db, company.id, 2026, {"B1": 4_000_000})
    c = db.get(Company, company.id)
    data = htkk_cit.build_xml(db, c, 2026, v)
    validate(data, "03_TNDN_TT80_294.xsd")
    root = ElementTree.fromstring(data)
    assert root.find(".//t:maTKhai", NS).text == "892"
    assert root.find(".//t:kyKKhai", NS).text == "2026"
    assert _v(root, "ctA1") == 50_000_000
    assert _v(root, "ctB1") == _v(root, "ctB4") == 4_000_000
    assert _v(root, "ctB13") == _v(root, "ctC1") == 54_000_000
    assert root.find(".//t:ctC7_thueSuat", NS).text == "15"
    assert _v(root, "ctC9") == 8_100_000 == _v(root, "ctE")
    assert _v(root, "ctG2") == 2_000_000
    assert _v(root, "ctI") == 6_100_000
    app = root.find(".//t:PLuc_03_1A_TNDN", NS)
    assert int(app.find("t:ct04", NS).text) == 100_000_000
    assert int(app.find("t:ct14", NS).text) == 10_000_000  # selling
    assert int(app.find("t:ct15", NS).text) == 40_000_000  # admin
    assert int(app.find("t:ct22", NS).text) == 50_000_000
    from tests.conftest import ROLE_EMAILS

    web.login(ROLE_EMAILS["chief_accountant"])
    r = web.get("/vat/cit.xml?year=2026")
    assert r.status_code == 200 and b"<maTKhai>892</maTKhai>" in r.content
    assert "/vat/cit.xml?year=2026" in web.get("/vat/cit?year=2026").text


def test_other_rate_goes_to_c8():
    f = htkk_cit.main_values({"C6": 1000, "C7": 25})
    assert f["C7_thuNhap"] == 0 and f["C8"] == 1000 and f["C8a"] == 25
