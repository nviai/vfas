"""Loss carry-forward of the CIT worksheet (appendix 03-2/TNDN)."""

from datetime import date

import pytest
from defusedxml import ElementTree

from app.core.errors import BusinessError
from app.core.registry import ACCOUNTANT
from app.features.org.models import Company
from app.features.vat import cit, htkk_cit
from tests.conftest import ROLE_EMAILS, post
from tests.htkk_schema import validate

NS = {"t": "http://kekhaithue.gdt.gov.vn/TKhaiThue"}


def test_carry_oldest_first_within_five_years():
    rows = [
        {"year": 2023, "loss": 30, "before": 10},
        {"year": 2020, "loss": 99, "before": 0},  # expired for 2026
        {"year": 2021, "loss": 50, "before": 45},
        {"year": 2025, "loss": 40, "before": 0},
    ]
    out = cit.carry_losses(rows, 2026, 40)
    assert [(r["year"], r["carry"], r["remaining"]) for r in out] == [
        (2021, 5, 0),
        (2023, 20, 0),
        (2025, 15, 25),
    ]
    # Nothing to carry against a loss year.
    assert {r["carry"] for r in cit.carry_losses(rows, 2026, -10)} == {0}


def test_losses_from_finalized_years(db, ctx, company):
    # 2025: expenses only -> loss of 10 million.
    post(db, ctx, date(2025, 3, 1), "6422", "3388", 10_000_000, "PK")
    cit.save(db, ctx, 2025, {}, None, finalize=True)
    assert cit.build(db, company.id, 2025)[0]["B12"] == -10_000_000
    # 2026: 4 million of profit uses 4 million of the 2025 loss.
    post(db, ctx, date(2026, 2, 1), "1388", "5113", 4_000_000, "PK")
    v = cit.compute(db, company.id, 2026)
    assert v["C3"] == 4_000_000 and v["C4"] == 0 and v["D"] == 0
    assert v["_losses"] == [
        {
            "year": 2025,
            "loss": 10_000_000,
            "before": 0,
            "carry": 4_000_000,
            "remaining": 6_000_000,
        }
    ]
    cit.save(db, ctx, 2026, {}, None, finalize=True)
    # 2027 sees what 2026 already carried.
    post(db, ctx, date(2027, 2, 1), "1388", "5113", 20_000_000, "PK")
    v = cit.compute(db, company.id, 2027)
    assert v["_losses"][0]["before"] == 4_000_000
    assert v["C3"] == 6_000_000 and v["C4"] == 14_000_000

    c = db.get(Company, company.id)
    data = htkk_cit.build_xml(db, c, 2027, v)
    validate(data, "03_TNDN_TT80_294.xsd")
    root = ElementTree.fromstring(data)
    assert root.find(".//t:ctC3", NS).text == "6000000"
    assert root.find(".//t:ctC3a", NS).text == "6000000"
    pl = root.find(".//t:PLuc_03_2_TNDN", NS)
    first = pl.find(".//t:noiDungNamThu1", NS)
    assert first.find("t:ct2", NS).text == "2025"
    assert first.find("t:ct4", NS).text == "4000000"
    assert pl.find(".//t:tongCong", NS).text == "6000000"
    # Unused years are nil.
    assert pl.find(".//t:noiDungNamThu2/t:ct3", NS).text is None


def test_manual_losses_and_web(db, ctxs, company, web, users):
    ctx = ctxs[ACCOUNTANT]
    post(db, ctx, date(2026, 2, 1), "1388", "5113", 30_000_000, "PK")
    with pytest.raises(BusinessError, match="tối đa 5 năm"):
        cit.save(
            db,
            ctx,
            2026,
            {},
            None,
            losses=[{"year": 2020, "loss": 1, "before": 0}],
        )
    with pytest.raises(BusinessError, match="lớn hơn"):
        cit.save(
            db,
            ctx,
            2026,
            {},
            None,
            losses=[{"year": 2024, "loss": 1, "before": 2}],
        )
    web.login(ROLE_EMAILS[ACCOUNTANT])
    page = web.get("/vat/cit?year=2026").text
    assert "03-2/TNDN" in page and 'name="loss_year_0"' in page
    r = web.post(
        "/vat/cit",
        {
            "year": "2026",
            "action": "save",
            "losses_form": "1",
            "loss_year_0": "2024",
            "loss_amount_0": "12.000.000",
            "loss_before_0": "2.000.000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    v, _decl = cit.build(db, company.id, 2026)
    assert v["C3"] == 10_000_000 and v["C4"] == 20_000_000
    # Clearing the table means no losses (not the history).
    web.post(
        "/vat/cit",
        {"year": "2026", "action": "save", "losses_form": "1"},
        follow_redirects=False,
    )
    db.expire_all()
    assert cit.build(db, company.id, 2026)[0]["C3"] == 0
