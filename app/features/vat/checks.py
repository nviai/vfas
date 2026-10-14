"""VAT data checks."""

from __future__ import annotations

from sqlalchemy import func, or_, select

from app.core.dates import fmt_date
from app.core.registry import CheckIssue, data_check, doc_url
from app.features.journal.models import (
    POSTED,
    JournalEntry,
    JournalLine,
    VatRecord,
)


@data_check(
    code="vat_duplicate_invoice",
    title="Hóa đơn GTGT bị kê trùng",
    perm="vat.view",
    order=80,
)
def check_duplicate_invoices(db, ctx):
    vr, je = VatRecord, JournalEntry
    rows = db.execute(
        select(
            vr.direction,
            vr.partner_tax_code,
            vr.invoice_series,
            vr.invoice_no,
            func.count(func.distinct(vr.entry_id)),
        )
        .join(je, je.id == vr.entry_id)
        .where(
            vr.company_id == ctx.company_id,
            je.status == POSTED,
            vr.invoice_no != "",
            vr.group != "adjust_down",
        )
        .group_by(
            vr.direction, vr.partner_tax_code, vr.invoice_series, vr.invoice_no
        )
        .having(func.count(func.distinct(vr.entry_id)) > 1)
        .limit(50)
    ).all()
    return [
        CheckIssue(
            f"Hóa đơn {'mua vào' if d == 'in' else 'bán ra'} {s}-{n} "
            f"(MST {t or '–'}) có trên {c} chứng từ",
            "error",
            "/vat/lists?direction=" + d,
        )
        for d, t, s, n, c in rows
    ]


@data_check(
    code="vat_missing_invoice",
    title="Có thuế GTGT nhưng thiếu hóa đơn",
    perm="vat.view",
    order=81,
)
def check_missing_invoice(db, ctx):
    jl, je, vr = JournalLine, JournalEntry, VatRecord
    has_vat = select(vr.entry_id).where(vr.company_id == ctx.company_id)
    rows = db.execute(
        select(je.id, je.doc_type, je.number, je.date)
        .join(jl, jl.entry_id == je.id)
        .where(
            je.company_id == ctx.company_id,
            je.status == POSTED,
            je.doc_type != "KC",
            or_(jl.debit_account.like("133%"), jl.credit_account == "33311"),
            je.id.not_in(has_vat),
        )
        .where(
            ~((jl.debit_account == "33311") & jl.credit_account.like("133%"))
        )
        .distinct()
        .order_by(je.date)
        .limit(50)
    ).all()
    return [
        CheckIssue(
            f"Chứng từ {n} ngày {fmt_date(d)} hạch toán TK 133/33311 "
            "nhưng chưa nhập thông tin hóa đơn",
            "warning",
            doc_url(t, i),
            d,
        )
        for i, t, n, d in rows
    ]
