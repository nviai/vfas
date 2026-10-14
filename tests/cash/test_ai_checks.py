"""AI tools and data checks of the cash / bank group."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import BusinessError
from app.core.registry import CASHIER, load_features, registry
from app.features.bank import statements as st
from app.features.bank.service import create_bank_account
from app.features.cash import counts
from app.features.org.models import Company
from tests.cash.test_vouchers import voucher


def tool(name):
    load_features()
    return registry.ai_tools[name].func


def test_ai_tools(db, ctx, ctxs, company):
    voucher(db, ctx, "PT", date(2024, 1, 2), 3_000_000, "4111")
    assert (
        tool("get_cash_balance")(db, ctx, date="2024-01-31")["balance"]
        == 3_000_000
    )
    vcb = create_bank_account(db, ctx, number="111", bank_name="VCB")
    out = tool("get_bank_balances")(db, ctx)
    assert (
        out["accounts"][0]["bank_account"] == vcb.label and out["total"] == 0
    )
    r = tool("create_draft_cash_voucher")(
        db,
        ctxs[CASHIER],
        kind="payment",
        amount=500_000,
        contra_account="6422",
        description="Mua nước uống",
        date="2024-01-05",
    )
    assert r["url"].startswith("/cash/") and r["status"] == "draft"
    with pytest.raises(BusinessError):
        tool("create_draft_cash_voucher")(
            db,
            ctx,
            kind="payment",
            amount=-1,
            contra_account="6422",
            description="x",
        )
    assert registry.ai_tools["create_draft_cash_voucher"].write is True
    assert tool("list_unmatched_bank_lines")(db, ctx)["count"] == 0


def test_data_checks(db, ctx, company):
    voucher(db, ctx, "PT", date(2024, 5, 2), 100_000, "4111")
    inp, _ = counts.parse_form(
        {"count_date": "2024-05-02", "qty_50000": "1"},
        db.get(Company, company.id),
    )
    counts.create_cash_count(db, ctx, inp)
    load_features()
    check = next(
        c for c in registry.checks if c.code == "cashcount_unresolved"
    )
    issues = check.func(db, ctx)
    assert len(issues) == 1 and "thiếu 50.000" in issues[0].message
    vcb = create_bank_account(db, ctx, number="222", bank_name="VCB")
    rows = st.read_rows(
        (
            "Ngày;Diễn giải;Ghi nợ;Ghi có;Số dư\n01/01/2024;Phí;;1000;1000\n"
        ).encode(),
        "s.csv",
    )
    h, m = st.guess_mapping(rows)
    st.import_statement(
        db, ctx, vcb, "s.csv", st.apply_mapping(rows, h, m), h, m
    )
    old = next(
        c for c in registry.checks if c.code == "bank_unmatched_old"
    ).func(db, ctx)
    mism = next(
        c for c in registry.checks if c.code == "bank_statement_mismatch"
    ).func(db, ctx)
    assert len(old) == 1 and len(mism) == 1
