"""AI tools: receivables, payables and debt aging."""

from __future__ import annotations

from app.core.dates import parse_date, today
from app.core.registry import ai_tool
from app.features.debts import service as ds
from app.features.partners.models import Partner

_PARAMS = {
    "type": "object",
    "properties": {
        "partner_code": {
            "type": "string",
            "description": "Partner code; omit for all partners",
        },
        "date": {
            "type": "string",
            "description": "As-of date YYYY-MM-DD (default today)",
        },
    },
}


def _balances(
    db, ctx, kind: str, partner_code: str | None, as_of: str | None
) -> dict:
    d = parse_date(as_of) if as_of else today()
    sign = ds.sign_of(kind)
    rows = ds.balances_at(db, ctx.company_id, kind, d)
    partners = {
        p.id: p
        for p in db.query(Partner).filter(
            Partner.company_id == ctx.company_id,
            Partner.id.in_([k for k in rows if k]),
        )
    }
    out = []
    for pid, net in rows.items():
        p = partners.get(pid)
        if partner_code and (p is None or p.code != partner_code):
            continue
        out.append(
            {
                "partner_code": p.code if p else None,
                "partner_name": p.name if p else None,
                "balance": sign * net,
            }
        )
    out.sort(key=lambda r: -r["balance"])
    return {
        "as_of": d.isoformat(),
        "account": ds.KINDS[kind]["account"],
        "partners": out[:100],
        "total_owed": sum(r["balance"] for r in out if r["balance"] > 0),
        "total_prepaid": -sum(r["balance"] for r in out if r["balance"] < 0),
    }


@ai_tool(
    name="get_receivables",
    label="Công nợ phải thu",
    perm="debts.view",
    parameters=_PARAMS,
    description=(
        "Customer receivables (account 131) per partner at a date. "
        "Positive balance = customer owes us; "
        "negative = customer prepaid."
    ),
)
def get_receivables(
    db, ctx, partner_code: str | None = None, date: str | None = None
) -> dict:  # noqa: A002 - AI tool parameter name
    return _balances(db, ctx, "receivable", partner_code, date)


@ai_tool(
    name="get_payables",
    label="Công nợ phải trả",
    perm="debts.view",
    parameters=_PARAMS,
    description=(
        "Supplier payables (account 331) per partner at a date. "
        "Positive balance = we owe the supplier; "
        "negative = we prepaid."
    ),
)
def get_payables(
    db, ctx, partner_code: str | None = None, date: str | None = None
) -> dict:  # noqa: A002 - AI tool parameter name
    return _balances(db, ctx, "payable", partner_code, date)


@ai_tool(
    name="get_debt_aging",
    label="Tuổi nợ",
    perm="debts.view",
    description=(
        "Debt aging by overdue buckets (FIFO) for receivables or "
        "payables at a date."
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["receivable", "payable"]},
            "date": {"type": "string", "description": "As-of date YYYY-MM-DD"},
        },
        "required": ["kind"],
    },
)
def get_debt_aging(db, ctx, kind: str, date: str | None = None) -> dict:  # noqa: A002 - AI tool parameter name
    d = parse_date(date) if date else today()
    rows = ds.aging(
        db, ctx.company_id, kind if kind in ds.KINDS else "receivable", d
    )
    return {
        "as_of": d.isoformat(),
        "buckets": [k for k, _ in ds.BUCKETS],
        "partners": [
            {
                "partner_code": r.partner.code if r.partner else None,
                "partner_name": r.partner.name if r.partner else None,
                "balance": r.balance,
                "max_days_overdue": r.max_overdue(d),
                **{k: r.buckets.get(k, 0) for k, _ in ds.BUCKETS},
            }
            for r in rows[:100]
        ],
        "totals": ds.aging_totals(rows),
    }
