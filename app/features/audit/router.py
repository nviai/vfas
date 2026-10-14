"""Audit log (read-only, append-only) of the selected company."""

from __future__ import annotations

from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select

from app.core.audit import AuditLog
from app.core.dates import parse_date, tz
from app.core.db import ulower
from app.core.security import CurrentUser, require
from app.core.web import get_tenant_db, is_htmx, render

router = APIRouter()
PAGE = 50

ACTION_GROUPS = {
    "entry": "Chứng từ",
    "period": "Khóa sổ",
    "opening": "Số dư đầu kỳ",
    "member": "Thành viên",
    "company": "Công ty",
    "account": "Tài khoản",
    "partner": "Đối tượng",
    "item": "Hàng hóa",
    "attachment": "Đính kèm",
    "api_token": "API token",
    "ai": "Trợ lý AI",
}


PAYROLL_ENTITIES = {"employee", "payroll_run", "payroll_param"}


def payroll_sensitive(log: AuditLog) -> bool:
    """Return True if the log may contain payroll figures.

    Covers employee records, payroll runs and payroll accrual/payment
    entries.
    """
    if log.entity_type in PAYROLL_ENTITIES or (log.action or "").startswith(
        "payroll."
    ):
        return True
    if log.entity_type == "entry":
        blob = (log.before or "") + (log.after or "")
        return '"doc_type": "LG"' in blob or '"source": "payroll"' in blob
    return False


def _local_bound(d, end: bool) -> datetime:
    t = time.max if end else time.min
    return datetime.combine(d, t, tzinfo=tz()).astimezone(timezone.utc)


@router.get("/audit")
def audit_page(
    request: Request,
    q: str = "",
    group: str = "",
    via: str = "",
    start: str = "",
    end: str = "",
    page: int = 1,
    user: CurrentUser = Depends(require("audit.view")),
    db=Depends(get_tenant_db),
):
    page = max(1, page)
    stmt = select(AuditLog).where(AuditLog.company_id == user.company_id)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                ulower(AuditLog.summary).like(like),
                ulower(AuditLog.username).like(like),
                AuditLog.entity_id == q.strip(),
            )
        )
    if group:
        stmt = stmt.where(AuditLog.action.like(f"{group}.%"))
    if via:
        stmt = stmt.where(AuditLog.via == via)
    try:
        if start:
            stmt = stmt.where(
                AuditLog.at >= _local_bound(parse_date(start), False)
            )
        if end:
            stmt = stmt.where(
                AuditLog.at <= _local_bound(parse_date(end), True)
            )
    except ValueError:
        pass
    rows = list(
        db.scalars(
            stmt.order_by(AuditLog.id.desc())
            .limit(PAGE + 1)
            .offset((page - 1) * PAGE)
        )
    )
    hidden = (
        set()
        if user.can("payroll.view")
        else {r.id for r in rows if payroll_sensitive(r)}
    )
    ctx = {
        "logs": rows[:PAGE],
        "has_next": len(rows) > PAGE,
        "page": page,
        "q": q,
        "group": group,
        "via": via,
        "hidden_ids": hidden,
        "start": start,
        "end": end,
        "groups": ACTION_GROUPS,
        "breadcrumbs": [("Nhật ký thao tác", None)],
    }
    if (
        is_htmx(request)
        and request.headers.get("hx-target") == "audit-results"
    ):
        return render(request, "audit/_rows.html", ctx)
    return render(request, "audit/index.html", ctx)
