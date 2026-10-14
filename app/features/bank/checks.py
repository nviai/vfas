"""Data checks of the bank deposit module.

Flags statement lines left unmatched for a long time and book
balances that differ from the statement balance.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.core.dates import fmt_date, today
from app.core.money import format_vnd
from app.core.registry import CheckIssue, data_check
from app.features.bank import statements as st
from app.features.bank.models import BankAccount
from app.features.bank.statement_models import UNMATCHED, BankStatementLine


@data_check(
    code="bank_unmatched_old",
    title="Giao dịch sao kê chưa khớp quá 30 ngày",
    perm="bank.view",
    order=35,
)
def check_unmatched(db, ctx):
    cutoff = today() - timedelta(days=30)
    rows = db.execute(
        select(
            BankStatementLine.bank_account_id,
            func.count(BankStatementLine.id),
            func.min(BankStatementLine.date),
        )
        .where(
            BankStatementLine.company_id == ctx.company_id,
            BankStatementLine.status == UNMATCHED,
            BankStatementLine.date <= cutoff,
        )
        .group_by(BankStatementLine.bank_account_id)
    ).all()
    out = []
    for bid, n, first in rows:
        b = db.get(BankAccount, bid)
        out.append(
            CheckIssue(
                f"{b.label}: {n} giao dịch sao kê chưa khớp "
                f"từ ngày {fmt_date(first)}",
                "warning",
                f"/bank/statements?bank_account_id={bid}",
                first,
            )
        )
    return out


@data_check(
    code="bank_statement_mismatch",
    title="Số dư sổ tiền gửi khác sao kê",
    perm="bank.view",
    order=36,
)
def check_mismatch(db, ctx):
    out = []
    for b in db.scalars(
        select(BankAccount).where(BankAccount.company_id == ctx.company_id)
    ):
        rec = st.reconciliation(db, ctx.company_id, b)
        if rec.difference:
            out.append(
                CheckIssue(
                    f"{b.label}: số dư sao kê ngày {fmt_date(rec.as_of)} là "
                    f"{format_vnd(rec.statement_balance)}, "
                    f"trên sổ {format_vnd(rec.book_balance)} "
                    f"(lệch {format_vnd(rec.difference)})",
                    "warning",
                    f"/bank/statements?bank_account_id={b.id}",
                    rec.as_of,
                )
            )
    return out
