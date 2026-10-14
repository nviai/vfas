"""Data checks for the assets module.

The fixed asset and prepaid/tool registers must agree with the general
ledger, and past months must have depreciation/allocation runs.
"""

from __future__ import annotations

from app.core.dates import today
from app.core.money import format_vnd
from app.core.registry import CheckIssue, data_check
from app.features.assets import service
from app.features.assets.models import RUN_ALLOCATION, RUN_DEPRECIATION
from app.features.journal import queries


@data_check(
    code="assets_vs_ledger",
    title="Sổ TSCĐ khớp số dư TK 211, 214",
    perm="assets.view",
    order=200,
)
def check_assets_vs_ledger(db, ctx):
    d = today()
    rows = service.register(db, ctx.company_id, d)
    gl_cost = queries.balance(db, ctx.company_id, "211", upto=d)
    gl_acc = -queries.balance(db, ctx.company_id, "214", upto=d)
    cost = sum(r.cost for r in rows)
    acc = sum(r.accumulated for r in rows)
    out = []
    if not rows and not gl_cost and not gl_acc:
        return out
    if cost != gl_cost:
        out.append(
            CheckIssue(
                f"Tổng nguyên giá trên sổ TSCĐ {format_vnd(cost)} "
                f"khác số dư Nợ TK 211 {format_vnd(gl_cost)} "
                f"(lệch {format_vnd(cost - gl_cost)})",
                "warning",
                "/assets",
            )
        )
    if acc != gl_acc:
        out.append(
            CheckIssue(
                f"Hao mòn lũy kế trên sổ TSCĐ {format_vnd(acc)} "
                f"khác số dư Có TK 214 {format_vnd(gl_acc)} "
                f"(lệch {format_vnd(acc - gl_acc)})",
                "warning",
                "/assets",
            )
        )
    return out


@data_check(
    code="prepaid_vs_ledger",
    title="CCDC, chi phí trả trước khớp số dư TK 242",
    perm="assets.view",
    order=210,
)
def check_prepaid_vs_ledger(db, ctx):
    items = service.list_prepaid(db, ctx.company_id)
    if not items:
        return []
    posted = service.posted_depreciation(
        db, ctx.company_id, [p.id for p in items], kind="prepaid"
    )
    remaining = sum(
        p.amount - (p.opening_allocated or 0) - posted.get(p.id, 0)
        for p in items
        if p.start_month <= today()
    )
    gl = queries.balance(db, ctx.company_id, "242", upto=today())
    if remaining != gl:
        return [
            CheckIssue(
                f"Giá trị còn phải phân bổ {format_vnd(remaining)} "
                f"khác số dư Nợ TK 242 {format_vnd(gl)} "
                f"(lệch {format_vnd(remaining - gl)})",
                "warning",
                "/prepaid",
            )
        ]
    return []


def _months_issue(months, label, url):
    if not months:
        return []
    shown = ", ".join(f"{m:02d}/{y}" for y, m in months[:6]) + (
        "…" if len(months) > 6 else ""
    )
    return [
        CheckIssue(
            f"{len(months)} tháng đã qua chưa {label}: {shown}", "warning", url
        )
    ]


@data_check(
    code="depreciation_missing",
    title="Tháng chưa trích khấu hao TSCĐ",
    perm="assets.view",
    order=220,
)
def check_depreciation(db, ctx):
    return _months_issue(
        service.missing_months(db, ctx.company_id, RUN_DEPRECIATION, today()),
        "trích khấu hao",
        "/assets/depreciation",
    )


@data_check(
    code="allocation_missing",
    title="Tháng chưa phân bổ CCDC, chi phí trả trước",
    perm="assets.view",
    order=230,
)
def check_allocation(db, ctx):
    return _months_issue(
        service.missing_months(db, ctx.company_id, RUN_ALLOCATION, today()),
        "phân bổ chi phí",
        "/prepaid/allocation",
    )
