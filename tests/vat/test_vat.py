"""VAT return and tax calendar.

01/GTGT return checked by hand, [43] carried to [22] of the next
period, HTKK XML, CIT worksheet, tax filing calendar.
"""

from datetime import date

import pytest
from defusedxml import ElementTree

from app.core.errors import BusinessError
from app.features.journal import service as js
from app.features.org.models import Company
from app.features.partners import service as ps
from app.features.vat import cit, htkk
from app.features.vat import service as vs
from tests.conftest import post
from tests.htkk_schema import validate
from tests.sales.helpers import buy, line, sell


@pytest.fixture()
def parties(db, ctx):
    c = ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="Khách A", is_customer=True, tax_code="0101243150"
        ),
    )
    s = ps.create(
        db,
        ctx,
        ps.PartnerInput(name="NCC B", is_supplier=True, tax_code="0301234567"),
    )
    return c, s


@pytest.fixture()
def q1(db, ctx, parties):
    c, s = parties
    sell(
        db,
        ctx,
        date(2026, 1, 10),
        [
            line(10_000_000, "10"),
            line(5_000_000, "8", "5111"),
            line(2_000_000, "KCT"),
        ],
        partner_id=c.id,
        invoice_no="1",
    )
    sell(
        db,
        ctx,
        date(2026, 2, 10),
        [line(4_000_000, "5", "5111"), line(3_000_000, "0", "5111")],
        partner_id=c.id,
        invoice_no="2",
    )
    sell(
        db,
        ctx,
        date(2026, 3, 1),
        [line(1_000_000, "KKKNT")],
        partner_id=c.id,
        invoice_no="3",
    )
    sell(
        db,
        ctx,
        date(2026, 3, 20),
        [line(2_000_000, "10")],
        kind="return",
        partner_id=c.id,
        invoice_no="4",
    )
    buy(
        db,
        ctx,
        date(2026, 2, 1),
        [
            line(6_000_000, "10", "156"),
            line(500_000, "8", "6422"),
            line(200_000, "10", "6422", deductible=False),
        ],
        partner_id=s.id,
        invoice_no="9",
    )
    buy(
        db,
        ctx,
        date(2026, 3, 25),
        [line(1_000_000, "10", "156")],
        kind="return",
        partner_id=s.id,
        invoice_no="10",
    )
    # Not counted: drafts, other periods
    sell(
        db,
        ctx,
        date(2026, 3, 30),
        [line(9_000_000, "10")],
        partner_id=c.id,
        post=False,
    )
    sell(
        db,
        ctx,
        date(2026, 4, 2),
        [line(7_000_000, "10")],
        partner_id=c.id,
        invoice_no="5",
    )


def test_declaration_q1_hand_calculated(db, company, q1):
    p = vs.TaxPeriod("quarter", 2026, 1)
    v = vs.compute(db, company.id, p.start, p.end)
    expected = {
        "22": 0,
        "23": 5_700_000,
        "24": 560_000,
        "25": 540_000,
        "26": 2_000_000,
        "29": 3_000_000,
        "30": 4_000_000,
        "31": 200_000,
        "32": 13_000_000,
        "33": 1_200_000,
        "32a": 1_000_000,
        "27": 21_000_000,
        "28": 1_400_000,
        "34": 23_000_000,
        "35": 1_400_000,
        "36": 860_000,
        "40a": 860_000,
        "40": 860_000,
        "41": 0,
        "43": 0,
    }
    assert {k: v[k] for k in expected} == expected
    assert v["21"] is False
    assert [r["goods"] for r in v["_reduced"]] == [5_000_000]
    assert v["_reduced"][0]["reduced"] == 100_000
    lst = vs.vat_list(db, company.id, p.start, p.end, "out")
    assert lst.goods == 21_000_000 + 2_000_000 and lst.vat == 1_400_000


def test_carry_forward_43_to_22(db, ctx, company, parties):
    c, s = parties
    buy(
        db,
        ctx,
        date(2026, 5, 1),
        [line(20_000_000, "10", "156")],
        partner_id=s.id,
        invoice_no="20",
    )
    sell(
        db,
        ctx,
        date(2026, 5, 2),
        [line(5_000_000, "10")],
        partner_id=c.id,
        invoice_no="21",
    )
    q2 = vs.TaxPeriod("quarter", 2026, 2)
    vs.save(db, ctx, q2, manual={}, carried_override=None, finalize=True)
    v2 = vs.build(db, company.id, q2, vs.get_declaration(db, company.id, q2))
    assert (
        v2["36"] == -1_500_000
        and v2["41"] == 1_500_000
        and v2["43"] == 1_500_000
        and v2["40"] == 0
    )
    sell(
        db,
        ctx,
        date(2026, 8, 2),
        [line(30_000_000, "10")],
        partner_id=c.id,
        invoice_no="22",
    )
    q3 = vs.TaxPeriod("quarter", 2026, 3)
    v3 = vs.build(db, company.id, q3)
    assert v3["22"] == 1_500_000 and v3["40"] == 1_500_000
    decl = vs.save(db, ctx, q3, manual={"37": 100_000}, carried_override=None)
    v3 = vs.build(db, company.id, q3, decl)
    assert v3["40a"] == 1_600_000 and v3["37"] == 100_000
    # An earlier period cannot be reopened once a later one is final
    vs.save(
        db,
        ctx,
        q3,
        manual={"37": 100_000},
        carried_override=None,
        finalize=True,
    )
    with pytest.raises(BusinessError):
        vs.reopen(db, ctx, q2)
    with pytest.raises(BusinessError):
        vs.save(db, ctx, q3, manual={}, carried_override=None)
    vs.reopen(db, ctx, q3)
    vs.reopen(db, ctx, q2)
    # The refund request cannot exceed [41]
    with pytest.raises(BusinessError):
        vs.save(db, ctx, q2, manual={"42": 2_000_000}, carried_override=None)


def test_htkk_xml(db, company, q1):
    p = vs.TaxPeriod("quarter", 2026, 1)
    v = vs.compute(db, company.id, p.start, p.end)
    data = htkk.build_xml(
        db.get(Company, company.id), p, v, signed_on=date(2026, 4, 20)
    )
    root = ElementTree.fromstring(data)
    ns = {"t": htkk.NAMESPACE}
    assert root.find(".//t:mst", ns).text == "0000000001"
    assert (
        root.find(".//t:kyKKhai", ns).text == "1/2026"
        and root.find(".//t:kieuKy", ns).text == "Q"
    )
    assert (
        root.find(".//t:ct32", ns).text == "13000000"
        and root.find(".//t:ct40", ns).text == "860000"
    )
    assert root.find(".//t:ct32a", ns).text == "1000000"
    # Schema 2.8.3: business line + sub-item before [21], no empty
    # signature element
    assert root.find(".//t:pbanTKhaiXML", ns).text == "2.8.3"
    main = root.find(".//t:CTieuTKhaiChinh", ns)
    assert [c.tag.split("}")[1] for c in main][:5] == [
        "ma_NganhNghe",
        "ten_NganhNghe",
        "tieuMucHachToan",
        "Header",
        "ct21",
    ]
    assert root.find(".//t:CKyDTu", ns) is None
    validate(data, "01_GTGT_TT80_283.xsd")


def test_vat_offset_entry(db, ctx, company, q1):
    e = vs.create_offset_entry(db, ctx, vs.TaxPeriod("quarter", 2026, 1))
    assert e.status == "draft" and e.doc_type == "PK"
    assert [
        (ln.debit_account, ln.credit_account, ln.amount) for ln in e.lines
    ] == [("33311", "1331", 540_000)]
    js.post_entry(db, ctx, e)


def test_cit_computation(db, ctx, company):
    post(db, ctx, date(2026, 1, 5), "1111", "4111", 10_000_000, "PT")
    post(db, ctx, date(2026, 2, 1), "1388", "5113", 100_000_000, "PK")
    post(db, ctx, date(2026, 2, 2), "6422", "3388", 40_000_000, "PK")
    post(db, ctx, date(2026, 2, 3), "811", "3388", 5_000_000, "PK")
    post(db, ctx, date(2026, 2, 4), "1388", "515", 1_000_000, "PK")
    post(db, ctx, date(2026, 4, 30), "3334", "1111", 2_000_000, "PC")
    post(db, ctx, date(2026, 6, 30), "5113", "911", 100_000_000, "KC")
    v = cit.compute(db, company.id, 2026, {"B1": 4_000_000})
    assert v["A1"] == 56_000_000 and v["B12"] == 60_000_000 and v["C7"] == 15
    assert v["C8"] == 9_000_000 and v["E"] == 2_000_000 and v["F"] == 7_000_000
    assert (
        cit.rate_for_revenue(3_000_000_000) == 15
        and cit.rate_for_revenue(3_000_000_001) == 17
    )
    assert cit.rate_for_revenue(50_000_000_001) == 20
    cit.save(db, ctx, 2026, {"B1": 4_000_000}, rate=20, finalize=True)
    values, decl = cit.build(db, company.id, 2026)
    assert decl.status == "final" and values["C8"] == 12_000_000


def test_calendar(company):
    from app.features.vat.calendar import deadlines

    items = deadlines(company, date(2026, 10, 14))
    titles = [i["title"] for i in items]
    assert "Tờ khai thuế GTGT quý 3/2026" in titles
    q3 = next(i for i in items if i["title"] == "Tờ khai thuế GTGT quý 3/2026")
    # 31/10 is a Saturday
    assert q3["due"] == date(2026, 11, 2) and q3["days"] == 19
