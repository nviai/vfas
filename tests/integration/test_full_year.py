"""Golden data set: one company from 01/01 last year to today.

The company runs through the real services of every module.

Cross-module figures: data checks report no errors, F01 balances,
B01a assets = liabilities and equity, B02 = closing entries, B03 =
closing cash, inventory/fixed assets/tools match the general ledger,
payroll paid, the 01/GTGT return matches invoices, no negative
balances; plus a web smoke test.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.core.dates import month_bounds, today
from app.core.registry import ACCOUNTANT, CHIEF
from app.demo import build_demo
from app.features.checks.router import run_checks
from app.features.gl import books, periods, statements
from app.features.journal import queries
from app.features.journal.models import POSTED, JournalEntry, JournalLine
from app.features.org.models import Company
from tests.conftest import ROLE_EMAILS


@pytest.fixture()
def demo(db, ctxs, company):
    ctx = ctxs[CHIEF]
    summary = build_demo(db, ctx)
    db.expire_all()
    return db.get(Company, company.id), ctx, summary


def _last_costed_month_end(db, company) -> date:
    from app.features.inventory.costing import months_status

    done = [
        m
        for m in months_status(db, company)
        if m.run is not None and not m.run.is_stale and m.uncosted == 0
    ]
    assert done, "no month has been costed yet"
    return month_bounds(done[0].year, done[0].month)[1]


def test_full_year_figures(db, demo):
    company, ctx, summary = demo
    cid = company.id
    now = today()
    prev = periods.make("year", now.year - 1)
    cur = periods.make("custom", now.year, start=date(now.year, 1, 1), end=now)
    assert summary["total"] > 300
    for kind in (
        "PT",
        "PC",
        "BC",
        "BN",
        "CK",
        "BH",
        "MH",
        "TL",
        "GV",
        "KH",
        "PB",
        "LG",
        "KC",
    ):
        assert kind in summary["entries"], kind

    # 1. Data checks: no error-level results; the subledger vs. general
    # ledger reconciliation checks report no findings
    results = {r["code"]: r for r in run_checks(db, ctx)}
    errors = [
        (code, i.message)
        for code, r in results.items()
        for i in r["issues"]
        if i.severity == "error"
    ]
    assert not errors, errors
    assert not [r["code"] for r in results.values() if r["error"]]
    for code in (
        "inventory_gl_mismatch",
        "assets_vs_ledger",
        "prepaid_vs_ledger",
        "negative_money",
        "inventory_negative",
        "gl_trial_balance",
        "gl_balance_sheet",
        "opening_unbalanced",
        "vat_duplicate_invoice",
        "vat_missing_invoice",
    ):
        assert code in results and not results[code]["issues"], (
            code,
            results.get(code),
        )

    # 2. F01 balances; B01a assets = liabilities and equity (last year
    # closed, this year not yet)
    for p in (prev, cur):
        assert books.trial_balance(db, cid, p.start, p.end).balanced, p.label
        b01 = statements.b01a(db, company, p)
        assert b01.value("200") == b01.value("500") > 0, p.label

    # 3. Last year's B02: line 60 = profit/loss closed to 4212 on 31/12
    je, jl = JournalEntry, JournalLine
    kc = db.execute(
        select(jl.debit_account, jl.credit_account, jl.amount)
        .join(je, je.id == jl.entry_id)
        .where(
            je.company_id == cid,
            je.status == POSTED,
            je.doc_type == "KC",
            je.date == prev.end,
        )
    ).all()
    to_4212 = sum(a for d, c, a in kc if c == "4212") - sum(
        a for d, c, a in kc if d == "4212"
    )
    assert statements.b02(db, company, prev).value("60") == to_4212 != 0
    # last year's profit moved to 4211 at the start of this year
    assert (
        queries.balance(db, cid, "4212", exact=True, upto=date(now.year, 1, 1))
        == 0
    )
    assert (
        queries.balance(db, cid, "4211", exact=True, upto=date(now.year, 1, 1))
        == -to_4212
    )
    # balances left in class 5-9 accounts last year? must be zero after
    # closing
    assert not books.pl_balances(db, cid, prev.end)

    # 4. B03: closing cash = balance of 111 + 112
    for p in (prev, cur):
        assert statements.b03(db, company, p).value("70") == queries.balance(
            db, cid, ["111", "112"], upto=p.end
        )

    # 5. Inventory: stock value of the inventory module = balance of TK
    # 15x (at the end of the latest costed month)
    from app.features.inventory.service import on_hand

    end = _last_costed_month_end(db, company)
    stock_value = sum(r["amount"] for r in on_hand(db, cid, upto=end))
    assert stock_value == queries.balance(db, cid, "15", upto=end) > 0
    assert all(r["quantity"] >= 0 for r in on_hand(db, cid, upto=now))

    # 6. Fixed assets: cost and accumulated depreciation in the asset
    # register = balances of 211, 214; tools: remaining value = 242
    from app.features.assets.service import (
        list_prepaid,
        posted_depreciation,
        register,
    )

    # end of last month (depreciation already charged)
    on = date(now.year, now.month, 1) - timedelta(days=1)
    reg = register(db, cid, on)
    assert (
        sum(r.cost for r in reg)
        == queries.balance(db, cid, "211", upto=on)
        == 96_000_000
    )
    assert (
        sum(r.accumulated for r in reg)
        == -queries.balance(db, cid, "214", upto=on)
        > 0
    )
    items = list_prepaid(db, cid)
    allocated = posted_depreciation(
        db, cid, [p.id for p in items], kind="prepaid"
    )
    remaining = sum(
        p.amount - (p.opening_allocated or 0) - allocated.get(p.id, 0)
        for p in items
    )
    assert remaining == queries.balance(db, cid, "242", upto=now) > 0

    # 7. Payroll: credit balance of 334 = net pay of unpaid payrolls
    from app.features.payroll.service import list_runs

    unpaid = sum(
        ln.net_pay
        for r in list_runs(db, cid)
        if r.entry is not None
        and r.entry.status == POSTED
        and (r.payment_entry is None or r.payment_entry.status != POSTED)
        for ln in r.lines
    )
    assert -queries.balance(db, cid, "334", exact=True, upto=now) == unpaid
    assert (
        len(
            [
                r
                for r in list_runs(db, cid)
                if r.entry is not None and r.entry.status == POSTED
            ]
        )
        >= 12
    )

    # 8. 01/GTGT return: lines match the invoices; [43] of a period
    # carries over to [22] of the next one
    from app.features.vat import service as vat

    decls = sorted(
        [d for d in vat.list_declarations(db, cid) if d.status != "draft"],
        key=lambda d: (d.year, d.period),
    )
    assert len(decls) >= 4
    for d in decls:
        period = vat.TaxPeriod(d.period_type, d.year, d.period)
        out = in_ded = 0
        for rec, _e in vat.records(db, cid, period.start, period.end):
            if rec.direction == "out":
                out += vat.signed(rec) * rec.vat_amount
            elif rec.deductible:
                in_ded += vat.signed(rec) * rec.vat_amount
        assert d.values["28"] == out > 0, period.label
        assert d.values["25"] == in_ded > 0, period.label
    for a, b in zip(decls, decls[1:], strict=False):
        assert b.values["22"] == a.values["43"]

    # 9. No negative cash or bank account balances
    from app.features.bank.service import list_bank_accounts

    assert queries.first_negative(db, cid, "1111") is None
    for b in list_bank_accounts(db, cid):
        assert (
            queries.first_negative(
                db, cid, b.account_code, bank_account_id=b.id
            )
            is None
        )

    # 10. Last year's books are locked
    from app.features.journal import locks

    assert locks.is_locked(db, cid, date(now.year - 1, 6, 30))


PAGES = [
    "/",
    "/entries",
    "/opening",
    "/locks",
    "/closing",
    "/reports",
    "/reports/s03a",
    "/reports/s03b?account=1111",
    "/reports/ledger?account=131",
    "/reports/f01",
    "/reports/b01a",
    "/reports/b02",
    "/reports/b03",
    "/reports/b09",
    "/cash",
    "/cash/book",
    "/cash/counts",
    "/bank",
    "/bank/book",
    "/bank/statements",
    "/bank-accounts",
    "/sales",
    "/sales/returns",
    "/purchases",
    "/einvoices",
    "/items",
    "/debts/receivables",
    "/debts/payables",
    "/debts/receivables/aging",
    "/vat/lists",
    "/vat/declarations",
    "/vat/cit",
    "/vat/calendar",
    "/inventory/receipts",
    "/inventory/issues",
    "/inventory/transfers",
    "/inventory/costing",
    "/inventory/stock-card",
    "/inventory/ledger",
    "/inventory/summary",
    "/inventory/warehouses",
    "/assets",
    "/assets/depreciation",
    "/prepaid",
    "/prepaid/allocation",
    "/payroll",
    "/payroll/employees",
    "/payroll/reports",
    "/payroll/settings",
    "/accounts",
    "/partners",
    "/checks",
    "/audit",
    "/ai",
    "/settings/ai",
    "/settings/api-tokens",
    "/members",
]


def test_demo_pages_render(web, db, demo):
    company, _ctx, _summary = demo
    y = today().year - 1
    web.login(ROLE_EMAILS[CHIEF])
    for url in PAGES:
        r = web.get(url)
        assert r.status_code == 200, (url, r.status_code, r.text[:300])
    # Last year's reports (after closing) and their PDF/Excel exports
    for url in (
        f"/reports/b01a?mode=year&year={y}",
        f"/reports/b02?mode=year&year={y}",
        f"/reports/f01?mode=year&year={y}",
    ):
        assert web.get(url).status_code == 200, url
    for url in (
        f"/reports/b01a.pdf?mode=year&year={y}",
        f"/reports/b02.xlsx?mode=year&year={y}",
        f"/cash/book.pdf?mode=year&year={y}",
    ):
        r = web.get(url)
        assert r.status_code == 200 and len(r.content) > 1000, url
    # Detail page of every voucher type
    for doc_type in (
        "PT",
        "PC",
        "BC",
        "BN",
        "CK",
        "BH",
        "MH",
        "TL",
        "GV",
        "KH",
        "PB",
        "LG",
        "KC",
    ):
        e = db.scalars(
            select(JournalEntry)
            .where(
                JournalEntry.company_id == company.id,
                JournalEntry.doc_type == doc_type,
            )
            .limit(1)
        ).first()
        r = web.get(e.url)
        assert r.status_code == 200, (doc_type, e.url, r.text[:200])
    # English
    web.get("/lang/en")
    for url in (
        "/",
        "/sales",
        "/reports/b01a",
        "/payroll",
        "/inventory/summary",
    ):
        r = web.get(url)
        assert r.status_code == 200 and 'lang="en"' in r.text, url
    # The accountant also sees the dashboard with figures
    web.login(ROLE_EMAILS[ACCOUNTANT])
    assert web.get("/").status_code == 200


def test_cli_demo(engine, capsys):
    from app import cli
    from app.core.db import SessionLocal

    assert (
        cli.main(
            ["demo", "--email", "thu@demo.vn", "--password", "Ketoan#2026"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "thu@demo.vn" in out and "chứng từ" in out
    with SessionLocal() as s:
        assert (
            s.scalar(
                select(Company.id).where(Company.tax_code == "0318888888")
            )
            is not None
        )
