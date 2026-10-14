"""General ledger queries.

A single source of ledger rows, balances, turnover, detailed ledgers
with running balances, and the negative-balance check.

`ledger_source()` returns a subquery with one row per side of each
journal line (+ opening balances):
  entry_id, date, posting_seq, line_no, line_id,
  side (0 = debit, 1 = credit, -1 = opening), number, doc_type,
  doc_date, description, account, debit, credit, contra, partner_id,
  bank_account_id, item_id, cash_flow
Book order: (date, posting_seq, line_no, side).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import (
    Date,
    Integer,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session

from app.core.db import Money
from app.features.journal.models import (
    POSTED,
    JournalEntry,
    JournalLine,
    OpeningBalance,
)
from app.features.org.models import Company

OPENING_DOC = "OB"
# Opening balance rows are dated "before every period": any reporting
# period starting on or before the accounting start date gets the
# correct opening balance (no transactions exist before the start
# date).
OPENING_SENTINEL = date(1900, 1, 1)


def opening_date(company: Company) -> date:
    """Display date of the opening balances.

    This is the day before the accounting start date.
    """
    return company.start_date - timedelta(days=1)


def ledger_source(
    db: Session,
    company_id: int,
    *,
    include_opening: bool = True,
    name: str = "lr",
):
    company = db.get(Company, company_id)
    je, jl = JournalEntry, JournalLine

    def common():
        return [
            je.id.label("entry_id"),
            je.date.label("date"),
            je.posting_seq.label("posting_seq"),
            jl.line_no.label("line_no"),
            jl.id.label("line_id"),
            je.number.label("number"),
            je.doc_type.label("doc_type"),
            je.doc_date.label("doc_date"),
            func.coalesce(
                func.nullif(jl.description, ""), je.description
            ).label("description"),
        ]

    def tail():
        return [jl.item_id.label("item_id"), jl.cash_flow.label("cash_flow")]

    debit = (
        select(
            *common(),
            literal(0, Integer).label("side"),
            jl.debit_account.label("account"),
            jl.amount.label("debit"),
            cast(literal(0), Money).label("credit"),
            jl.credit_account.label("contra"),
            jl.debit_partner_id.label("partner_id"),
            jl.debit_bank_account_id.label("bank_account_id"),
            *tail(),
        )
        .join_from(jl, je, jl.entry_id == je.id)
        .where(je.company_id == company_id, je.status == POSTED)
    )
    credit = (
        select(
            *common(),
            literal(1, Integer).label("side"),
            jl.credit_account.label("account"),
            cast(literal(0), Money).label("debit"),
            jl.amount.label("credit"),
            jl.debit_account.label("contra"),
            jl.credit_partner_id.label("partner_id"),
            jl.credit_bank_account_id.label("bank_account_id"),
            *tail(),
        )
        .join_from(jl, je, jl.entry_id == je.id)
        .where(je.company_id == company_id, je.status == POSTED)
    )
    parts = [debit, credit]
    if include_opening and company is not None:
        ob = OpeningBalance
        parts.append(
            select(
                cast(null(), Integer).label("entry_id"),
                literal(OPENING_SENTINEL, Date).label("date"),
                literal(0, Integer).label("posting_seq"),
                literal(0, Integer).label("line_no"),
                cast(null(), Integer).label("line_id"),
                cast(null(), String).label("number"),
                literal(OPENING_DOC, String).label("doc_type"),
                cast(null(), Date).label("doc_date"),
                literal("Số dư đầu kỳ", String).label("description"),
                literal(-1, Integer).label("side"),
                ob.account.label("account"),
                ob.debit.label("debit"),
                ob.credit.label("credit"),
                cast(null(), String).label("contra"),
                ob.partner_id.label("partner_id"),
                ob.bank_account_id.label("bank_account_id"),
                ob.item_id.label("item_id"),
                cast(null(), String).label("cash_flow"),
            ).where(ob.company_id == company_id)
        )
    return union_all(*parts).subquery(name)


def book_order(lr):
    return (lr.c.date, lr.c.posting_seq, lr.c.line_no, lr.c.side)


def account_match(col, accounts: str | Sequence[str], exact: bool = False):
    """Match accounts by prefix (111 matches 1111, 1112) or exactly."""
    if isinstance(accounts, str):
        accounts = [accounts]
    conds = [(col == a) if exact else col.like(f"{a}%") for a in accounts]
    return or_(*conds) if len(conds) > 1 else conds[0]


def _dims(lr, partner_id=None, bank_account_id=None, item_id=None):
    conds = []
    if partner_id is not None:
        conds.append(lr.c.partner_id == partner_id)
    if bank_account_id is not None:
        conds.append(lr.c.bank_account_id == bank_account_id)
    if item_id is not None:
        conds.append(lr.c.item_id == item_id)
    return conds


# ----------------------------------------------- balances and turnover


@dataclass
class Sums:
    debit: int = 0
    credit: int = 0

    @property
    def net(self) -> int:
        return self.debit - self.credit


def sums(
    db: Session,
    company_id: int,
    accounts,
    *,
    start: date | None = None,
    end: date | None = None,
    exact: bool = False,
    partner_id=None,
    bank_account_id=None,
    item_id=None,
    include_opening: bool = True,
    contra=None,
) -> Sums:
    """Debit/credit totals of ledger rows in [start, end].

    None means no limit.
    """
    lr = ledger_source(db, company_id, include_opening=include_opening)
    conds = [
        account_match(lr.c.account, accounts, exact),
        *_dims(lr, partner_id, bank_account_id, item_id),
    ]
    if start is not None:
        conds.append(lr.c.date >= start)
    if end is not None:
        conds.append(lr.c.date <= end)
    if contra is not None:
        conds.append(account_match(lr.c.contra, contra))
    d, c = db.execute(
        select(
            func.coalesce(func.sum(lr.c.debit), 0),
            func.coalesce(func.sum(lr.c.credit), 0),
        ).where(*conds)
    ).one()
    return Sums(int(d), int(c))


def balance(
    db: Session,
    company_id: int,
    accounts,
    *,
    upto: date | None = None,
    before: date | None = None,
    **kw,
) -> int:
    """Debit − credit balance up to `upto` (or before `before`)."""
    end = (
        upto
        if upto is not None
        else (before - timedelta(days=1) if before is not None else None)
    )
    return sums(db, company_id, accounts, end=end, **kw).net


def turnover(
    db: Session, company_id: int, accounts, start: date, end: date, **kw
) -> Sums:
    """Turnover in the period (excluding opening balances)."""
    return sums(
        db,
        company_id,
        accounts,
        start=start,
        end=end,
        include_opening=False,
        **kw,
    )


@dataclass
class AccountSums:
    account: str
    partner_id: int | None
    bank_account_id: int | None
    opening: int  # debit − credit before the period
    debit: int
    credit: int

    @property
    def closing(self) -> int:
        return self.opening + self.debit - self.credit


def account_sums(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    *,
    accounts=None,
    by_partner: bool = False,
    by_bank: bool = False,
) -> list[AccountSums]:
    """Opening balance + period turnover per detail account.

    Optionally broken down by partner / bank account.
    """
    lr = ledger_source(db, company_id)
    keys = [lr.c.account]
    if by_partner:
        keys.append(lr.c.partner_id)
    if by_bank:
        keys.append(lr.c.bank_account_id)
    in_period = and_(lr.c.date >= start, lr.c.date <= end)
    q = (
        select(
            *keys,
            func.coalesce(
                func.sum(
                    case(
                        (lr.c.date < start, lr.c.debit - lr.c.credit), else_=0
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(case((in_period, lr.c.debit), else_=0)), 0),
            func.coalesce(
                func.sum(case((in_period, lr.c.credit), else_=0)), 0
            ),
        )
        .where(lr.c.date <= end)
        .group_by(*keys)
        .order_by(*keys)
    )
    if accounts:
        q = q.where(account_match(lr.c.account, accounts))
    out = []
    for row in db.execute(q):
        acc = row[0]
        pid = row[1] if by_partner else None
        bid = row[1 + int(by_partner)] if by_bank else None
        opening, d, c = (int(x) for x in row[-3:])
        if opening == 0 and d == 0 and c == 0:
            continue
        out.append(AccountSums(acc, pid, bid, opening, d, c))
    return out


# ----------------------------------- detailed ledger with running total


@dataclass
class LedgerRow:
    entry_id: int | None
    date: date
    doc_date: date | None
    number: str | None
    doc_type: str
    description: str
    account: str
    contra: str | None
    debit: int
    credit: int
    balance: int
    # Row number in the book (counted from the start of the book,
    # excluding opening rows).
    line_no: int
    partner_id: int | None = None
    bank_account_id: int | None = None
    item_id: int | None = None
    cash_flow: str | None = None
    posting_seq: int | None = None


@dataclass
class AccountLedger:
    accounts: tuple[str, ...]
    start: date
    end: date
    opening: int
    rows: list[LedgerRow] = field(default_factory=list)
    total_debit: int = 0
    total_credit: int = 0

    @property
    def closing(self) -> int:
        return self.opening + self.total_debit - self.total_credit


def account_ledger(
    db: Session,
    company_id: int,
    accounts,
    start: date,
    end: date,
    *,
    exact: bool = False,
    partner_id=None,
    bank_account_id=None,
    item_id=None,
) -> AccountLedger:
    """Detailed ledger of the account(s) in [start, end].

    The opening balance is the balance at `start`; each row carries a
    running balance.
    """
    lr = ledger_source(db, company_id)
    base = [
        account_match(lr.c.account, accounts, exact),
        *_dims(lr, partner_id, bank_account_id, item_id),
    ]
    order = book_order(lr)
    w = (
        select(
            lr,
            func.sum(lr.c.debit - lr.c.credit)
            .over(order_by=order, rows=(None, 0))
            .label("cum"),
            func.sum(case((lr.c.side >= 0, 1), else_=0))
            .over(order_by=order, rows=(None, 0))
            .label("n"),
        )
        .where(*base)
        .subquery("w")
    )
    opening = int(
        db.scalar(
            select(func.coalesce(func.sum(lr.c.debit - lr.c.credit), 0)).where(
                *base, lr.c.date < start
            )
        )
        or 0
    )
    rows = db.execute(
        select(w)
        .where(w.c.date >= start, w.c.date <= end, w.c.side >= 0)
        .order_by(w.c.date, w.c.posting_seq, w.c.line_no, w.c.side)
    ).all()
    accs = (accounts,) if isinstance(accounts, str) else tuple(accounts)
    out = AccountLedger(accounts=accs, start=start, end=end, opening=opening)
    for r in rows:
        out.rows.append(
            LedgerRow(
                entry_id=r.entry_id,
                date=r.date,
                doc_date=r.doc_date,
                number=r.number,
                doc_type=r.doc_type,
                description=r.description or "",
                account=r.account,
                contra=r.contra,
                debit=int(r.debit),
                credit=int(r.credit),
                balance=int(r.cum),
                line_no=int(r.n),
                partner_id=r.partner_id,
                bank_account_id=r.bank_account_id,
                item_id=r.item_id,
                cash_flow=r.cash_flow,
                posting_seq=r.posting_seq,
            )
        )
        out.total_debit += int(r.debit)
        out.total_credit += int(r.credit)
    return out


# ----------------------------------- retroactive negative-balance check


@dataclass
class NegativePoint:
    date: date
    line_no: int
    balance: int
    number: str | None
    entry_id: int | None


def first_negative(
    db: Session,
    company_id: int,
    account: str,
    *,
    bank_account_id=None,
    from_date: date | None = None,
) -> NegativePoint | None:
    """First row (from from_date) whose running balance is negative.

    The balance is per account, and per bank account when given.
    """
    lr = ledger_source(db, company_id)
    conds = [lr.c.account == account]
    if bank_account_id is not None:
        conds.append(lr.c.bank_account_id == bank_account_id)
    order = book_order(lr)
    w = (
        select(
            lr.c.date,
            lr.c.posting_seq,
            lr.c.line_no,
            lr.c.side,
            lr.c.number,
            lr.c.entry_id,
            func.sum(lr.c.debit - lr.c.credit)
            .over(order_by=order, rows=(None, 0))
            .label("cum"),
            func.sum(case((lr.c.side >= 0, 1), else_=0))
            .over(order_by=order, rows=(None, 0))
            .label("n"),
        )
        .where(*conds)
        .subquery("w")
    )
    q = select(w).where(w.c.cum < 0)
    if from_date is not None:
        q = q.where(w.c.date >= from_date)
    row = db.execute(
        q.order_by(w.c.date, w.c.posting_seq, w.c.line_no, w.c.side).limit(1)
    ).first()
    if row is None:
        return None
    return NegativePoint(
        date=row.date,
        line_no=int(row.n),
        balance=int(row.cum),
        number=row.number,
        entry_id=row.entry_id,
    )


def entry_cash_keys(
    db: Session, company_id: int, entries: Iterable[JournalEntry]
) -> set[tuple[str, int | None]]:
    """(no-negative account, bank account) pairs touched by vouchers."""
    from app.features.accounts.models import Account

    no_neg = set(
        db.scalars(
            select(Account.code).where(
                Account.company_id == company_id, Account.no_negative.is_(True)
            )
        )
    )
    keys = set()
    for e in entries:
        for ln in e.lines:
            if ln.debit_account in no_neg:
                keys.add((ln.debit_account, ln.debit_bank_account_id))
            if ln.credit_account in no_neg:
                keys.add((ln.credit_account, ln.credit_bank_account_id))
    return keys
