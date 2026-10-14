"""General accounting books and shared balance queries.

Covers S03a-DNN (general journal), S03b-DNN (general ledger), account
detail ledgers, F01-DNN (trial balance) and the balance queries shared
by the financial statements.

All figures are read from `journal.queries.ledger_source` (posted
documents ∪ opening balances).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.features.accounts.models import Account
from app.features.journal import queries
from app.features.journal.queries import book_order, ledger_source
from app.features.org.models import Company

CLOSING_DOC = "KC"


def accounts_map(db: Session, company_id: int) -> dict[str, Account]:
    return {
        a.code: a
        for a in db.scalars(
            select(Account).where(Account.company_id == company_id)
        )
    }


def balances_at(
    db: Session, company_id: int, d: date, *, by_partner: bool = False
) -> dict[tuple, int]:
    """Return debit − credit balances up to and including day `d`.

    Keyed by (account, partner); partner is None unless `by_partner`.
    """
    lr = ledger_source(db, company_id)
    keys = [lr.c.account] + ([lr.c.partner_id] if by_partner else [])
    rows = db.execute(
        select(*keys, func.coalesce(func.sum(lr.c.debit - lr.c.credit), 0))
        .where(lr.c.date <= d)
        .group_by(*keys)
    ).all()
    out = {}
    for r in rows:
        k = (r[0], r[1] if by_partner else None)
        if int(r[-1]):
            out[k] = int(r[-1])
    return out


def flows(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    *,
    exclude_closing: bool = True,
) -> dict[str, tuple[int, int]]:
    """Return debit/credit turnover per account for the period.

    Closing (transfer) documents are excluded by default.
    """
    lr = ledger_source(db, company_id, include_opening=False)
    conds = [lr.c.date >= start, lr.c.date <= end]
    if exclude_closing:
        conds.append(lr.c.doc_type != CLOSING_DOC)
    rows = db.execute(
        select(
            lr.c.account,
            func.coalesce(func.sum(lr.c.debit), 0),
            func.coalesce(func.sum(lr.c.credit), 0),
        )
        .where(*conds)
        .group_by(lr.c.account)
    ).all()
    return {a: (int(d), int(c)) for a, d, c in rows}


def sum_prefix(values: dict, prefixes, *, key_index: int | None = None) -> int:
    """Sum values whose account code starts with one of `prefixes`."""
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    total = 0
    for k, v in values.items():
        acc = k[key_index] if key_index is not None else k
        if any(acc.startswith(p) for p in prefixes):
            total += v
    return total


# ------------------------------------------- F01-DNN Trial balance


@dataclass
class TBRow:
    code: str
    name: str
    level: int
    is_postable: bool
    open_d: int = 0
    open_c: int = 0
    per_d: int = 0
    per_c: int = 0
    close_d: int = 0
    close_c: int = 0

    def add(self, other: TBRow) -> None:
        for f in ("open_d", "open_c", "per_d", "per_c", "close_d", "close_c"):
            setattr(self, f, getattr(self, f) + getattr(other, f))

    @property
    def is_zero(self) -> bool:
        return not any(
            (
                self.open_d,
                self.open_c,
                self.per_d,
                self.per_c,
                self.close_d,
                self.close_c,
            )
        )


@dataclass
class TrialBalance:
    start: date
    end: date
    rows: list[TBRow]
    total: TBRow

    @property
    def balanced(self) -> bool:
        t = self.total
        return (
            t.open_d == t.open_c
            and t.per_d == t.per_c
            and t.close_d == t.close_c
        )


def _split(net: int) -> tuple[int, int]:
    return (net, 0) if net >= 0 else (0, -net)


def trial_balance(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    *,
    level: int = 2,
    show_zero: bool = False,
) -> TrialBalance:
    """Build the F01-DNN trial balance.

    For dual-nature accounts tracked by partner, balances are presented
    per partner on both the debit and the credit side.
    """
    accs = accounts_map(db, company_id)
    sums = queries.account_sums(db, company_id, start, end, by_partner=True)
    leaf: dict[str, TBRow] = {}
    for s in sums:
        a = accs.get(s.account)
        row = leaf.setdefault(
            s.account,
            TBRow(
                s.account,
                a.name if a else s.account,
                a.level if a else 1,
                True,
            ),
        )
        od, oc = _split(s.opening)
        cd, cc = _split(s.closing)
        row.open_d += od
        row.open_c += oc
        row.per_d += s.debit
        row.per_c += s.credit
        row.close_d += cd
        row.close_c += cc
    # Accounts not tracked by partner (or with a one-sided normal
    # balance): present the net amount.
    for code, row in leaf.items():
        a = accs.get(code)
        if a is None or a.partner_mode == "none" or a.normal != "both":
            row.open_d, row.open_c = _split(row.open_d - row.open_c)
            row.close_d, row.close_c = _split(row.close_d - row.close_c)
    # Roll totals up into parent accounts.
    rows: dict[str, TBRow] = {}
    for code, row in leaf.items():
        rows[code] = row
        a = accs.get(code)
        parent = a.parent_code if a else None
        while parent:
            pa = accs.get(parent)
            prow = rows.get(parent)
            if prow is None:
                prow = rows[parent] = TBRow(
                    parent,
                    pa.name if pa else parent,
                    pa.level if pa else 1,
                    False,
                )
            prow.add(row)
            parent = pa.parent_code if pa else None
    if show_zero:
        for code, a in accs.items():
            if a.is_active and code not in rows:
                rows[code] = TBRow(code, a.name, a.level, a.is_postable)
    out = sorted(
        (
            r
            for r in rows.values()
            if r.level <= level and (show_zero or not r.is_zero)
        ),
        key=lambda r: r.code,
    )
    total = TBRow("", "Tổng cộng", 0, False)
    for r in rows.values():
        if r.level == 1:
            total.add(r)
    return TrialBalance(start, end, out, total)


# ----------------------------------------- S03a-DNN General journal


@dataclass
class JournalRow:
    stt: int
    date: date
    number: str | None
    doc_date: date | None
    description: str
    account: str
    debit: int
    credit: int
    entry_id: int
    new_entry: bool  # first line of a document (doc number in bold)
    doc_type: str = ""


@dataclass
class JournalBook:
    start: date
    end: date
    rows: list[JournalRow] = field(default_factory=list)
    total_debit: int = 0
    total_credit: int = 0
    # (line_id, side) -> row number, referenced by the general ledger
    index: dict = field(default_factory=dict)


# ---------------------------------------------- Payroll data masking
# Users without the payroll permission (payroll.view) can still view
# the general books but not individual salaries: lines of payroll
# posting documents (LG) and of any document touching TK 334/3335
# (salary payments, PIT payments...) are grouped by (document,
# account, debit/credit side), hiding partner and description.
# Totals are unchanged.
PAYROLL_DOC = "LG"
PAYROLL_ACCOUNTS = ("334", "3335")
PAYROLL_HIDDEN = "Chi tiết ẩn – cần quyền xem lương"


def payroll_label(d: date) -> str:
    return f"Hạch toán lương tháng {d.month:02d}/{d.year} (chi tiết ẩn)"


def _is_payroll_account(code: str | None) -> bool:
    return bool(code) and code.startswith(PAYROLL_ACCOUNTS)


def mask_journal_rows(rows: list[JournalRow]) -> list[JournalRow]:
    sensitive = {
        r.entry_id
        for r in rows
        if r.doc_type == PAYROLL_DOC or _is_payroll_account(r.account)
    }
    out: list[JournalRow] = []
    groups: dict[tuple, JournalRow] = {}
    for r in rows:
        if r.entry_id not in sensitive:
            out.append(r)
            continue
        key = (r.entry_id, r.account, 0 if r.debit else 1)
        g = groups.get(key)
        if g is None:
            label = (
                payroll_label(r.date)
                if r.doc_type == PAYROLL_DOC
                else PAYROLL_HIDDEN
            )
            g = JournalRow(
                stt=0,
                date=r.date,
                number=r.number,
                doc_date=r.doc_date,
                description=label,
                account=r.account,
                debit=0,
                credit=0,
                entry_id=r.entry_id,
                new_entry=False,
                doc_type=r.doc_type,
            )
            groups[key] = g
            out.append(g)
        g.debit += r.debit
        g.credit += r.credit
    last = None
    for n, r in enumerate(out, start=1):
        r.stt = n
        r.new_entry = r.entry_id != last
        last = r.entry_id
    return out


def mask_ledger_rows(rows: list, opening: int, account: str) -> list:
    """Group/hide payroll-related detail ledger rows.

    Works on `queries.LedgerRow` items and recomputes the running
    balance.
    """
    from copy import copy

    sensitive_book = _is_payroll_account(account)
    out: list = []
    groups: dict[tuple, object] = {}
    for r in rows:
        if (
            r.doc_type == PAYROLL_DOC
            or sensitive_book
            or _is_payroll_account(r.contra)
        ):
            key = (r.entry_id, r.contra, 0 if r.debit else 1)
            g = groups.get(key)
            if g is None:
                g = copy(r)
                g.debit, g.credit = 0, 0
                g.partner_id = None
                g.description = (
                    payroll_label(r.date)
                    if r.doc_type == PAYROLL_DOC
                    else PAYROLL_HIDDEN
                )
                groups[key] = g
                out.append(g)
            g.debit += r.debit
            g.credit += r.credit
        else:
            out.append(r)
    running = opening
    for r in out:
        running += r.debit - r.credit
        r.balance = running
    return out


def general_journal(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    *,
    mask_payroll: bool = False,
) -> JournalBook:
    lr = ledger_source(db, company_id, include_opening=False)
    rows = db.execute(
        select(lr)
        .where(lr.c.date >= start, lr.c.date <= end)
        .order_by(*book_order(lr))
    ).all()
    book = JournalBook(start, end)
    last_entry = None
    for n, r in enumerate(rows, start=1):
        book.rows.append(
            JournalRow(
                stt=n,
                date=r.date,
                number=r.number,
                doc_date=r.doc_date,
                description=r.description or "",
                account=r.account,
                debit=int(r.debit),
                credit=int(r.credit),
                entry_id=r.entry_id,
                new_entry=r.entry_id != last_entry,
                doc_type=r.doc_type or "",
            )
        )
        book.index[(r.line_id, r.side)] = n
        last_entry = r.entry_id
        book.total_debit += int(r.debit)
        book.total_credit += int(r.credit)
    if mask_payroll:
        book.rows = mask_journal_rows(book.rows)
        book.index = {}
    return book


# --------------------- S03b-DNN General ledger, account detail ledger


@dataclass
class LedgerBook:
    account: str
    account_name: str
    start: date
    end: date
    opening: int
    rows: list = field(default_factory=list)  # queries.LedgerRow + .nkc_stt
    total_debit: int = 0
    total_credit: int = 0
    ytd_debit: int = 0
    ytd_credit: int = 0
    partner_name: str | None = None
    bank_label: str | None = None

    @property
    def closing(self) -> int:
        return self.opening + self.total_debit - self.total_credit

    @property
    def opening_split(self) -> tuple[int, int]:
        return _split(self.opening)

    @property
    def closing_split(self) -> tuple[int, int]:
        return _split(self.closing)


def ledger_book(
    db: Session,
    company_id: int,
    account: str,
    start: date,
    end: date,
    *,
    exact: bool = False,
    partner_id=None,
    bank_account_id=None,
    with_nkc: bool = False,
    mask_payroll: bool = False,
) -> LedgerBook:
    accs = accounts_map(db, company_id)
    led = queries.account_ledger(
        db,
        company_id,
        account,
        start,
        end,
        exact=exact,
        partner_id=partner_id,
        bank_account_id=bank_account_id,
    )
    a = accs.get(account)
    rows = (
        mask_ledger_rows(led.rows, led.opening, account)
        if mask_payroll
        else led.rows
    )
    book = LedgerBook(
        account=account,
        account_name=a.name if a else account,
        start=start,
        end=end,
        opening=led.opening,
        rows=rows,
        total_debit=led.total_debit,
        total_credit=led.total_credit,
    )
    if with_nkc and book.rows:
        stt = journal_line_numbers(
            db, company_id, start, end, mask_payroll=mask_payroll
        )
        for row in book.rows:
            side = 0 if row.debit else 1
            row.nkc_stt = stt.get(
                (row.entry_id, row.account, side, row.debit or row.credit)
            )
    # Year-to-date totals as of the period end date
    ystart = date(end.year, 1, 1)
    company = db.get(Company, company_id)
    ystart = max(ystart, company.start_date) if company else ystart
    s = queries.turnover(
        db,
        company_id,
        account,
        ystart,
        end,
        exact=exact,
        partner_id=partner_id,
        bank_account_id=bank_account_id,
    )
    book.ytd_debit, book.ytd_credit = s.debit, s.credit
    return book


def journal_line_numbers(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    *,
    mask_payroll: bool = False,
) -> dict[tuple, int]:
    """Map (entry_id, account, side, amount) to general journal row no.

    Used for column G of the general ledger (S03b-DNN).
    """
    book = general_journal(
        db, company_id, start, end, mask_payroll=mask_payroll
    )
    out: dict[tuple, int] = {}
    for r in book.rows:
        key = (r.entry_id, r.account, 0 if r.debit else 1, r.debit or r.credit)
        out.setdefault(key, r.stt)
    return out


def entry_count_by_account(
    db: Session, company_id: int, start: date, end: date
) -> dict[str, int]:
    lr = ledger_source(db, company_id, include_opening=False)
    rows = db.execute(
        select(lr.c.account, func.count())
        .where(lr.c.date >= start, lr.c.date <= end)
        .group_by(lr.c.account)
    ).all()
    return {a: int(n) for a, n in rows}


def pl_balances(db: Session, company_id: int, d: date) -> dict[str, int]:
    """Return (debit − credit) balances of class 5–9 accounts at `d`.

    A non-zero balance means the account has not been closed yet.
    """
    bal = balances_at(db, company_id, d)
    return {k[0]: v for k, v in bal.items() if k[0][:1] in "56789" and v}
