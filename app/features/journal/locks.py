"""Monthly / yearly period locks per company.

Locks are sequential: a period can only be locked when every earlier
period is locked, and only unlocked when no later period is locked.
This way back-dated postings can never change the balances of a
locked period.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.dates import iter_months, month_bounds, today
from app.core.errors import BusinessError, PeriodLockedError
from app.features.journal.models import PeriodLock
from app.features.org.models import Company

YEAR = 0  # month = 0 means the whole year is locked


def _locks(db: Session, company_id: int) -> dict[tuple[int, int], PeriodLock]:
    rows = db.scalars(
        select(PeriodLock).where(PeriodLock.company_id == company_id)
    )
    return {(r.year, r.month): r for r in rows}


def locked_set(db: Session, company_id: int) -> set[tuple[int, int]]:
    """Return the locked (year, month) pairs; (year, 0) = whole year."""
    return {k for k, r in _locks(db, company_id).items() if r.is_locked}


def month_locked(locks: set[tuple[int, int]], year: int, month: int) -> bool:
    return (year, month) in locks or (year, YEAR) in locks


def is_locked(db: Session, company_id: int, d: date) -> bool:
    return month_locked(locked_set(db, company_id), d.year, d.month)


def any_locked(db: Session, company_id: int) -> bool:
    return bool(locked_set(db, company_id))


def assert_open(
    db: Session, company_id: int, d: date, action: str = "thao tác"
) -> None:
    if is_locked(db, company_id, d):
        raise PeriodLockedError(
            f"Kỳ {d.month:02d}/{d.year} đã khóa sổ, không thể {action}. "
            "Cần Kế toán trưởng/Giám đốc mở khóa (có lý do) hoặc ghi "
            "điều chỉnh vào kỳ đang mở."
        )


def last_locked_month(db: Session, company: Company) -> tuple[int, int] | None:
    locks = locked_set(db, company.id)
    result = None
    for y, m in iter_months(
        company.start_date, max(today(), company.start_date)
    ):
        if month_locked(locks, y, m):
            result = (y, m)
        else:
            break
    return result


def _label(year: int, month: int) -> str:
    return f"năm {year}" if month == YEAR else f"tháng {month:02d}/{year}"


def lock_period(db: Session, ctx, year: int, month: int) -> None:
    """Lock a month (month 1..12) or a whole year (month = 0)."""
    company = db.get(Company, ctx.company_id)
    locks = locked_set(db, company.id)
    start_ym = (company.start_date.year, company.start_date.month)
    if (month == YEAR and year < start_ym[0]) or (
        month != YEAR and (year, month) < start_ym
    ):
        raise BusinessError("Kỳ này trước ngày bắt đầu hạch toán")
    if month == YEAR and (year, YEAR) in locks:
        raise BusinessError(f"Năm {year} đã khóa")
    if month != YEAR and month_locked(locks, year, month):
        raise BusinessError(f"Tháng {month:02d}/{year} đã khóa")
    end = date(year, 12, 31) if month == YEAR else month_bounds(year, month)[1]
    if end >= today():
        raise BusinessError(
            f"Chưa hết {_label(year, month)}, chưa thể khóa sổ"
        )
    period_start = date(year, 1, 1) if month == YEAR else date(year, month, 1)
    for y, m in iter_months(company.start_date, period_start):
        if (y, m) >= (period_start.year, period_start.month):
            break
        if not month_locked(locks, y, m):
            raise BusinessError(
                f"Phải khóa tháng {m:02d}/{y} trước "
                "(khóa sổ theo thứ tự thời gian)"
            )
    row = db.scalar(
        select(PeriodLock).where(
            PeriodLock.company_id == company.id,
            PeriodLock.year == year,
            PeriodLock.month == month,
        )
    )
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(
            PeriodLock(
                company_id=company.id,
                year=year,
                month=month,
                is_locked=True,
                locked_by=ctx.user_id,
                locked_at=now,
            )
        )
    else:
        row.is_locked, row.locked_by, row.locked_at = True, ctx.user_id, now
    write_audit(
        db,
        ctx,
        action="period.lock",
        entity_type="period",
        entity_id=f"{year}-{month:02d}",
        summary=f"Khóa sổ {_label(year, month)}",
    )
    db.commit()


def unlock_period(
    db: Session, ctx, year: int, month: int, reason: str
) -> None:
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("Mở khóa sổ bắt buộc phải nhập lý do")
    locks = locked_set(db, ctx.company_id)
    if month != YEAR and (year, YEAR) in locks:
        raise BusinessError(f"Năm {year} đang khóa – mở khóa năm trước")
    if (year, month) not in locks:
        raise BusinessError(f"{_label(year, month).capitalize()} chưa khóa")
    for y, m in locks:
        if (y, m) == (year, month):
            continue
        later = (
            (y > year)
            if month == YEAR
            else (
                (m == YEAR and y > year)
                or (m != YEAR and (y, m) > (year, month))
            )
        )
        if later:
            raise BusinessError(f"Phải mở khóa {_label(y, m)} trước")
    row = db.scalar(
        select(PeriodLock).where(
            PeriodLock.company_id == ctx.company_id,
            PeriodLock.year == year,
            PeriodLock.month == month,
        )
    )
    row.is_locked = False
    row.unlocked_by = ctx.user_id
    row.unlocked_at = datetime.now(timezone.utc)
    row.unlock_reason = reason
    write_audit(
        db,
        ctx,
        action="period.unlock",
        entity_type="period",
        entity_id=f"{year}-{month:02d}",
        summary=f"Mở khóa sổ {_label(year, month)}",
        reason=reason,
    )
    db.commit()


@dataclass
class MonthCell:
    year: int
    month: int
    locked: bool
    by_year: bool
    ended: bool
    before_start: bool
    can_lock: bool
    can_unlock: bool


def year_grid(db: Session, company: Company, year: int) -> list[MonthCell]:
    """Return the state of the 12 months of a year (lock page)."""
    locks = locked_set(db, company.id)
    last = last_locked_month(db, company)
    cells = []
    later_locked = {(y, m) for (y, m) in locks}
    for m in range(1, 13):
        start, end = month_bounds(year, m)
        before_start = end < company.start_date
        locked = month_locked(locks, year, m)
        if last is None:
            first_open = (company.start_date.year, company.start_date.month)
        else:
            first_open = (
                (last[0], last[1] + 1) if last[1] < 12 else (last[0] + 1, 1)
            )
        can_lock = (
            not locked
            and not before_start
            and end < today()
            and (year, m) == first_open
        )
        can_unlock = (
            locked
            and (year, YEAR) not in locks
            and (year, m) in locks
            and not any(
                (y, mm) > (year, m) for (y, mm) in later_locked if mm != YEAR
            )
            and not any(y > year for (y, mm) in later_locked if mm == YEAR)
        )
        cells.append(
            MonthCell(
                year=year,
                month=m,
                locked=locked,
                by_year=(year, YEAR) in locks,
                ended=end < today(),
                before_start=before_start,
                can_lock=can_lock,
                can_unlock=can_unlock,
            )
        )
    return cells
