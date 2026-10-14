"""Data checks of the cash module."""

from __future__ import annotations

from app.core.dates import fmt_date
from app.core.money import format_vnd
from app.core.registry import CheckIssue, data_check
from app.features.cash import counts


@data_check(
    code="cashcount_unresolved",
    title="Chênh lệch kiểm kê quỹ chưa xử lý",
    perm="cash.view",
    order=25,
)
def check_cashcount(db, ctx):
    out = []
    for cc in counts.list_cash_counts(db, ctx.company_id)[:24]:
        if cc.difference == 0:
            continue
        remaining = counts.remaining_difference(db, cc)
        if remaining:
            kind = "thừa" if remaining > 0 else "thiếu"
            out.append(
                CheckIssue(
                    f"Bảng kiểm kê {cc.number} ngày "
                    f"{fmt_date(cc.count_date)}: còn {kind} "
                    f"{format_vnd(abs(remaining))} đồng "
                    "chưa lập chứng từ xử lý",
                    "warning",
                    f"/cash/counts/{cc.id}",
                    cc.count_date,
                )
            )
    return out
