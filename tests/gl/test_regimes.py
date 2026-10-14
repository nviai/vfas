"""Accounting regimes: TT99/2025 and TT58/2026 next to TT133.

Chart of accounts, statements (B01-DN with short/long-term split,
B02-DN, B03-DN; B01-DNSN, B02-DNSN), regime-specific default accounts
(payroll, assets), printed form codes and switching the regime of an
empty company.
"""

from datetime import date

import pytest

from app.core.errors import BusinessError
from app.core.regimes import account, form_for, issued_for
from app.core.registry import OWNER
from app.core.tenancy import Ctx
from app.features.accounts.models import Account
from app.features.bank.service import create_bank_account
from app.features.gl import notes, periods, statements
from app.features.org import service as org
from app.features.partners import service as ps
from tests.conftest import (
    ROLE_EMAILS,
    as_current,
    make_company,
    make_user,
    post,
)

M = 1_000_000
YEAR = periods.make("year", 2026)


def _company(db, regime: str, email: str, tax_code: str):
    owner = make_user(db, email)
    c = make_company(
        db,
        owner,
        name=f"Công ty {regime.upper()}",
        tax_code=tax_code,
        start=date(2026, 1, 1),
        regime=regime,
    )
    return c, Ctx(company_id=c.id, user=as_current(db, owner, c))


def _acc(db, c, code) -> Account:
    return db.query(Account).filter_by(company_id=c.id, code=code).one()


@pytest.fixture()
def tt99(db, company):
    return _company(db, "tt99", "chu@tt99.vn", "0101010199")


def _book_tt99(db, ctx):
    bank = create_bank_account(
        db, ctx, number="0071000999", bank_name="VCB", short_name="VCB"
    )
    kh = ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="Khách A", is_customer=True, tax_code="0101243150"
        ),
    )
    b = {"debit_bank_account_id": bank.id}
    cb = {"credit_bank_account_id": bank.id}
    post(db, ctx, date(2026, 1, 2), "1121", "41111", 500 * M, "BC", **b)
    post(db, ctx, date(2026, 1, 5), "1121", "3411", 100 * M, "BC", **b)
    post(db, ctx, date(2026, 1, 10), "211", "1121", 120 * M, "BN", **cb)
    post(
        db,
        ctx,
        date(2026, 2, 1),
        "131",
        "5113",
        200 * M,
        "PK",
        debit_partner_id=kh.id,
    )
    post(
        db,
        ctx,
        date(2026, 3, 1),
        "1121",
        "131",
        150 * M,
        "BC",
        credit_partner_id=kh.id,
        **b,
    )
    post(db, ctx, date(2026, 3, 5), "6427", "1121", 20 * M, "BN", **cb)
    post(db, ctx, date(2026, 3, 6), "6417", "1121", 5 * M, "BN", **cb)
    post(db, ctx, date(2026, 3, 31), "6424", "2141", 2 * M, "PK")
    post(db, ctx, date(2026, 3, 31), "82111", "3334", 3 * M, "PK")
    return bank


def test_tt99_chart_and_defaults(db, tt99):
    c, _ctx = tt99
    assert c.regime == "tt99"
    assert _acc(db, c, "211").is_postable
    assert _acc(db, c, "641").is_postable is False
    assert _acc(db, c, "6421").name == "Chi phí nhân viên quản lý"
    assert _acc(db, c, "3386").name == "Bảo hiểm thất nghiệp"
    assert _acc(db, c, "1121").needs_bank and _acc(db, c, "1111").no_negative
    assert account(c, "unemployment_insurance") == "3386"
    assert account(c, "expense_depreciation") == "6424"
    assert form_for(c, "S03a-DNN") == "S03a-DN"
    assert form_for(c, "F01-DNN") == "S06-DN"
    assert "99/2025/TT-BTC" in issued_for(c)


def test_tt99_statements(db, tt99):
    c, ctx = tt99
    _book_tt99(db, ctx)
    ss = statements.statement_set(c)
    b01 = ss.builders["b01a"](db, c, YEAR)
    assert b01.code == "B01-DN"
    assert b01.value("280") == b01.value("440") > 0
    assert b01.value("111") == 500 * M + 100 * M - 120 * M + 150 * M - 25 * M
    assert b01.value("131") == 50 * M  # short term by default
    assert b01.value("222") == 120 * M and b01.value("223") == -2 * M
    assert b01.value("321") == 100 * M and b01.value("339") == 0
    assert b01.value("411a") == 500 * M
    # unclosed revenue and expenses are this year's profit
    assert b01.value("420b") == 200 * M - 27 * M - 3 * M
    assert any("3411" in w or "341" in w for w in b01.warnings)
    b02 = ss.builders["b02"](db, c, YEAR)
    assert b02.value("10") == 200 * M
    assert b02.value("25") == 5 * M and b02.value("26") == 22 * M
    assert b02.value("51") == 3 * M
    assert b02.value("60") == 170 * M
    b03 = ss.builders["b03"](db, c, YEAR)
    assert b03.value("70") == b01.value("110")
    assert b03.value("33") == 100 * M and b03.value("31") == 500 * M
    assert b03.value("21") == -120 * M and b03.value("01") == 150 * M
    nb = notes.build(db, c, YEAR)
    codes = {t.code: t for t in nb.income_tables}
    assert "VII.05" in codes
    assert dict((r[0], r[1]) for r in codes["VII.05"].rows) == {
        "Chi phí bán hàng": 5 * M,
        "Chi phí quản lý doanh nghiệp": 22 * M,
    }


def test_tt99_long_term_setting(db, tt99):
    c, ctx = tt99
    _book_tt99(db, ctx)
    org.update_company_settings(
        db, ctx, "b01", {"long_term": ["3411"], "short_term": []}
    )
    b01 = statements.statement_set(c).builders["b01a"](db, c, YEAR)
    assert b01.value("321") == 0 and b01.value("339") == 100 * M
    assert b01.value("280") == b01.value("440")


def test_tt99_payroll_and_assets_use_regime_accounts(db, tt99):
    from app.features.assets import service as assets
    from app.features.payroll import service as payroll

    c, ctx = tt99
    _book_tt99(db, ctx)
    e = payroll.create_employee(
        db,
        ctx,
        payroll.EmployeeInput(
            full_name="Nhân Viên TT99",
            base_salary=20 * M,
            insurance_salary=20 * M,
            start_date=date(2026, 1, 1),
        ),
    )
    assert e.expense_account == "6421"
    run = payroll.create_run(db, ctx, 2026, 1)
    entry = payroll.post_run(db, ctx, run)
    accounts = {ln.credit_account for ln in entry.lines}
    assert "3386" in accounts and "3385" not in accounts
    a = assets.create_asset(
        db,
        ctx,
        assets.AssetInput(
            name="Máy tính",
            cost=30 * M,
            useful_life_months=36,
            acquired_date=date(2026, 1, 10),
        ),
    )
    assert (a.asset_account, a.depreciation_account, a.expense_account) == (
        "211",
        "2141",
        "6424",
    )
    with pytest.raises(BusinessError, match="TK chi phí lương"):
        payroll.create_employee(
            db,
            ctx,
            payroll.EmployeeInput(full_name="Sai TK", expense_account="811"),
        )


def test_tt99_pages_and_print_headers(db, tt99, web):
    c, ctx = tt99
    _book_tt99(db, ctx)
    web.login("chu@tt99.vn")
    page = web.get("/reports?mode=year&year=2026").text
    assert "B01-DN" in page and "S06-DN" in page and "B09-DN" in page
    assert "/reports/bctc.xml" not in page  # HTKK set 683 is TT133-only
    r = web.get("/reports/b01a?mode=year&year=2026")
    assert r.status_code == 200 and "Mẫu số B01-DN" in r.text
    assert "Kèm theo Thông tư số 99/2025/TT-BTC" in r.text
    assert "Trình bày ngắn hạn / dài hạn" in r.text
    assert "S03a-DN" in web.get("/reports/s03a?mode=month&month=2026-03").text
    assert web.get("/reports/b01a.pdf?mode=year&year=2026").status_code == 200
    assert web.get("/reports/b03.xlsx?mode=year&year=2026").status_code == 200
    r = web.get("/reports/bctc.xml?year=2026", follow_redirects=False)
    assert r.status_code == 303  # refused with a message
    r = web.post(
        "/reports/b01-terms",
        {
            "long_term": "3411",
            "short_term": "",
            "query": "mode=year&year=2026",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert (
        statements.statement_set(c).builders["b01a"](db, c, YEAR).value("339")
        == 100 * M
    )


def test_tt58_statements(db, company, web):
    c, ctx = _company(db, "tt58", "chu@tt58.vn", "0101010158")
    post(db, ctx, date(2026, 7, 2), "1111", "4111", 50 * M, "PT")
    post(db, ctx, date(2026, 7, 10), "1111", "5113", 30 * M, "PT")
    post(db, ctx, date(2026, 7, 20), "6422", "1111", 10 * M, "PC")
    post(db, ctx, date(2026, 7, 31), "821", "3334", 1 * M, "PK")
    ss = statements.statement_set(c)
    assert set(ss.builders) == {"b01a", "b02"} and ss.notes_code is None
    b01 = ss.builders["b01a"](db, c, YEAR)
    assert b01.code == "B01-DNSN" and not b01.has_notes
    assert b01.value("110") == 70 * M
    assert b01.value("200") == b01.value("500") == 70 * M
    assert b01.value("320") == 1 * M and b01.value("410") == 50 * M
    b02 = ss.builders["b02"](db, c, YEAR)
    assert (b02.value("01"), b02.value("02"), b02.value("20")) == (
        30 * M,
        10 * M,
        19 * M,
    )
    assert form_for(c, "S03a-DNN") == "" and form_for(c, "01-TT") == "01-TT"
    assert "58/2026/TT-BTC" in issued_for(c, "01-TT")
    web.login("chu@tt58.vn")
    assert "B01-DNSN" in web.get("/reports/b01a?mode=year&year=2026").text
    assert web.get("/reports/b03?mode=year&year=2026").status_code == 404
    assert web.get("/reports/b09?mode=year&year=2026").status_code == 404
    page = web.get("/reports/f01?mode=year&year=2026").text
    assert "F01-DNN" not in page
    assert web.get("/reports/b01a.xlsx?mode=year&year=2026").status_code == 200


def test_switch_regime_only_with_empty_books(db, ctxs, company, users):
    ctx = ctxs[OWNER]
    org.set_regime(db, ctx, "tt99")
    db.expire_all()
    assert _acc(db, company, "6421").name == "Chi phí nhân viên quản lý"
    assert _acc(db, company, "211").is_postable
    assert not _acc(db, company, "2111").is_active  # TT133-only account
    org.set_regime(db, ctx, "tt133")
    db.expire_all()
    assert _acc(db, company, "6421").name == "Chi phí bán hàng"
    assert _acc(db, company, "2111").is_active
    assert not _acc(db, company, "211").is_postable
    post(db, ctx, date(2026, 1, 5), "1111", "4111", 10 * M, "PT")
    with pytest.raises(BusinessError, match="chứng từ"):
        org.set_regime(db, ctx, "tt99")
    with pytest.raises(BusinessError, match="không hợp lệ"):
        org.set_regime(db, ctx, "tt200")


def test_regime_chosen_when_creating_a_company(web, users):
    web.login(ROLE_EMAILS[OWNER])
    page = web.get("/companies/new").text
    assert 'value="tt99"' in page and 'value="tt58"' in page
    r = web.post(
        "/companies/new",
        {"name": "Công ty Chọn TT99", "regime": "tt99"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "B01-DN" in web.get("/reports?mode=year&year=2026").text
    settings = web.get("/settings?tab=accounting").text
    assert 'name="regime"' in settings
