"""AI tools of the bank deposit module."""

from __future__ import annotations

from sqlalchemy import select

from app.core.registry import ai_tool
from app.features.bank import statements as st
from app.features.bank.models import BankAccount


@ai_tool(
    name="list_unmatched_bank_lines",
    label="Xem giao dịch sao kê chưa khớp",
    perm="bank.view",
    description=(
        "Bank statement transactions not yet matched to any posted "
        "voucher, with a suggested offsetting account from keyword rules "
        "when available. Use it to explain what is missing from the "
        "books."
    ),
    parameters={
        "type": "object",
        "properties": {
            "bank_account": {
                "type": "string",
                "description": "Account number or short name; default all",
            },
            "limit": {
                "type": "integer",
                "description": "Max rows (default 30, max 100)",
            },
        },
    },
)
def list_unmatched_bank_lines(
    db, ctx, *, bank_account: str | None = None, limit: int = 30
) -> dict:
    banks = list(
        db.scalars(
            select(BankAccount).where(BankAccount.company_id == ctx.company_id)
        )
    )
    if bank_account:
        key = bank_account.strip().lower()
        banks = [
            b
            for b in banks
            if key in b.number.lower()
            or key in (b.short_name or "").lower()
            or key in b.bank_name.lower()
        ]
    limit = max(1, min(int(limit or 30), 100))
    out = []
    for b in banks:
        for sl in st.list_lines(
            db,
            ctx.company_id,
            bank_account_id=b.id,
            status="unmatched",
            limit=limit,
        ):
            rule = st.suggest(db, ctx.company_id, sl)
            out.append(
                {
                    "id": sl.id,
                    "bank_account": b.label,
                    "date": sl.date.isoformat(),
                    "direction": "in" if sl.incoming else "out",
                    "amount": sl.amount,
                    "description": sl.description[:200],
                    "reference": sl.reference,
                    "suggested_account": rule.account if rule else None,
                    "url": f"/bank/statements/lines/{sl.id}",
                }
            )
    out.sort(key=lambda r: r["date"], reverse=True)
    return {"count": len(out[:limit]), "lines": out[:limit]}
