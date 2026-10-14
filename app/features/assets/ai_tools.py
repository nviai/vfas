"""Read-only AI tools of the assets module."""

from __future__ import annotations

from app.core.dates import parse_date, today
from app.core.registry import ai_tool
from app.features.assets import service
from app.features.assets.models import DISPOSED, IN_USE, RUN_DEPRECIATION


@ai_tool(
    name="list_fixed_assets",
    label="Xem danh sách TSCĐ",
    perm="assets.view",
    description=(
        "List fixed assets with cost, accumulated depreciation (posted), "
        "remaining value and monthly depreciation as of a date."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["in_use", "disposed", "all"],
                "description": "Default in_use",
            },
            "date": {
                "type": "string",
                "description": "ISO date, default today",
            },
        },
    },
)
def list_fixed_assets(
    db, ctx, status: str = "in_use", date: str | None = None
) -> dict:
    on = parse_date(date) if date else today()
    rows = service.register(
        db, ctx.company_id, on, include_disposed=status in ("disposed", "all")
    )
    if status == "disposed":
        rows = [r for r in rows if r.asset.status == DISPOSED]
    elif status == "in_use":
        rows = [r for r in rows if r.asset.status == IN_USE]
    return {
        "date": on.isoformat(),
        "assets": [
            {
                "code": r.asset.code,
                "name": r.asset.name,
                "category": r.asset.category_label,
                "status": r.asset.status,
                "cost": r.cost,
                "accumulated_depreciation": r.accumulated,
                "remaining_value": r.remaining,
                "monthly_depreciation": service.monthly_rate(r.asset),
                "useful_life_months": r.asset.useful_life_months,
                "acquired_date": r.asset.acquired_date.isoformat(),
            }
            for r in rows[:200]
        ],
        "total_cost": sum(r.cost for r in rows),
        "total_remaining": sum(r.remaining for r in rows),
    }


@ai_tool(
    name="get_depreciation_schedule",
    label="Xem lịch khấu hao",
    perm="assets.view",
    description=(
        "Monthly depreciation for a year: scheduled amount and whether "
        "the month has been posted."
    ),
    parameters={
        "type": "object",
        "properties": {"year": {"type": "integer"}},
        "required": ["year"],
    },
)
def get_depreciation_schedule(db, ctx, year: int) -> dict:
    months = []
    for m in range(1, 13):
        pv = service.preview(
            db, ctx.company_id, RUN_DEPRECIATION, int(year), m
        )
        run = service.get_run(
            db, ctx.company_id, RUN_DEPRECIATION, int(year), m
        )
        posted = bool(run and run.entry and run.entry.status == "posted")
        months.append(
            {
                "month": m,
                "scheduled": pv.total,
                "posted": posted,
                "entry_number": run.entry.number if posted else None,
            }
        )
    return {
        "year": int(year),
        "months": months,
        "total_scheduled": sum(x["scheduled"] for x in months),
    }
