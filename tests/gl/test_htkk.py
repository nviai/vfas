"""Financial statements XML for HTKK (form 683, version 2.3.2)."""

from defusedxml import ElementTree

from app.features.gl import htkk, periods, statements
from app.features.org.models import Company
from tests.htkk_schema import validate

NS = {"t": "http://kekhaithue.gdt.gov.vn/TKhaiThue"}


def _value(node, tag: str) -> int:
    return int(node.find(f"t:{tag}", NS).text)


def test_bctc_xml_matches_statements(db, company, year_book):
    c = db.get(Company, company.id)
    data = htkk.build_xml(db, c, 2024)
    validate(data, "133_B01A_DNN_BCTC_232.xsd")
    root = ElementTree.fromstring(data)
    assert root.find(".//t:maTKhai", NS).text == "683"
    assert root.find(".//t:kieuKy", NS).text == "D"
    assert root.find(".//t:kyKKhaiDenNgay", NS).text == "31/12/2024"
    period = periods.make("year", 2024)
    b01 = statements.b01a(db, c, period)
    cur = root.find(".//t:CTieuTKhaiChinh/t:SoCuoiNam", NS)
    assert len(cur) == 47
    assert _value(cur, "ct200") == b01.value("200") == _value(cur, "ct500")
    assert _value(cur, "ct152") == b01.value("152") <= 0
    b02 = statements.b02(db, c, period)
    pl = root.find(".//t:PL_KQHDSXKD/t:NamNay", NS)
    assert _value(pl, "ct60") == b02.value("60")
    cf = root.find(".//t:PL_LCTTTT/t:NamNay", NS)
    assert len(cf) == 24
    assert _value(cf, "ct70") == statements.b03(db, c, period).value("70")
    # F01: 123 accounts + total on each side, debits = credits
    f01 = root.find(".//t:PL_CDTK", NS)
    for block in ("SoDuDauKy", "SoPhatSinhTrongKy", "SoDuCuoiKy"):
        no = f01.find(f"t:{block}/t:No", NS)
        co = f01.find(f"t:{block}/t:Co", NS)
        assert len(no) == len(co) == 124
        assert _value(no, "tongCong") == _value(co, "tongCong")
    closing = f01.find("t:SoDuCuoiKy/t:No", NS)
    assert _value(closing, "ct111") == _value(closing, "ct1111") + _value(
        closing, "ct1112"
    )
    assert htkk.unmapped_accounts(db, c, 2024) == []


def test_bctc_xml_route(web, users, year_book):
    from tests.conftest import ROLE_EMAILS

    web.login(ROLE_EMAILS["chief_accountant"])
    r = web.get("/reports/bctc.xml?year=2024")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert b"<maTKhai>683</maTKhai>" in r.content
    assert (
        "/reports/bctc.xml?year="
        in web.get("/reports?mode=year&year=2024").text
    )
