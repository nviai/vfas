"""Vietnamese-style dates (dd/mm/yyyy) and reporting periods."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def tz() -> ZoneInfo:
    from app.core.config import get_settings

    return ZoneInfo(get_settings().timezone)


def now_local() -> datetime:
    return datetime.now(tz())


def today() -> date:
    return now_local().date()


def fmt_date(d: date | datetime | None) -> str:
    if d is None:
        return ""
    return d.strftime("%d/%m/%Y")


def fmt_datetime(d: datetime | None) -> str:
    if d is None:
        return ""
    # The DB stores UTC without a time zone (SQLite)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    d = d.astimezone(tz())
    return d.strftime("%d/%m/%Y %H:%M")


def parse_date(value: str | date | datetime | None) -> date | None:
    """Accept 'dd/mm/yyyy', 'yyyy-mm-dd' (input type=date) or a date.

    A datetime is converted to its date.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Ngày không hợp lệ: {value}")


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(
        year, month, calendar.monthrange(year, month)[1]
    )


def year_bounds(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def iter_months(start: date, end: date):
    """Yield (year, month) from the month of start to that of end."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


@dataclass(frozen=True)
class Period:
    """Time range selected for viewing a ledger."""

    mode: str  # month | year | all | custom
    start: date
    end: date
    label: str

    @property
    def day_before_start(self) -> date:
        return self.start - timedelta(days=1)


def resolve_period(
    mode: str | None,
    month: str | None,
    year: str | int | None,
    start: str | None,
    end: str | None,
    open_date: date,
) -> Period:
    """Build a Period from query parameters.

    Defaults to the current month.
    """
    t = today()
    mode = mode or "month"
    if mode == "year":
        y = int(year or t.year)
        s, e = year_bounds(y)
        return Period(
            "year",
            max(s, open_date) if y == open_date.year else s,
            e,
            f"Năm {y}",
        )
    if mode == "all":
        return Period(
            "all",
            open_date,
            max(t, open_date),
            f"Từ {fmt_date(open_date)} đến {fmt_date(max(t, open_date))}",
        )
    if mode == "custom":
        s = parse_date(start) or open_date
        e = parse_date(end) or t
        if e < s:
            s, e = e, s
        return Period("custom", s, e, f"Từ {fmt_date(s)} đến {fmt_date(e)}")
    if month:
        y, m = (int(x) for x in month.split("-")[:2])
    else:
        y, m = t.year, t.month
    s, e = month_bounds(y, m)
    return Period("month", s, e, f"Tháng {m:02d}/{y}")
