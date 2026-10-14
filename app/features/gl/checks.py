"""Data checks for the general ledger and financial statements."""

from __future__ import annotations

from datetime import date

from app.core.dates import today
from app.core.money import format_vnd
from app.core.registry import CheckIssue, data_check
from app.features.gl import books, statements
from app.features.gl.periods import make
from app.features.journal import locks
from app.features.org.models import Company


def _years(company: Company) -> range:
    return range(company.start_date.year, today().year + 1)


@data_check(
    code="gl_trial_balance",
    title="Bảng cân đối tài khoản không cân",
    perm="reports.view",
    order=15,
)
def check_trial_balance(db, ctx):
    company = db.get(Company, ctx.company_id)
    out = []
    for y in _years(company):
        tb = books.trial_balance(
            db,
            company.id,
            max(date(y, 1, 1), company.start_date),
            date(y, 12, 31),
            level=1,
        )
        if not tb.balanced:
            t = tb.total
            out.append(
                CheckIssue(
                    f"Năm {y}: dư Nợ cuối kỳ {format_vnd(t.close_d)} "
                    f"≠ dư Có {format_vnd(t.close_c)}",
                    "error",
                    f"/reports/f01?mode=year&year={y}",
                )
            )
    return out


@data_check(
    code="gl_balance_sheet",
    title="Báo cáo tình hình tài chính không cân",
    perm="reports.view",
    order=16,
)
def check_balance_sheet(db, ctx):
    company = db.get(Company, ctx.company_id)
    out = []
    for y in _years(company):
        st = statements.b01a(db, company, make("year", y))
        if st.value("200") != st.value("500"):
            out.append(
                CheckIssue(
                    f"Năm {y}: tổng tài sản {format_vnd(st.value('200'))} "
                    "≠ tổng nguồn vốn "
                    f"{format_vnd(st.value('500'))}",
                    "error",
                    f"/reports/b01a?mode=year&year={y}",
                )
            )
        if st.unmapped:
            out.append(
                CheckIssue(
                    f"Năm {y}: số dư TK chưa xếp vào chỉ tiêu B01a: "
                    f"{', '.join(sorted(st.unmapped))}",
                    "warning",
                    f"/reports/b01a?mode=year&year={y}",
                )
            )
    return out


@data_check(
    code="gl_unclosed_years",
    title="Năm đã kết thúc chưa kết chuyển lãi lỗ",
    perm="reports.view",
    order=17,
)
def check_unclosed(db, ctx):
    company = db.get(Company, ctx.company_id)
    out = []
    locked = locks.locked_set(db, company.id)
    for y in _years(company):
        end = date(y, 12, 31)
        if end >= today():
            continue
        pl = books.pl_balances(db, company.id, end)
        if pl:
            sev = "error" if (y, 0) in locked else "warning"
            out.append(
                CheckIssue(
                    f"Năm {y}: TK {', '.join(sorted(pl))} còn số dư "
                    "ngày 31/12 – chưa kết chuyển",
                    sev,
                    f"/closing?mode=year&year={y}",
                )
            )
    return out
