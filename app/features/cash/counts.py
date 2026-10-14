"""Cash counts (08a-TT).

Counts cash by denomination, compares it with the TK 1111 book
balance and suggests a voucher to settle the difference (it is never
created automatically).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.dates import fmt_date, parse_date, today
from app.core.errors import NotFoundError
from app.core.money import format_vnd
from app.features.cash.models import DENOMINATIONS, CashCount, CashCountLine
from app.features.journal import queries
from app.features.journal.models import POSTED, JournalEntry
from app.features.org.models import Company

CASH = "1111"
SURPLUS_ACCOUNT = "3381"  # surplus assets awaiting resolution
SHORTAGE_ACCOUNT = "1381"  # shortage of assets awaiting resolution
MAX_QUANTITY = 10_000_000
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
TEXT_FIELDS = (
    "accountant_name",
    "cashier_name",
    "other_member_name",
    "other_member_role",
    "responsible_name",
    "difference_reason",
    "conclusion",
)


@dataclass
class CashCountInput:
    count_date: date
    count_time: str
    quantities: dict[int, int]
    accountant_name: str = ""
    cashier_name: str = ""
    other_member_name: str = ""
    other_member_role: str = ""
    responsible_name: str = ""
    difference_reason: str = ""
    conclusion: str = ""

    @property
    def actual_total(self) -> int:
        return sum(d * q for d, q in self.quantities.items())


def parse_form(
    form: dict, company: Company
) -> tuple[CashCountInput | None, dict[str, str]]:
    """Validate the cash count form.

    The total is always recomputed on the server from the quantities.
    """
    errors: dict[str, str] = {}
    count_date = None
    try:
        count_date = parse_date(form.get("count_date"))
        if count_date is None:
            errors["count_date"] = "Nhập ngày kiểm kê"
        elif count_date < company.start_date:
            errors["count_date"] = (
                "Ngày kiểm kê không được trước ngày bắt đầu hạch toán "
                f"{fmt_date(company.start_date)}"
            )
        elif count_date > today():
            errors["count_date"] = "Ngày kiểm kê không được ở tương lai"
    except ValueError:
        errors["count_date"] = "Ngày không hợp lệ"
    count_time = (form.get("count_time") or "").strip()
    if count_time and not _TIME.match(count_time):
        errors["count_time"] = "Giờ không hợp lệ (HH:MM)"
    quantities: dict[int, int] = {}
    for d in DENOMINATIONS:
        raw = (
            str(form.get(f"qty_{d}") or "0")
            .strip()
            .replace(".", "")
            .replace(" ", "")
            or "0"
        )
        try:
            q = int(raw)
            if q < 0 or q > MAX_QUANTITY:
                raise ValueError
            quantities[d] = q
        except ValueError:
            errors[f"qty_{d}"] = "Số tờ phải là số nguyên ≥ 0"
            quantities[d] = 0
    if errors:
        return None, errors
    return CashCountInput(
        count_date=count_date,
        count_time=count_time,
        quantities=quantities,
        **{
            f: (form.get(f) or "").strip()[
                : 120 if f not in ("difference_reason", "conclusion") else 2000
            ]
            for f in TEXT_FIELDS
        },
    ), {}


def book_balance_at(db: Session, company_id: int, d: date) -> int:
    """Return the TK 1111 book balance at the time of the count.

    Includes every posted voucher with a posting date <= ``d``.
    """
    return queries.balance(db, company_id, CASH, upto=d, exact=True)


def next_number(db: Session, company_id: int, year: int) -> str:
    prefix = f"KK{year}-"
    last = 0
    for n in db.scalars(
        select(CashCount.number).where(
            CashCount.company_id == company_id,
            CashCount.number.like(f"{prefix}%"),
        )
    ):
        tail = n[len(prefix) :]
        if tail.isdigit():
            last = max(last, int(tail))
    return f"{prefix}{last + 1:03d}"


def create_cash_count(db: Session, ctx, inp: CashCountInput) -> CashCount:
    book = book_balance_at(db, ctx.company_id, inp.count_date)
    cc = CashCount(
        company_id=ctx.company_id,
        number=next_number(db, ctx.company_id, inp.count_date.year),
        count_date=inp.count_date,
        count_time=inp.count_time,
        book_balance=book,
        actual_total=inp.actual_total,
        created_by=ctx.user_id,
        created_at=datetime.now(timezone.utc),
        **{f: getattr(inp, f) for f in TEXT_FIELDS},
    )
    cc.lines = [
        CashCountLine(company_id=ctx.company_id, denomination=d, quantity=q)
        for d, q in inp.quantities.items()
    ]
    db.add(cc)
    db.flush()
    write_audit(
        db,
        ctx,
        action="cashcount.create",
        entity_type="cash_count",
        entity_id=cc.id,
        summary=(
            f"Kiểm kê quỹ {cc.number} ngày {fmt_date(cc.count_date)}: "
            f"thực tế {format_vnd(cc.actual_total)}, "
            f"sổ {format_vnd(cc.book_balance)}"
        ),
        after={
            "number": cc.number,
            "count_date": cc.count_date,
            "book_balance": cc.book_balance,
            "actual_total": cc.actual_total,
            "quantities": {str(d): q for d, q in inp.quantities.items() if q},
        },
    )
    db.commit()
    return cc


def get_cash_count(db: Session, company_id: int, cc_id: int) -> CashCount:
    cc = db.get(CashCount, cc_id)
    if cc is None or cc.company_id != company_id:
        raise NotFoundError("Không tìm thấy bảng kiểm kê")
    return cc


def list_cash_counts(db: Session, company_id: int) -> list[CashCount]:
    return list(
        db.scalars(
            select(CashCount)
            .where(CashCount.company_id == company_id)
            .order_by(CashCount.count_date.desc(), CashCount.id.desc())
        )
    )


def latest_cash_count(db: Session, company_id: int) -> CashCount | None:
    return db.scalar(
        select(CashCount)
        .where(CashCount.company_id == company_id)
        .order_by(CashCount.count_date.desc(), CashCount.id.desc())
        .limit(1)
    )


def linked_entries(db: Session, cc: CashCount) -> list[JournalEntry]:
    return list(
        db.scalars(
            select(JournalEntry)
            .where(
                JournalEntry.company_id == cc.company_id,
                JournalEntry.source == "cashcount",
                JournalEntry.source_id == cc.id,
            )
            .order_by(JournalEntry.id)
        )
    )


def adjusted_total(db: Session, cc: CashCount) -> int:
    """Return the signed total of posted settlement vouchers.

    Only vouchers linked to the cash count are included.
    """
    rows = db.execute(
        select(
            JournalEntry.doc_type,
            func.coalesce(func.sum(JournalEntry.total), 0),
        )
        .where(
            JournalEntry.company_id == cc.company_id,
            JournalEntry.source == "cashcount",
            JournalEntry.source_id == cc.id,
            JournalEntry.status == POSTED,
        )
        .group_by(JournalEntry.doc_type)
    ).all()
    return sum(int(v) if k == "PT" else -int(v) for k, v in rows)


def remaining_difference(db: Session, cc: CashCount) -> int:
    """Return the difference still to be settled.

    actual - (book balance at the count + posted settlement vouchers).
    Positive means a surplus.
    """
    return cc.actual_total - (cc.book_balance + adjusted_total(db, cc))


@dataclass
class Suggestion:
    kind: str  # PT | PC
    amount: int
    debit: str
    credit: str
    account: str
    reason: str
    url: str


def suggestion(cc: CashCount, remaining: int) -> Suggestion | None:
    """Suggest a voucher that settles the difference.

    The user opens the form, reviews and confirms it themselves.
    """
    if remaining == 0:
        return None
    if remaining > 0:
        kind, account = "PT", SURPLUS_ACCOUNT
        reason = f"Thừa quỹ chưa rõ nguyên nhân theo bảng kiểm kê {cc.number}"
        debit, credit = CASH, account
    else:
        kind, account = "PC", SHORTAGE_ACCOUNT
        reason = f"Thiếu quỹ chưa rõ nguyên nhân theo bảng kiểm kê {cc.number}"
        debit, credit = account, CASH
    amount = abs(remaining)
    params = {
        "kind": kind,
        "account": account,
        "amount": amount,
        "description": reason,
        "cash_count_id": cc.id,
        "contact_name": cc.cashier_name or cc.responsible_name or "",
        "date": cc.count_date.isoformat(),
    }
    return Suggestion(
        kind=kind,
        amount=amount,
        debit=debit,
        credit=credit,
        account=account,
        reason=reason,
        url="/cash/new?" + urlencode(params),
    )
