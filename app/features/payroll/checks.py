from __future__ import annotations

from app.core.dates import today
from app.core.registry import CheckIssue, data_check
from app.features.payroll import service


@data_check(
    code="payroll_unposted",
    title="Bảng lương chưa ghi sổ",
    perm="payroll.view",
    order=240,
)
def check_unposted(db, ctx):
    return [
        CheckIssue(
            f"Bảng lương tháng {r.period_label} chưa ghi sổ",
            "warning",
            f"/payroll/runs/{r.id}",
        )
        for r in service.months_unposted(db, ctx.company_id, today())
    ]
