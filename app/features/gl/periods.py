"""Reporting periods: month / quarter / year / custom.

Used by the ``period_selector`` macro.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.core.dates import fmt_date, month_bounds, parse_date, today

MODES = ("month", "quarter", "year", "custom")


@dataclass(frozen=True)
class ReportPeriod:
    mode: str
    start: date
    end: date
    year: int
    quarter: int | None = None
    month: int | None = None

    @property
    def label(self) -> str:
        """Vietnamese label printed on books and reports."""
        if self.mode == "month":
            return f"Tháng {self.month:02d} năm {self.year}"
        if self.mode == "quarter":
            return f"Quý {self.quarter} năm {self.year}"
        if self.mode == "year":
            return f"Năm {self.year}"
        return f"Từ ngày {fmt_date(self.start)} đến ngày {fmt_date(self.end)}"

    @property
    def range_label(self) -> str:
        return f"Từ ngày {fmt_date(self.start)} đến ngày {fmt_date(self.end)}"

    @property
    def key(self) -> str:
        if self.mode == "month":
            return f"{self.year}-{self.month:02d}"
        if self.mode == "quarter":
            return f"{self.year}-Q{self.quarter}"
        if self.mode == "year":
            return f"{self.year}"
        return f"{self.start.isoformat()}_{self.end.isoformat()}"

    def query(self) -> str:
        """Query string that keeps the period across pages/exports."""
        if self.mode == "month":
            return f"mode=month&month={self.year}-{self.month:02d}"
        if self.mode == "quarter":
            return f"mode=quarter&quarter={self.quarter}&year={self.year}"
        if self.mode == "year":
            return f"mode=year&year={self.year}"
        return (
            f"mode=custom&start={self.start.isoformat()}"
            f"&end={self.end.isoformat()}"
        )

    def previous_year(self) -> ReportPeriod:
        """Same period of the previous year.

        Used for the comparative column of the financial statements.
        """

        def shift(d: date) -> date:
            try:
                return d.replace(year=d.year - 1)
            except ValueError:  # 29/02
                return d.replace(year=d.year - 1, day=28)

        return ReportPeriod(
            self.mode,
            shift(self.start),
            shift(self.end),
            self.year - 1,
            self.quarter,
            self.month,
        )


def quarter_bounds(year: int, q: int) -> tuple[date, date]:
    start = date(year, 3 * (q - 1) + 1, 1)
    end = month_bounds(year, 3 * q)[1]
    return start, end


def make(
    mode: str,
    year: int,
    *,
    month: int | None = None,
    quarter: int | None = None,
    start: date | None = None,
    end: date | None = None,
) -> ReportPeriod:
    if mode == "month":
        s, e = month_bounds(year, month)
        return ReportPeriod("month", s, e, year, (month - 1) // 3 + 1, month)
    if mode == "quarter":
        s, e = quarter_bounds(year, quarter)
        return ReportPeriod("quarter", s, e, year, quarter)
    if mode == "year":
        return ReportPeriod("year", date(year, 1, 1), date(year, 12, 31), year)
    if start is None or end is None:
        raise ValueError("custom period needs start and end")
    if end < start:
        start, end = end, start
    return ReportPeriod("custom", start, end, start.year)


def resolve(params, default_mode: str = "year") -> ReportPeriod:
    """Read the period from query parameters.

    Parameters: mode, month=YYYY-MM, quarter, year, start, end.
    Invalid values fall back to the default period.
    """
    t = today()
    mode = params.get("mode") or default_mode
    if mode not in MODES:
        mode = default_mode
    try:
        year = int(params.get("year") or t.year)
        if not 1900 < year < 3000:
            raise ValueError
    except (TypeError, ValueError):
        year = t.year
    try:
        if mode == "month":
            raw = params.get("month")
            if raw:
                y, m = (int(x) for x in str(raw).split("-")[:2])
            else:
                y, m = t.year, t.month
            if not 1 <= m <= 12:
                raise ValueError
            return make("month", y, month=m)
        if mode == "quarter":
            q = int(params.get("quarter") or ((t.month - 1) // 3 + 1))
            if not 1 <= q <= 4:
                raise ValueError
            return make("quarter", year, quarter=q)
        if mode == "year":
            return make("year", year)
        s = parse_date(params.get("start")) or date(t.year, 1, 1)
        e = parse_date(params.get("end")) or t
        return make("custom", year, start=s, end=e)
    except (TypeError, ValueError):
        return (
            make("year", t.year)
            if default_mode == "year"
            else make("month", t.year, month=t.month)
        )


def day_before(d: date) -> date:
    return d - timedelta(days=1)
