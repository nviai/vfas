"""Filing calendar for tax returns and financial statements.

Based on the Law on Tax Administration 2019 and Decree
126/2020/ND-CP.

- VAT and withheld PIT: monthly period -> the 20th of the following
  month; quarterly period -> the last day of the first month of the
  next quarter. Withheld PIT on salaries is quarterly from 04/2026
  (see app/features/payroll/pit_return.py).
- Provisional quarterly CIT: the 30th of the first month of the next
  quarter (Q4 is paid with the annual finalization).
- Annual CIT finalization and annual financial statements: the last
  day of the third month after the fiscal year end.

Deadlines falling on a Saturday or Sunday move to the following
Monday (public holidays are not taken into account).
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from app.features.org.models import Company


def next_workday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _last_day(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y + 1, 1) if m == 12 else (y, m + 1)


def vat_periods_due(company: Company, today: date, window: int = 75):
    """VAT periods whose deadline falls within
    [today - window, today + window].
    """
    out = []
    if company.vat_period == "month":
        y, m = today.year, today.month
        for back in range(0, 4):
            yy, mm = y, m - back
            while mm <= 0:
                yy, mm = yy - 1, mm + 12
            ny, nm = _next_month(yy, mm)
            due = next_workday(date(ny, nm, 20))
            out.append(("month", yy, mm, due))
    else:
        q = (today.month - 1) // 3 + 1
        for back in range(0, 3):
            yy, qq = today.year, q - back
            while qq <= 0:
                yy, qq = yy - 1, qq + 4
            end_m = qq * 3
            ny, nm = _next_month(yy, end_m)
            due = next_workday(_last_day(ny, nm))
            out.append(("quarter", yy, qq, due))
    return [p for p in out if abs((p[3] - today).days) <= window]


def deadlines(
    company: Company, today: date, db=None, *, window: int = 75
) -> list[dict]:
    """Nearby deadlines (overdue or upcoming), sorted by date.

    Each item: {title, label, due, days, period, url, status, kind};
    status is 'submitted' once the return has been finalized.
    """
    final_vat: set = set()
    final_cit: set = set()
    final_pit: set = set()
    if db is not None:
        from sqlalchemy import select

        from app.features.vat.models import (
            FORM_CIT,
            FORM_PIT,
            FORM_VAT,
            TaxDeclaration,
        )

        rows = db.execute(
            select(
                TaxDeclaration.form,
                TaxDeclaration.period_type,
                TaxDeclaration.year,
                TaxDeclaration.period,
            ).where(
                TaxDeclaration.company_id == company.id,
                TaxDeclaration.status != "draft",
            )
        )
        for form, pt, y, p in rows:
            if form == FORM_VAT:
                final_vat.add((pt, y, p))
            elif form == FORM_PIT:
                final_pit.add((pt, y, p))
            elif form == FORM_CIT:
                final_cit.add(y)

    items: list[dict] = []

    def add(
        title: str,
        due: date,
        period: str,
        url: str,
        kind: str,
        status: str = "pending",
    ) -> None:
        items.append(
            {
                "title": title,
                "label": title,
                "due": due,
                "period": period,
                "url": url,
                "status": status,
                "kind": kind,
                "days": (due - today).days,
            }
        )

    for pt, y, p, due in vat_periods_due(company, today, window):
        label = f"tháng {p:02d}/{y}" if pt == "month" else f"quý {p}/{y}"
        add(
            f"Tờ khai thuế GTGT {label}",
            due,
            label,
            f"/vat/declarations/{pt}/{y}/{p}",
            "vat",
            "submitted" if (pt, y, p) in final_vat else "pending",
        )
    # Withheld PIT on salaries (05/KK-TNCN). Past deadlines are only
    # shown once finalized: not every company pays salaries.
    from app.features.payroll import pit_return

    p = pit_return.period_of(company, today)
    for _ in range(4):
        due = next_workday(pit_return.due_date(p))
        key = (p.period_type, p.year, p.period)
        if abs((due - today).days) <= window and (
            due >= today or key in final_pit
        ):
            label = p.label[0].lower() + p.label[1:]
            add(
                f"Tờ khai khấu trừ thuế TNCN {label} (05/KK-TNCN)",
                due,
                label,
                f"/payroll/pit-returns/{p.period_type}/{p.year}/{p.period}",
                "pit",
                "submitted" if key in final_pit else "pending",
            )
        p = pit_return.previous(company, p)
    # Provisional CIT for Q1–Q3 (Q4 is paid with the annual
    # finalization).
    q = (today.month - 1) // 3 + 1
    for back in range(0, 2):
        yy, qq = today.year, q - back
        if qq <= 0:
            yy, qq = yy - 1, qq + 4
        if qq == 4:
            continue
        ny, nm = _next_month(yy, qq * 3)
        due = next_workday(date(ny, nm, 30))
        if abs((due - today).days) <= window:
            add(
                f"Tạm nộp thuế TNDN quý {qq}/{yy}",
                due,
                f"quý {qq}/{yy}",
                f"/vat/cit?year={yy}",
                "cit_q",
            )
    # CIT finalization and financial statements for the previous year.
    prev = today.year - 1
    due = next_workday(_last_day(today.year, 3))
    if -window <= (due - today).days <= 120:
        add(
            f"Quyết toán thuế TNDN năm {prev}",
            due,
            str(prev),
            f"/vat/cit?year={prev}",
            "cit",
            "submitted" if prev in final_cit else "pending",
        )
        add(
            f"Báo cáo tài chính năm {prev}",
            due,
            str(prev),
            "/vat/calendar",
            "fs",
        )
    # Stop reminding once a deadline is more than 45 days past (avoids
    # noise when the company does not prepare its returns in the app).
    items = [it for it in items if it["days"] >= -45]
    items.sort(key=lambda it: (it["status"] == "submitted", it["due"]))
    return items
