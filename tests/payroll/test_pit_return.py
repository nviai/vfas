"""05/KK-TNCN: return of PIT withheld from salaries (HTKK form 864)."""

from datetime import date

import pytest
from defusedxml import ElementTree

from app.core.errors import BusinessError
from app.core.registry import ACCOUNTANT, CHIEF, VIEWER
from app.features.org.models import Company
from app.features.payroll import htkk_kk, pit_return, service
from app.features.vat.service import TaxPeriod
from tests.conftest import ROLE_EMAILS
from tests.htkk_schema import validate
from tests.payroll.test_payroll import staff  # noqa: F401 - fixture

NS = {"t": "http://kekhaithue.gdt.gov.vn/TKhaiThue"}
Q1 = TaxPeriod("quarter", 2026, 1)


def test_periods(company):
    c = company
    c.vat_period = "month"
    # Monthly filers keep monthly periods until 03/2026 ...
    assert pit_return.period_of(c, date(2026, 3, 15)) == TaxPeriod(
        "month", 2026, 3
    )
    assert pit_return.parse_period("month", 2026, 3).period == 3
    # ... then every employer files quarterly.
    assert pit_return.period_of(c, date(2026, 5, 2)) == TaxPeriod(
        "quarter", 2026, 2
    )
    with pytest.raises(BusinessError):
        pit_return.parse_period("month", 2026, 4)
    q2 = TaxPeriod("quarter", 2026, 2)
    assert pit_return.previous(c, q2) == TaxPeriod("month", 2026, 3)
    assert pit_return.following(c, TaxPeriod("month", 2026, 3)) == q2
    c.vat_period = "quarter"
    assert pit_return.previous(c, q2) == Q1
    assert pit_return.due_date(TaxPeriod("month", 2026, 3)) == date(
        2026, 4, 20
    )
    assert pit_return.due_date(TaxPeriod("quarter", 2026, 3)) == date(
        2026, 10, 31
    )


def test_values_and_xml(db, ctxs, staff, company):  # noqa: F811
    ctx = ctxs[ACCOUNTANT]
    for month in (1, 2):
        service.post_run(db, ctx, service.create_run(db, ctx, 2026, month))
    rows = pit_return.collect(db, company.id, Q1)
    v = pit_return.compute(rows)
    by_name = {r.employee.full_name: r for r in rows}
    an, binh, vu = (
        by_name["Nguyễn Văn An"],
        by_name["Trần Thị Bình"],
        by_name["Lê Thời Vụ"],
    )
    assert (an.months, binh.months, vu.months) == (2, 2, 1)
    assert an.pit == 2 * 257_500 and vu.pit == 400_000 and binh.pit == 0
    assert v["16"] == 3 and v["17"] == 2
    assert v["18"] == v["19"] == 2 and v["20"] == 0
    assert v["21"] == v["22"] == sum(r.taxable_income for r in rows)
    # Only the income of people with tax withheld goes to [26].
    assert v["26"] == an.taxable_income + vu.taxable_income
    assert v["29"] == v["30"] == 915_000 and v["31"] == 0

    c = db.get(Company, company.id)
    data = htkk_kk.build_xml(c, Q1, v, signed_on=date(2026, 4, 20))
    validate(data, "05_KK_TNCN_TT80_293.xsd")
    root = ElementTree.fromstring(data)
    assert root.find(".//t:maTKhai", NS).text == "864"
    assert root.find(".//t:pbanTKhaiXML", NS).text == "2.9.3"
    assert root.find(".//t:kieuKy", NS).text == "Q"
    assert root.find(".//t:kyKKhai", NS).text == "1/2026"
    assert root.find(".//t:loaiTKhai", NS).text == "C"
    main = root.find(".//t:CTieuTKhaiChinh", NS)
    assert int(main.find("t:ct16", NS).text) == 3
    assert int(main.find("t:ct29", NS).text) == 915_000
    assert main.find("t:ct25_1", NS).text == "0"


def test_finalize_and_reopen(db, ctxs, staff, company):  # noqa: F811
    ctx = ctxs[ACCOUNTANT]
    run = service.create_run(db, ctx, 2026, 1)
    # A run of the period that is not posted blocks finalizing.
    with pytest.raises(BusinessError, match="01/2026"):
        pit_return.finalize(db, ctx, Q1)
    service.post_run(db, ctx, run)
    decl = pit_return.finalize(db, ctx, Q1)
    assert decl.status == "final" and decl.values["29"] == 657_500
    with pytest.raises(BusinessError):
        pit_return.finalize(db, ctx, Q1)
    # Later payroll changes do not alter the finalized figures.
    service.post_run(db, ctx, service.create_run(db, ctx, 2026, 2))
    stored = pit_return.values_of(db, company.id, Q1, decl)
    assert stored["29"] == 657_500
    assert pit_return.live_values(db, company.id, Q1)["29"] == 915_000
    pit_return.reopen(db, ctx, Q1)
    assert pit_return.get_declaration(db, company.id, Q1) is None


def test_web(db, ctxs, staff, company, web, users):  # noqa: F811
    ctx = ctxs[ACCOUNTANT]
    service.post_run(db, ctx, service.create_run(db, ctx, 2026, 1))
    web.login(ROLE_EMAILS[CHIEF])
    page = web.get("/payroll/pit-returns")
    assert page.status_code == 200 and "05/KK-TNCN" in page.text
    url = "/payroll/pit-returns/quarter/2026/1"
    page = web.get(url).text
    assert "Nguyễn Văn An" in page and "657.500" in page
    r = web.get(url + ".xml")
    assert r.status_code == 200 and b"<maTKhai>864</maTKhai>" in r.content
    r = web.post(url + "/final", {}, follow_redirects=False)
    assert r.status_code == 303
    assert "Đã chốt" in web.get(url).text
    # Monthly periods ended with 03/2026: the quarter opens instead.
    r = web.get("/payroll/pit-returns/month/2026/5", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/payroll/pit-returns/quarter/2026/2"
    web.login(ROLE_EMAILS[VIEWER])
    assert web.get(url).status_code == 403
    assert web.get(url + ".xml").status_code == 403


def test_calendar(db, ctxs, staff, company):  # noqa: F811
    from app.features.vat.calendar import deadlines

    c = db.get(Company, company.id)
    c.vat_period = "month"
    items = deadlines(c, date(2026, 10, 14), db)
    pit = [i for i in items if i["kind"] == "pit"]
    # Quarterly even for a monthly VAT filer; 31/10 is a Saturday.
    assert [i["period"] for i in pit] == ["quý 3/2026"]
    assert pit[0]["due"] == date(2026, 11, 2)
    assert pit[0]["url"] == "/payroll/pit-returns/quarter/2026/3"


def test_supplement(db, ctxs, staff, company, web, users):  # noqa: F811
    from app.features.vat import khbs

    ctx = ctxs[ACCOUNTANT]
    service.post_run(db, ctx, service.create_run(db, ctx, 2026, 1))
    pit_return.finalize(db, ctx, Q1)
    with pytest.raises(BusinessError, match="không khác"):
        pit_return.supplement(db, ctx, Q1, reason="x", until=date(2026, 6, 1))
    # February was left out of the filed return.
    service.post_run(db, ctx, service.create_run(db, ctx, 2026, 2))
    with pytest.raises(BusinessError, match="lý do"):
        pit_return.supplement(db, ctx, Q1, reason=" ", until=date(2026, 6, 1))
    decl = pit_return.supplement(
        db, ctx, Q1, reason="Bổ sung lương tháng 2", until=date(2026, 6, 1)
    )
    assert decl.version == 1
    (s,) = pit_return.supplements(db, company.id, Q1)
    assert s.payable == 257_500 and s.deductible == 0
    # Due 30/04/2026; interest from 01/05 to 31/05 = 31 days at 0.03%.
    assert s.due == date(2026, 4, 30) and s.days == 31
    assert s.interest == 2_395
    assert {c.code for c in s.changes} >= {"21", "26", "29"}
    assert pit_return.latest_values(db, company.id, Q1)["29"] == 915_000
    with pytest.raises(BusinessError, match="không khác"):
        pit_return.supplement(db, ctx, Q1, reason="x", until=date(2026, 6, 1))
    with pytest.raises(BusinessError, match="bổ sung"):
        pit_return.reopen(db, ctx, Q1)

    v, sup = pit_return.export(db, company.id, Q1, 1)
    c = db.get(Company, company.id)
    data = htkk_kk.build_xml(
        c,
        Q1,
        v,
        version=1,
        appendices=khbs.appendices_xml(c, htkk_kk.FORM, sup),
        signed_on=date(2026, 6, 1),
    )
    validate(data, "KHBS_05_KK_TNCN_TT80_293.xsd")
    root = ElementTree.fromstring(data)
    assert root.find(".//t:loaiTKhai", NS).text == "B"
    assert root.find(".//t:soLan", NS).text == "1"
    pl = root.find(".//t:PL01_KHBS", NS)
    assert pl.find(".//t:Muc_1/t:ChiTiet/t:ct2_ma", NS).text == "1001"
    assert int(pl.find(".//t:tongCong_ct10", NS).text) == 257_500
    assert int(pl.find(".//t:ct3b", NS).text) == 2_395
    rows = root.findall(".//t:PL01_1_KHBS//t:CTietHSo/t:ChiTiet", NS)
    by_code = {r.find("t:ct3_ma", NS).text: r for r in rows}
    assert by_code["[29]"].find("t:ct04", NS).text == "657500"
    assert by_code["[29]"].find("t:ct7", NS).text == "257500"
    assert by_code["[21]"].find("t:ct7", NS).text == "0"
    assert root.find(".//t:tongCong_7", NS).text == "257500"

    web.login(ROLE_EMAILS[CHIEF])
    url = "/payroll/pit-returns/quarter/2026/1"
    page = web.get(url).text
    assert "BS lần 1" in page and "Bổ sung lương tháng 2" in page
    r = web.get(url + ".xml?version=1")
    assert r.status_code == 200 and b"<PL01_1_KHBS>" in r.content
    assert web.get(url + ".xml?version=2").status_code == 404
    # Only the latest supplement can be deleted.
    r = web.post(url + "/supplements/1/delete", {}, follow_redirects=False)
    assert r.status_code == 303
    assert pit_return.supplements(db, company.id, Q1) == []
    r = web.post(
        url + "/supplements",
        {"reason": "Lương tháng 2", "until": "2026-05-04"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    (s,) = pit_return.supplements(db, company.id, Q1)
    assert s.days == 3 and s.reason == "Lương tháng 2"
    listing = web.get("/payroll/pit-returns").text
    assert "BS lần 1" in listing and "915.000" in listing
