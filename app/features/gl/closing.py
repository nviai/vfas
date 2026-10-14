"""Period-end closing (KC vouchers).

Closing transfers the full balance at period end of the revenue, income
and expense accounts (classes 5, 6, 7, 8) to 911, then transfers the
profit/loss 911 <-> 4212. Re-running a period cancels that period's old
KC voucher and creates a new one (only if no later period has been
closed yet). At the start of a year, the previous year's profit is
transferred 4212 -> 4211.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dates import fmt_date
from app.core.errors import BusinessError
from app.features.gl import books
from app.features.gl.periods import ReportPeriod
from app.features.journal import locks
from app.features.journal import service as js
from app.features.journal.models import POSTED, JournalEntry

PL_CLASSES = ("5", "6", "7", "8")
SKIP = ("611", "631")  # periodic inventory method: not closed automatically
KIND_PL = "pl"
KIND_RETAINED = "retained"


@dataclass
class ClosingLine:
    debit: str
    credit: str
    amount: int
    description: str


@dataclass
class ClosingPlan:
    period: ReportPeriod
    lines: list[ClosingLine] = field(default_factory=list)
    profit: int = 0  # > 0 profit, < 0 loss
    revenue: int = 0
    expense: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    existing: JournalEntry | None = None
    later: JournalEntry | None = None

    @property
    def total(self) -> int:
        return sum(ln.amount for ln in self.lines)


def _closing_entries(
    db: Session, company_id: int, kind: str
) -> list[JournalEntry]:
    rows = db.scalars(
        select(JournalEntry)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.doc_type == books.CLOSING_DOC,
            JournalEntry.status == POSTED,
        )
        .order_by(JournalEntry.date)
    )
    return [e for e in rows if (e.meta or {}).get("kind") == kind]


def plan(db: Session, company_id: int, period: ReportPeriod) -> ClosingPlan:
    accs = books.accounts_map(db, company_id)
    p = ClosingPlan(period)
    for e in _closing_entries(db, company_id, KIND_PL):
        if (e.meta or {}).get(
            "closing"
        ) == period.key and e.date == period.end:
            p.existing = e
        elif e.date > period.end:
            p.later = p.later or e
    balances = books.balances_at(db, company_id, period.end)
    # When re-running, exclude this period's existing KC voucher (it
    # will be cancelled).
    if p.existing is not None:
        for ln in p.existing.lines:
            balances[(ln.debit_account, None)] = (
                balances.get((ln.debit_account, None), 0) - ln.amount
            )
            balances[(ln.credit_account, None)] = (
                balances.get((ln.credit_account, None), 0) + ln.amount
            )
    pl = {}
    for (acc, _p), net in balances.items():
        if acc[:1] in PL_CLASSES and net:
            pl[acc] = pl.get(acc, 0) + net
    to_911 = balances.get(("911", None), 0)
    for acc in sorted(pl):
        net = pl[acc]
        if not net:
            continue
        if acc.startswith(SKIP):
            p.skipped[acc] = net
            continue
        name = accs[acc].name if acc in accs else acc
        if net < 0:  # credit balance (revenue, income): Dr acc / Cr 911
            p.lines.append(
                ClosingLine(acc, "911", -net, f"Kết chuyển {name.lower()}")
            )
            p.revenue += -net
        else:  # debit balance (expense): Dr 911 / Cr acc
            p.lines.append(
                ClosingLine("911", acc, net, f"Kết chuyển {name.lower()}")
            )
            p.expense += net
        to_911 += net
    if to_911 < 0:  # credit balance on 911 -> profit
        p.lines.append(ClosingLine("911", "4212", -to_911, "Kết chuyển lãi"))
        p.profit = -to_911
    elif to_911 > 0:
        p.lines.append(ClosingLine("4212", "911", to_911, "Kết chuyển lỗ"))
        p.profit = -to_911
    return p


def run(db: Session, ctx, period: ReportPeriod) -> JournalEntry:
    p = plan(db, ctx.company_id, period)
    if p.later is not None:
        raise BusinessError(
            f"Đã có kết chuyển kỳ sau ({p.later.number} ngày "
            f"{fmt_date(p.later.date)}). "
            "Hủy kết chuyển kỳ sau trước khi chạy lại kỳ này."
        )
    locks.assert_open(db, ctx.company_id, period.end, "kết chuyển")
    if not p.lines:
        raise BusinessError("Không có số dư doanh thu, chi phí để kết chuyển")
    if p.existing is not None:
        js.cancel_entry(db, ctx, p.existing, "Chạy lại kết chuyển cuối kỳ")
    lines = [
        js.LineIn(
            debit_account=ln.debit,
            credit_account=ln.credit,
            amount=ln.amount,
            description=ln.description,
        )
        for ln in p.lines
    ]
    inp = js.EntryIn(
        doc_type=books.CLOSING_DOC,
        date=period.end,
        doc_date=period.end,
        description=(
            f"Kết chuyển xác định kết quả kinh doanh {period.label.lower()}"
        ),
        lines=lines,
        source="closing",
        meta={"closing": period.key, "kind": KIND_PL},
    )
    return js.create_entry(db, ctx, inp, post=True)


@dataclass
class RetainedPlan:
    year: int
    amount: int  # > 0: prior-year profit 4212 -> 4211; < 0: loss
    on: date
    existing: JournalEntry | None = None


def retained_plan(db: Session, company_id: int, year: int) -> RetainedPlan:
    """Plan the transfer of the `year` profit to 4211.

    Takes the 4212 balance on 31/12 and transfers it to 4211 on 01/01
    of the following year.
    """
    end = date(year, 12, 31)
    existing = next(
        (
            e
            for e in _closing_entries(db, company_id, KIND_RETAINED)
            if (e.meta or {}).get("closing") == f"{year}-421"
        ),
        None,
    )
    bal = books.balances_at(db, company_id, end).get(("4212", None), 0)
    return RetainedPlan(
        year=year, amount=-bal, on=date(year + 1, 1, 1), existing=existing
    )


def run_retained(db: Session, ctx, year: int) -> JournalEntry:
    p = retained_plan(db, ctx.company_id, year)
    if p.existing is not None:
        raise BusinessError(
            f"Đã kết chuyển lợi nhuận năm {year} ({p.existing.number}). "
            "Hủy chứng từ đó nếu cần làm lại."
        )
    if not p.amount:
        raise BusinessError(f"TK 4212 không còn số dư tại 31/12/{year}")
    locks.assert_open(db, ctx.company_id, p.on, "kết chuyển")
    line = (
        js.LineIn(
            "4212",
            "4211",
            p.amount,
            f"Kết chuyển lợi nhuận năm {year} sang năm trước",
        )
        if p.amount > 0
        else js.LineIn(
            "4211",
            "4212",
            -p.amount,
            f"Kết chuyển lỗ năm {year} sang năm trước",
        )
    )
    inp = js.EntryIn(
        doc_type=books.CLOSING_DOC,
        date=p.on,
        doc_date=p.on,
        description=f"Kết chuyển lợi nhuận sau thuế chưa phân phối năm {year}",
        lines=[line],
        source="closing",
        meta={"closing": f"{year}-421", "kind": KIND_RETAINED},
    )
    return js.create_entry(db, ctx, inp, post=True)


def record_cit(
    db: Session, ctx, on: date, amount: int, description: str = ""
) -> JournalEntry:
    """Record corporate income tax expense (Dr 821 / Cr 3334).

    Creates a general journal voucher (PK) and posts it immediately.
    """
    if amount <= 0:
        raise BusinessError("Số thuế TNDN phải lớn hơn 0")
    inp = js.EntryIn(
        doc_type="PK",
        date=on,
        doc_date=on,
        description=description
        or f"Chi phí thuế TNDN đến ngày {fmt_date(on)}",
        lines=[
            js.LineIn(
                "821", "3334", amount, "Chi phí thuế thu nhập doanh nghiệp"
            )
        ],
        source="closing",
    )
    return js.create_entry(db, ctx, inp, post=True)


def history(db: Session, company_id: int) -> list[JournalEntry]:
    rows = db.scalars(
        select(JournalEntry)
        .where(
            JournalEntry.company_id == company_id,
            JournalEntry.doc_type == books.CLOSING_DOC,
        )
        .order_by(JournalEntry.date.desc(), JournalEntry.id.desc())
        .limit(50)
    )
    return list(rows)
