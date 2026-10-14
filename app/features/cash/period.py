"""Report period selection for the books.

Supports month, quarter, year, all and custom periods (matches the
`ui.period_selector` macro).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.dates import fmt_date, month_bounds, parse_date, today


@dataclass(frozen=True)
class ReportPeriod:
    mode: str
    start: date
    end: date
    label: str
    year: int | None = None
    quarter: int | None = None

    def tr(self, _) -> str:
        """Return the period label translated to the UI language.

        Printouts always use Vietnamese.
        """
        if self.mode == "month":
            return _(
                "Tháng {m}/{y}", m=f"{self.start.month:02d}", y=self.start.year
            )
        if self.mode == "quarter":
            return _("Quý {q} năm {y}", q=self.quarter, y=self.year)
        if self.mode == "year":
            return _("Năm {y}", y=self.year)
        if self.mode == "all":
            return _("Toàn bộ")
        return _(
            "Từ ngày {s} đến ngày {e}",
            s=fmt_date(self.start),
            e=fmt_date(self.end),
        )

    @property
    def print_label(self) -> str:
        return f"Từ ngày {fmt_date(self.start)} đến ngày {fmt_date(self.end)}"

    def query(self) -> str:
        """Return the query string that rebuilds this period.

        Used for the PDF/Excel export links.
        """
        if self.mode == "month":
            return f"mode=month&month={self.start:%Y-%m}"
        if self.mode == "quarter":
            return f"mode=quarter&quarter={self.quarter}&year={self.year}"
        if self.mode == "year":
            return f"mode=year&year={self.year}"
        if self.mode == "all":
            return "mode=all"
        return (
            f"mode=custom&start={self.start.isoformat()}"
            f"&end={self.end.isoformat()}"
        )


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def resolve(params, floor: date, default_mode: str = "month") -> ReportPeriod:
    """Read the period parameters.

    `floor` is the bookkeeping start date: a period never starts
    before it.
    """
    t = today()
    mode = params.get("mode") or default_mode
    if mode == "quarter":
        y = _int(params.get("year"), t.year)
        q = min(4, max(1, _int(params.get("quarter"), (t.month - 1) // 3 + 1)))
        s = date(y, 3 * q - 2, 1)
        e = month_bounds(y, 3 * q)[1]
        label = f"Quý {q} năm {y}"
        p = ReportPeriod("quarter", s, e, label, y, q)
    elif mode == "year":
        y = _int(params.get("year"), t.year)
        p = ReportPeriod("year", date(y, 1, 1), date(y, 12, 31), f"Năm {y}", y)
    elif mode == "all":
        p = ReportPeriod("all", floor, max(t, floor), "Toàn bộ")
    elif mode == "custom":
        try:
            s = parse_date(params.get("start")) or floor
            e = parse_date(params.get("end")) or t
        except ValueError:
            s, e = floor, t
        if e < s:
            s, e = e, s
        p = ReportPeriod(
            "custom", s, e, f"Từ ngày {fmt_date(s)} đến ngày {fmt_date(e)}"
        )
    else:
        y, m = t.year, t.month
        raw = params.get("month") or ""
        try:
            y, m = (int(x) for x in raw.split("-")[:2])
            if not 1 <= m <= 12:
                raise ValueError
        except ValueError:
            y, m = t.year, t.month
        s, e = month_bounds(y, m)
        p = ReportPeriod("month", s, e, f"Tháng {m:02d}/{y}", y)
    if p.start < floor:
        p = ReportPeriod(
            p.mode, floor, max(p.end, floor), p.label, p.year, p.quarter
        )
    return p
