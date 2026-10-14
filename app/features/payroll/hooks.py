from __future__ import annotations

from sqlalchemy import update

from app.core.registry import on


@on("entry.deleted")
def _payroll_entry_deleted(db, ctx, entry) -> None:
    from app.features.payroll.models import PayrollRun

    cid = entry.company_id
    db.execute(
        update(PayrollRun)
        .where(PayrollRun.company_id == cid, PayrollRun.entry_id == entry.id)
        .values(entry_id=None)
    )
    db.execute(
        update(PayrollRun)
        .where(
            PayrollRun.company_id == cid,
            PayrollRun.payment_entry_id == entry.id,
        )
        .values(payment_entry_id=None)
    )
