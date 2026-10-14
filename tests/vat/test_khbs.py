"""Supplementary 01/GTGT returns (01/KHBS, 01-1/KHBS)."""

from datetime import date

import pytest
from defusedxml import ElementTree

from app.core.errors import BusinessError
from app.core.registry import ACCOUNTANT, VIEWER
from app.features.org.models import Company
from app.features.vat import htkk, khbs
from app.features.vat import service as vs
from app.features.vat.models import FORM_VAT
from tests.conftest import ROLE_EMAILS
from tests.htkk_schema import validate
from tests.sales.helpers import line, sell
from tests.vat.test_vat import parties, q1  # noqa: F401 - fixtures

Q1 = vs.TaxPeriod("quarter", 2026, 1)
NS = {"t": htkk.NAMESPACE}


def test_late_interest():
    # From the day after the deadline to the day before payment.
    assert khbs.late_interest(
        1_000_000, date(2026, 4, 30), date(2026, 5, 1)
    ) == (0, 0)
    assert khbs.late_interest(
        1_000_000, date(2026, 4, 30), date(2026, 5, 2)
    ) == (1, 300)
    assert khbs.late_interest(-500, date(2026, 4, 30), date(2026, 6, 1)) == (
        0,
        0,
    )
    assert khbs.late_interest(500, date(2026, 4, 30), date(2026, 4, 1)) == (
        0,
        0,
    )


def test_vat_supplement(db, ctx, company, parties, q1):  # noqa: F811
    with pytest.raises(BusinessError, match="Chưa chốt"):
        vs.supplement(db, ctx, Q1, reason="x", until=date(2026, 5, 15))
    vs.save(db, ctx, Q1, manual={}, carried_override=None, finalize=True)
    assert vs.pending_changes(db, company.id, Q1) == []
    # An invoice of March was left out of the filed return.
    c, _s = parties
    sell(
        db,
        ctx,
        date(2026, 3, 15),
        [line(3_000_000, "10")],
        partner_id=c.id,
        invoice_no="6",
    )
    pending = {ch.code: ch for ch in vs.pending_changes(db, company.id, Q1)}
    assert pending["40"].declared == 860_000
    assert pending["40"].adjusted == 1_160_000
    decl = vs.supplement(
        db, ctx, Q1, reason="Bỏ sót hóa đơn số 6", until=date(2026, 5, 15)
    )
    assert decl.version == 1 and decl.values["40"] == 1_160_000
    (s,) = vs.supplements(db, company.id, Q1)
    assert s.payable == 300_000 and s.deductible == 0 and s.refund == 0
    # Deadline 30/04/2026, 01/05 – 14/05 = 14 days at 0.03%.
    assert (s.days, s.interest) == (14, 1_260)
    assert vs.pending_changes(db, company.id, Q1) == []
    with pytest.raises(BusinessError, match="bổ sung"):
        vs.reopen(db, ctx, Q1)

    c = db.get(Company, company.id)
    data = htkk.build_xml(
        c,
        Q1,
        decl.values,
        version=1,
        appendices=khbs.appendices_xml(c, htkk.FORM, s),
        signed_on=date(2026, 5, 15),
    )
    validate(data, "KHBS_01_GTGT_TT80_283.xsd")
    root = ElementTree.fromstring(data)
    assert root.find(".//t:loaiTKhai", NS).text == "B"
    assert root.find(".//t:CTieuTKhaiChinh/t:ct40", NS).text == "1160000"
    pl = root.find(".//t:PL01_KHBS", NS)
    assert pl.find(".//t:Muc_1/t:ChiTiet/t:ct2_ma", NS).text == "1701"
    assert pl.find(".//t:tongCong_ct10", NS).text == "300000"
    assert pl.find(".//t:ct3b", NS).text == "1260"
    codes = [
        r.find("t:ct3_ma", NS).text
        for r in root.findall(".//t:PL01_1_KHBS//t:ChiTiet", NS)
    ]
    assert "[32]" in codes and "[40]" in codes and "[22]" not in codes


def test_vat_supplement_changes_deductible(db, ctx, company, parties):  # noqa: F811
    from tests.sales.helpers import buy

    c, s = parties
    buy(
        db,
        ctx,
        date(2026, 2, 1),
        [line(10_000_000, "10", "156")],
        partner_id=s.id,
        invoice_no="20",
    )
    vs.save(db, ctx, Q1, manual={}, carried_override=None, finalize=True)
    sell(
        db,
        ctx,
        date(2026, 3, 1),
        [line(4_000_000, "10")],
        partner_id=c.id,
        invoice_no="21",
    )
    vs.supplement(db, ctx, Q1, reason="Bổ sung", until=date(2026, 5, 4))
    (sup,) = vs.supplements(db, company.id, Q1)
    # Still in credit: no tax payable, the credit carried forward drops.
    assert sup.payable == 0 and sup.deductible == -400_000
    assert sup.interest == 0
    # A draft next period carries the corrected [43].
    assert vs.carried_from_previous(db, company.id, Q1.next())[0] == 600_000


def test_vat_supplement_web(db, ctx, company, parties, q1, web, users):  # noqa: F811
    vs.save(db, ctx, Q1, manual={}, carried_override=None, finalize=True)
    c, _s = parties
    sell(
        db,
        ctx,
        date(2026, 3, 15),
        [line(3_000_000, "10")],
        partner_id=c.id,
        invoice_no="6",
    )
    web.login(ROLE_EMAILS[ACCOUNTANT])
    url = "/vat/declarations/quarter/2026/1"
    page = web.get(url).text
    assert "Chênh lệch so với lần khai gần nhất" in page
    r = web.post(
        url + "/supplements",
        {"reason": "Bỏ sót hóa đơn số 6", "until": "2026-05-15"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = web.get(url).text
    assert "BS lần 1" in page and "1.160.000" in page
    listing = web.get("/vat/declarations").text
    assert "BS lần 1" in listing and "1.160.000" in listing
    r = web.get(url + ".xml?version=1")
    assert r.status_code == 200 and b"<loaiTKhai>B</loaiTKhai>" in r.content
    r = web.get(url + ".pdf?version=1")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert web.get(url + ".xml?version=3").status_code == 404
    web.login(ROLE_EMAILS[VIEWER])
    r = web.post(url + "/supplements/1/delete", {}, follow_redirects=False)
    assert r.status_code == 403
    web.login(ROLE_EMAILS[ACCOUNTANT])
    r = web.post(url + "/supplements/1/delete", {}, follow_redirects=False)
    assert r.status_code == 303
    assert khbs.versions(db, company.id, FORM_VAT, Q1)[-1].version == 0
