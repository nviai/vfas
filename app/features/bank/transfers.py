"""Internal fund transfers (CK).

Covers withdrawing deposits to the cash fund, depositing cash into
a bank, and transfers between two bank accounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.core.dates import parse_date, today
from app.core.money import MoneyError, parse_vnd
from app.features.bank.models import BankAccount
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.org.models import Company

CASH = "1111"


@dataclass
class TransferIn:
    date: date
    doc_date: date | None
    source: str  # "cash" | "bank:<id>"
    target: str
    amount: int
    description: str
    ref_no: str = ""


def _side(
    db: Session, company_id: int, ref: str
) -> tuple[str, int | None, str] | None:
    """Return (account, bank account id, label) of a transfer side."""
    if ref == "cash":
        return CASH, None, "Tiền mặt (1111)"
    if ref.startswith("bank:") and ref[5:].isdigit():
        b = db.get(BankAccount, int(ref[5:]))
        if b is not None and b.company_id == company_id:
            return b.account_code, b.id, b.label
    return None


def parse_form(
    db: Session, form, company: Company
) -> tuple[TransferIn | None, dict[str, str]]:
    errors: dict[str, str] = {}
    d = doc_date = None
    try:
        d = parse_date(form.get("date"))
        if d is None:
            errors["date"] = "Nhập ngày hạch toán"
        elif d > today():
            errors["date"] = "Chuyển tiền không được ghi ở ngày tương lai"
        elif d < company.start_date:
            errors["date"] = "Ngày hạch toán trước ngày bắt đầu hạch toán"
        doc_date = parse_date(form.get("doc_date")) or d
    except ValueError:
        errors["date"] = "Ngày không hợp lệ"
    source, target = form.get("source") or "", form.get("target") or ""
    if _side(db, company.id, source) is None:
        errors["source"] = "Chọn nơi chuyển đi"
    if _side(db, company.id, target) is None:
        errors["target"] = "Chọn nơi nhận"
    if source and source == target:
        errors["target"] = "Nơi nhận phải khác nơi chuyển"
    amount = 0
    try:
        amount = parse_vnd(form.get("amount"))
        if amount <= 0:
            errors["amount"] = "Số tiền phải lớn hơn 0"
    except MoneyError as e:
        errors["amount"] = str(e)
    description = (form.get("description") or "").strip()
    if not description:
        errors["description"] = "Nhập diễn giải"
    if errors:
        return None, errors
    return TransferIn(
        date=d,
        doc_date=doc_date,
        source=source,
        target=target,
        amount=amount,
        description=description[:500],
        ref_no=(form.get("ref_no") or "").strip()[:100],
    ), {}


def to_entry(db: Session, company_id: int, t: TransferIn) -> js.EntryIn:
    src = _side(db, company_id, t.source)
    dst = _side(db, company_id, t.target)
    line = js.LineIn(
        debit_account=dst[0],
        credit_account=src[0],
        amount=t.amount,
        description=t.description,
        debit_bank_account_id=dst[1],
        credit_bank_account_id=src[1],
    )
    return js.EntryIn(
        doc_type="CK",
        date=t.date,
        doc_date=t.doc_date,
        lines=[line],
        description=t.description,
        ref_no=t.ref_no,
        source="bank",
        meta={"form": "transfer"},
    )


def save(
    db: Session, ctx, t: TransferIn, entry: JournalEntry | None = None
) -> JournalEntry:
    inp = to_entry(db, ctx.company_id, t)
    if entry is None:
        return js.create_entry(db, ctx, inp)
    return js.update_draft(db, ctx, entry, inp)


def values(e: JournalEntry) -> dict:
    ln = e.lines[0] if e.lines else None

    def ref(account, bank_id):
        if account == CASH:
            return "cash"
        return f"bank:{bank_id}" if bank_id else ""

    return {
        "date": e.date,
        "doc_date": e.doc_date,
        "description": e.description,
        "ref_no": e.ref_no,
        "amount": ln.amount if ln else None,
        "source": ref(ln.credit_account, ln.credit_bank_account_id)
        if ln
        else "",
        "target": ref(ln.debit_account, ln.debit_bank_account_id)
        if ln
        else "",
    }


def options(db: Session, company_id: int) -> list[tuple[str, str]]:
    from app.features.bank.service import list_bank_accounts

    out = [("cash", "Tiền mặt (1111)")]
    out += [
        (f"bank:{b.id}", f"{b.label} · {b.bank_name}")
        for b in list_bank_accounts(db, company_id)
    ]
    return out
