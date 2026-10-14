"""Cash and bank books.

S04a-DNN (cash book, one row per voucher), S04b-DNN (detailed by
offsetting account) and S05-DNN (bank deposits).

Built from the single ledger source (journal.queries.account_ledger):
the opening balance is exact at the start date and the running
balance follows (posting date, posting sequence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.journal import queries
from app.features.journal.models import JournalEntry

FORMS = {
    "s04a": ("S04a-DNN", "SỔ QUỸ TIỀN MẶT"),
    "s04b": ("S04b-DNN", "SỔ KẾ TOÁN CHI TIẾT QUỸ TIỀN MẶT"),
    "s05": ("S05-DNN", "SỔ TIỀN GỬI NGÂN HÀNG"),
}


@dataclass
class BookRow:
    post_date: date
    doc_date: date | None
    receipt_no: str
    payment_no: str
    description: str
    contra: str
    receipt: int
    payment: int
    balance: int
    note: str = ""
    entry_id: int | None = None
    doc_type: str = ""

    @property
    def number(self) -> str:
        return self.receipt_no or self.payment_no

    @property
    def url(self) -> str:
        from app.core.registry import doc_url

        return doc_url(self.doc_type, self.entry_id) if self.entry_id else ""


@dataclass
class MoneyBook:
    form: str
    account: str
    start: date
    end: date
    opening: int
    rows: list[BookRow] = field(default_factory=list)
    total_in: int = 0
    total_out: int = 0
    bank_account_id: int | None = None

    @property
    def closing(self) -> int:
        return self.opening + self.total_in - self.total_out

    @property
    def code(self) -> str:
        return FORMS[self.form][0]

    @property
    def title(self) -> str:
        return FORMS[self.form][1]


def _notes(db: Session, entry_ids: set[int]) -> dict[int, str]:
    if not entry_ids:
        return {}
    out = {}
    rows = db.execute(
        select(
            JournalEntry.id, JournalEntry.reverses_id, JournalEntry.meta
        ).where(JournalEntry.id.in_(entry_ids))
    ).all()
    rev_ids = {r.reverses_id for r in rows if r.reverses_id}
    numbers = (
        dict(
            db.execute(
                select(JournalEntry.id, JournalEntry.number).where(
                    JournalEntry.id.in_(rev_ids)
                )
            ).all()
        )
        if rev_ids
        else {}
    )
    for eid, rev, meta in rows:
        parts = []
        if rev:
            reversed_ref = (
                numbers.get(rev)
                or (meta or {}).get("reverses")
                or ("#" + str(rev))
            )
            parts.append(f"Điều chỉnh cho {reversed_ref}")
        note = (meta or {}).get("note")
        if note:
            parts.append(str(note))
        if parts:
            out[eid] = "; ".join(parts)
    return out


def money_book(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    *,
    form: str = "s04a",
    account: str = "1111",
    bank_account_id: int | None = None,
) -> MoneyBook:
    """Build the money book of an account for [start, end].

    Detailed by bank account when ``bank_account_id`` is given.
    """
    led = queries.account_ledger(
        db,
        company_id,
        account,
        start,
        end,
        exact=True,
        bank_account_id=bank_account_id,
    )
    book = MoneyBook(
        form=form,
        account=account,
        start=start,
        end=end,
        opening=led.opening,
        bank_account_id=bank_account_id,
    )
    notes = _notes(db, {r.entry_id for r in led.rows if r.entry_id})
    grouped = form == "s04a"
    for r in led.rows:
        is_in = r.debit > 0
        amount = r.debit if is_in else r.credit
        if grouped and book.rows:
            last = book.rows[-1]
            same = last.entry_id == r.entry_id and (
                (last.receipt > 0) == is_in
            )
            if same:
                if is_in:
                    last.receipt += amount
                else:
                    last.payment += amount
                last.balance = r.balance
                if r.contra and r.contra not in last.contra.split(", "):
                    last.contra = f"{last.contra}, {r.contra}"
                continue
        book.rows.append(
            BookRow(
                post_date=r.date,
                doc_date=r.doc_date,
                receipt_no=(r.number or "") if is_in else "",
                payment_no="" if is_in else (r.number or ""),
                description=(r.description or ""),
                contra=r.contra or "",
                receipt=amount if is_in else 0,
                payment=0 if is_in else amount,
                balance=r.balance,
                note=notes.get(r.entry_id or 0, ""),
                entry_id=r.entry_id,
                doc_type=r.doc_type,
            )
        )
    book.total_in = led.total_debit
    book.total_out = led.total_credit
    # S04a: the description is the voucher reason, not the
    # description of each line
    if grouped and book.rows:
        desc = dict(
            db.execute(
                select(JournalEntry.id, JournalEntry.description).where(
                    JournalEntry.id.in_(
                        {r.entry_id for r in book.rows if r.entry_id}
                    )
                )
            ).all()
        )
        for row in book.rows:
            if row.entry_id and desc.get(row.entry_id):
                row.description = desc[row.entry_id]
    return book
