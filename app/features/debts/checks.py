"""Data checks for receivables/payables and partners."""

from __future__ import annotations

from sqlalchemy import select

from app.core.dates import today
from app.core.money import format_vnd
from app.core.registry import CheckIssue, data_check
from app.features.debts import service as ds
from app.features.org.service import validate_tax_code
from app.features.partners.models import Partner


@data_check(
    code="debts_overdue_90",
    title="Công nợ phải thu quá hạn trên 90 ngày",
    perm="debts.view",
    order=85,
)
def check_overdue(db, ctx):
    out = []
    for r in ds.aging(db, ctx.company_id, "receivable", today()):
        amount = r.buckets.get("over90", 0)
        if amount > 0 and r.partner is not None:
            out.append(
                CheckIssue(
                    f"{r.partner.name}: {format_vnd(amount)} đ "
                    "quá hạn trên 90 ngày",
                    "warning",
                    f"/debts/partner/{r.partner.id}?kind=receivable",
                )
            )
    return out[:50]


@data_check(
    code="partner_tax_code",
    title="Mã số thuế đối tượng sai định dạng",
    perm="catalog.view",
    order=86,
)
def check_tax_codes(db, ctx):
    rows = db.scalars(
        select(Partner).where(
            Partner.company_id == ctx.company_id, Partner.tax_code != ""
        )
    )
    return [
        CheckIssue(
            f"{p.code} – {p.name}: MST '{p.tax_code}' không hợp lệ",
            "warning",
            f"/partners/{p.id}",
        )
        for p in rows
        if validate_tax_code(p.tax_code)
    ][:50]
