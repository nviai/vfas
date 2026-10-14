"""AI tool: VAT summary for a period."""

from __future__ import annotations

from app.core.dates import parse_date
from app.core.errors import BusinessError
from app.core.registry import ai_tool
from app.features.vat import service as vs

KEYS = (
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31",
    "32",
    "33",
    "32a",
    "34",
    "35",
    "36",
    "40",
    "41",
    "43",
)


@ai_tool(
    name="get_vat_summary",
    label="Tóm tắt thuế GTGT",
    perm="vat.view",
    description=(
        "VAT return (form 01/GTGT, credit method) indicators computed "
        "from posted documents between two dates: purchases [23]/[24], "
        "deductible [25], sales by rate [26]-[35], payable [40] or "
        "carried forward [43]. Amounts in VND."
    ),
    parameters={
        "type": "object",
        "properties": {
            "period_start": {"type": "string", "description": "YYYY-MM-DD"},
            "period_end": {"type": "string", "description": "YYYY-MM-DD"},
            "carried_forward": {
                "type": "integer",
                "description": (
                    "Indicator [22] from the previous return (optional)"
                ),
            },
        },
        "required": ["period_start", "period_end"],
    },
)
def get_vat_summary(
    db,
    ctx,
    period_start: str,
    period_end: str,
    carried_forward: int | None = None,
) -> dict:
    start, end = parse_date(period_start), parse_date(period_end)
    if not start or not end or end < start:
        raise BusinessError("Kỳ không hợp lệ")
    v = vs.compute(
        db, ctx.company_id, start, end, carried=int(carried_forward or 0)
    )
    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "indicators": {k: v[k] for k in KEYS},
        "labels": {k: vs.LABELS[k] for k in KEYS},
        "vat_payable": v["40"],
        "carried_to_next_period": v["43"],
    }
