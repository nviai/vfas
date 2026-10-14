"""Trial balance, closing entries and financial statements.

Run on the one-year data set.
"""

from datetime import date

import pytest

from app.core.errors import BusinessError
from app.features.gl import books, closing, statements
from app.features.gl.periods import make
from app.features.journal import queries
from tests.gl.conftest import (
    CIT,
    FIN_INCOME,
    PROFIT_AFTER_TAX,
    PROFIT_BEFORE_TAX,
    REVENUE,
)

Y2024 = make("year", 2024)


@pytest.fixture()
def closed_year(db, ctx, company, year_book):
    closing.record_cit(db, ctx, date(2024, 12, 31), CIT)
    kc = closing.run(db, ctx, Y2024)
    return {**year_book, "kc": kc}


def test_trial_balance_balanced(db, company, year_book):
    tb = books.trial_balance(
        db, company.id, date(2024, 1, 1), date(2024, 12, 31), level=2
    )
    assert tb.balanced
    rows = {r.code: r for r in tb.rows}
    assert rows["111"].close_d == 50_000_000 - 5_500_000
    assert (
        rows["131"].per_d == 220_000_000 and rows["131"].per_c == 220_000_000
    )
    assert rows["2141"].close_c == 24_000_000
    # Level 1 does not mix in sub-accounts
    tb1 = books.trial_balance(
        db, company.id, date(2024, 1, 1), date(2024, 12, 31), level=1
    )
    assert all(r.level == 1 for r in tb1.rows) and tb1.balanced


def test_closing_and_income_statement(db, ctx, company, closed_year):
    kc = closed_year["kc"]
    assert kc.doc_type == "KC" and kc.status == "posted"
    to_4212 = sum(ln.amount for ln in kc.lines if ln.credit_account == "4212")
    assert to_4212 == PROFIT_AFTER_TAX
    # After closing: class 5-9 accounts have no balance
    assert books.pl_balances(db, company.id, date(2024, 12, 31)) == {}
    st = statements.b02(db, company, Y2024)
    assert st.value("01") == REVENUE and st.value("10") == REVENUE
    assert st.value("21") == FIN_INCOME
    assert st.value("24") == 89_000_000
    assert st.value("50") == PROFIT_BEFORE_TAX
    assert st.value("51") == CIT
    assert st.value("60") == PROFIT_AFTER_TAX == to_4212


def test_rerun_closing_replaces_entry(db, ctx, company, closed_year):
    kc1 = closed_year["kc"]
    kc2 = closing.run(db, ctx, Y2024)
    db.refresh(kc1)
    assert kc1.status == "cancelled" and kc2.status == "posted"
    assert (
        sum(ln.amount for ln in kc2.lines if ln.credit_account == "4212")
        == PROFIT_AFTER_TAX
    )
    # An earlier period cannot be rerun once a later one is closed
    with pytest.raises(BusinessError):
        closing.run(db, ctx, make("month", 2024, month=6))


def test_balance_sheet_equation(db, company, closed_year):
    st = statements.b01a(db, company, Y2024)
    assert st.value("200") == st.value("500") > 0
    assert st.value("417") == PROFIT_AFTER_TAX
    assert st.value("313") == CIT
    assert st.value("151") == 120_000_000 and st.value("152") == -24_000_000
    assert st.value("110") == queries.balance(
        db, company.id, ("111", "112"), upto=date(2024, 12, 31)
    )
    assert st.value("411") == 500_000_000
    assert not st.unmapped
    # Start of year (31/12/2023): no figures yet
    assert st.value("200", "previous") == 0


def test_balance_sheet_before_closing_includes_unclosed_profit(
    db, company, year_book
):
    st = statements.b01a(db, company, Y2024)
    assert st.value("200") == st.value("500")
    assert st.value("417") == PROFIT_BEFORE_TAX
    assert any("chưa kết chuyển" in w for w in st.warnings)


def test_cash_flow_statement(db, company, closed_year):
    st = statements.b03(db, company, Y2024)
    cash_end = queries.balance(
        db, company.id, ("111", "112"), upto=date(2024, 12, 31)
    )
    assert st.value("70") == cash_end
    assert not st.warnings
    assert st.value("31") == 500_000_000
    assert st.value("01") == 220_000_000
    # cash flow code overridden on the voucher line
    assert st.value("21") == -132_000_000
    assert st.value("03") == -52_700_000
    assert st.value("25") == FIN_INCOME
    assert st.value("20") + st.value("30") + st.value("40") == st.value("50")


def test_retained_earnings_transfer(db, ctx, company, closed_year):
    rp = closing.retained_plan(db, company.id, 2024)
    assert rp.amount == PROFIT_AFTER_TAX
    e = closing.run_retained(db, ctx, 2024)
    assert e.date == date(2025, 1, 1)
    assert (
        queries.balance(db, company.id, "4211", exact=True)
        == -PROFIT_AFTER_TAX
    )
    with pytest.raises(BusinessError):
        closing.run_retained(db, ctx, 2024)


def test_general_journal_and_ledger(db, company, year_book):
    nkc = books.general_journal(
        db, company.id, date(2024, 1, 1), date(2024, 12, 31)
    )
    assert nkc.total_debit == nkc.total_credit
    assert len(nkc.rows) == 2 * 21
    assert [r.stt for r in nkc.rows[:4]] == [1, 2, 3, 4]
    first = nkc.rows[0]
    assert (first.account, first.debit) == ("1121", 500_000_000)
    book = books.ledger_book(
        db,
        company.id,
        "112",
        date(2024, 1, 1),
        date(2024, 12, 31),
        with_nkc=True,
    )
    assert book.opening == 0
    assert book.closing == queries.balance(db, company.id, "112")
    assert all(getattr(r, "nkc_stt", None) for r in book.rows)
    assert book.rows[0].contra == "4111"
