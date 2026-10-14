"""Business logic for sales (BH) and sales returns / allowances (TL).

Posting (BH): Dr 131 (partner) | 1111 | 1121 / Cr each line's revenue
              account (after discount);
              Dr 131 | 1111 | 1121 / Cr 33311 per VAT rate.
TL is the reverse. One VatRecord per VAT rate (direction="out"); TL
records belong to the "adjust_down" group.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import like_contains, like_prefix, ulower
from app.core.errors import BusinessError, NotFoundError
from app.core.registry import emit
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.org.models import Company
from app.features.partners import service as partners
from app.features.partners.models import Partner
from app.features.sales.common import (
    TradeHeader,
    TradeLine,
    build_lines,
    invoice_ref,
    parse_header,
    read_grid,
    vat_groups,
)
from app.features.sales.models import (
    DOC_TYPE,
    RETURN,
    SALE,
    SalesDoc,
    SalesLine,
)

PAYMENT_ACCOUNT = {"credit": "131", "cash": "1111", "bank": "1121"}
OUTPUT_VAT_ACCOUNT = "33311"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_revenue_account(company: Company):
    fallback = (
        "5111"
        if company.business_type in ("trade",)
        else "5113"
        if company.business_type == "service"
        else "5112"
    )

    def pick(item) -> str:
        if item is not None and item.revenue_account:
            return item.revenue_account
        if item is not None:
            return {"service": "5113", "product": "5112"}.get(
                item.kind, "5111"
            )
        return fallback

    return pick


@dataclass
class SalesInput:
    kind: str
    header: TradeHeader
    lines: list[TradeLine] = field(default_factory=list)
    einvoice_key: str | None = None

    @property
    def goods_amount(self) -> int:
        return sum(ln.amount for ln in self.lines)

    @property
    def discount_amount(self) -> int:
        return sum(ln.discount_amount for ln in self.lines)

    @property
    def vat_amount(self) -> int:
        return sum(ln.vat_amount for ln in self.lines)

    @property
    def total_amount(self) -> int:
        return self.goods_amount - self.discount_amount + self.vat_amount


def parse_form(
    db: Session, ctx, form: dict, kind: str
) -> tuple[SalesInput | None, dict, list[TradeLine]]:
    """Validate the form data.

    Returns (input, per-field errors, parsed lines for re-rendering).
    """
    company = db.get(Company, ctx.company_id)
    errors: dict = {}
    header = parse_header(form, errors=errors)
    lines, line_errors = build_lines(
        db,
        ctx.company_id,
        read_grid(form),
        default_account=default_revenue_account(company),
        account_prefixes=("511", "711", "515"),
    )
    if line_errors:
        errors["lines"] = "; ".join(line_errors[:5])
    if header is not None:
        if header.partner_id:
            p = db.get(Partner, header.partner_id)
            if p is None or p.company_id != ctx.company_id:
                errors["partner_id"] = "Khách hàng không hợp lệ"
        elif header.payment_method == "credit" and not header.partner_name:
            errors["partner_id"] = (
                "Bán chịu (ghi nợ TK 131) phải chọn khách hàng"
            )
        if header.due_date and header.due_date < header.date:
            errors["due_date"] = (
                "Hạn thanh toán không được trước ngày hạch toán"
            )
        if header.original_entry_id:
            orig = db.get(JournalEntry, header.original_entry_id)
            if orig is None or orig.company_id != ctx.company_id:
                errors["original_entry_id"] = "Chứng từ bán gốc không hợp lệ"
    if errors or header is None:
        return None, errors, lines
    return SalesInput(kind=kind, header=header, lines=lines), {}, lines


def _resolve_partner(db: Session, ctx, inp: SalesInput) -> Partner | None:
    h = inp.header
    if h.partner_id:
        p = db.get(Partner, h.partner_id)
        partners.touch(p)
        return p
    if h.partner_name:
        return partners.find_or_create(
            db,
            ctx,
            h.partner_name,
            address=h.address,
            tax_code=h.tax_code,
            role="customer",
        )
    return None


def build_entry(
    inp: SalesInput, doc_id: int, partner: Partner | None
) -> js.EntryIn:
    h = inp.header
    money_acc = PAYMENT_ACCOUNT[h.payment_method]
    pid = partner.id if partner and h.payment_method == "credit" else None
    bid = h.bank_account_id if h.payment_method == "bank" else None
    is_return = inp.kind == RETURN
    lines: list[js.LineIn] = []

    def pair(
        other: str, amount: int, desc: str, item_id=None, qty=None
    ) -> js.LineIn:
        if is_return:  # Dr revenue/VAT, Cr cash/receivable
            return js.LineIn(
                debit_account=other,
                credit_account=money_acc,
                amount=amount,
                description=desc,
                credit_partner_id=pid,
                credit_bank_account_id=bid,
                item_id=item_id,
                quantity=qty,
            )
        return js.LineIn(
            debit_account=money_acc,
            credit_account=other,
            amount=amount,
            description=desc,
            debit_partner_id=pid,
            debit_bank_account_id=bid,
            item_id=item_id,
            quantity=qty,
        )

    for ln in inp.lines:
        if ln.net > 0:
            lines.append(
                pair(
                    ln.account, ln.net, ln.description, ln.item_id, ln.quantity
                )
            )
    vat: list[js.VatIn] = []
    name = (partner.name if partner else h.partner_name) or h.contact_name
    tax_code = h.tax_code or (partner.tax_code if partner else "")
    for rate, g in vat_groups(inp.lines).items():
        if g.vat > 0:
            lines.append(pair(OUTPUT_VAT_ACCOUNT, g.vat, f"Thuế GTGT {rate}%"))
        vat.append(
            js.VatIn(
                direction="out",
                goods_amount=g.base,
                vat_rate=rate,
                vat_amount=g.vat,
                invoice_no=h.invoice_no,
                invoice_series=h.invoice_series,
                invoice_template=h.invoice_template,
                invoice_date=h.invoice_date or h.date,
                partner_id=partner.id if partner else None,
                partner_name=name,
                partner_tax_code=tax_code,
                description="; ".join(x.description for x in g.lines)[:500],
                vat_account=OUTPUT_VAT_ACCOUNT,
                group="adjust_down" if is_return else "normal",
            )
        )
    label = "Giảm giá, trả lại hàng bán" if is_return else "Bán hàng"
    desc = h.description or f"{label} – {name or 'khách lẻ'}" + (
        f" (HĐ {invoice_ref(h.invoice_series, h.invoice_no)})"
        if h.invoice_no
        else ""
    )
    return js.EntryIn(
        doc_type=DOC_TYPE[inp.kind],
        date=h.date,
        doc_date=h.doc_date,
        lines=lines,
        description=desc,
        partner_id=partner.id if partner else None,
        contact_name=h.contact_name or name or "",
        contact_address=h.address or (partner.address if partner else ""),
        ref_no=invoice_ref(h.invoice_series, h.invoice_no),
        source="sales",
        source_id=doc_id,
        meta={"doc": "sales", "kind": inp.kind},
        vat=vat,
    )


def _apply_doc(
    doc: SalesDoc, inp: SalesInput, partner: Partner | None
) -> None:
    h = inp.header
    doc.kind = inp.kind
    doc.partner_id = partner.id if partner else None
    doc.partner_name = (partner.name if partner else h.partner_name) or ""
    doc.buyer_name = h.contact_name
    doc.buyer_address = h.address or (partner.address if partner else "")
    doc.buyer_tax_code = h.tax_code or (partner.tax_code if partner else "")
    doc.invoice_template, doc.invoice_series, doc.invoice_no = (
        h.invoice_template,
        h.invoice_series,
        h.invoice_no,
    )
    doc.invoice_date = h.invoice_date
    doc.payment_method = h.payment_method
    doc.bank_account_id = (
        h.bank_account_id if h.payment_method == "bank" else None
    )
    due = h.due_date
    if (
        due is None
        and h.payment_method == "credit"
        and partner
        and partner.payment_terms_days
    ):
        due = h.date + timedelta(days=partner.payment_terms_days)
    doc.due_date = due
    doc.goods_amount, doc.discount_amount = (
        inp.goods_amount,
        inp.discount_amount,
    )
    doc.vat_amount, doc.total_amount = inp.vat_amount, inp.total_amount
    doc.original_entry_id = h.original_entry_id
    doc.note = h.note
    if inp.einvoice_key:
        doc.einvoice_key = inp.einvoice_key
    doc.lines.clear()
    for n, ln in enumerate(inp.lines, start=1):
        doc.lines.append(
            SalesLine(
                company_id=doc.company_id,
                line_no=n,
                item_id=ln.item_id,
                description=ln.description,
                unit=ln.unit,
                quantity=ln.quantity,
                unit_price=ln.unit_price,
                amount=ln.net,
                discount_amount=ln.discount_amount,
                vat_rate=ln.vat_rate,
                vat_amount=ln.vat_amount,
                revenue_account=ln.account,
                warehouse_id=ln.warehouse_id,
            )
        )


def create(
    db: Session, ctx, inp: SalesInput, *, post: bool = False
) -> SalesDoc:
    """Create a draft document.

    With post=True the entry is posted immediately; if posting fails
    the draft is kept and the error is raised.
    """
    if inp.einvoice_key and db.scalar(
        select(SalesDoc.id).where(
            SalesDoc.company_id == ctx.company_id,
            SalesDoc.einvoice_key == inp.einvoice_key,
        )
    ):
        raise BusinessError("Hóa đơn này đã được nhập trước đó")
    partner = _resolve_partner(db, ctx, inp)
    doc = SalesDoc(company_id=ctx.company_id, kind=inp.kind, created_at=_now())
    db.add(doc)
    _apply_doc(doc, inp, partner)
    db.flush()
    entry = js.create_entry(
        db, ctx, build_entry(inp, doc.id, partner), commit=False
    )
    doc.entry_id = entry.id
    db.commit()
    if post:
        js.post_entry(db, ctx, entry)
    return doc


def update(db: Session, ctx, doc: SalesDoc, inp: SalesInput) -> SalesDoc:
    entry = db.get(JournalEntry, doc.entry_id)
    if entry is None or not entry.is_draft:
        raise BusinessError(
            "Chứng từ đã ghi sổ không được sửa. Hãy hủy (kỳ đang mở) "
            "hoặc lập chứng từ điều chỉnh."
        )
    if inp.kind != doc.kind:
        raise BusinessError("Không đổi được loại chứng từ")
    # Lets other modules refuse the change (an issued e-invoice).
    emit("sales.before_update", db, ctx, doc)
    partner = _resolve_partner(db, ctx, inp)
    _apply_doc(doc, inp, partner)
    db.flush()
    js.update_draft(
        db, ctx, entry, build_entry(inp, doc.id, partner), commit=False
    )
    db.commit()
    return doc


def get_doc(
    db: Session, ctx, entry_id: int
) -> tuple[JournalEntry, SalesDoc | None]:
    entry = js.get_entry(db, ctx, entry_id, doc_types=tuple(DOC_TYPE.values()))
    doc = db.scalar(
        select(SalesDoc).where(
            SalesDoc.company_id == ctx.company_id,
            SalesDoc.entry_id == entry.id,
        )
    )
    return entry, doc


def doc_to_form(doc: SalesDoc) -> tuple[dict, list[dict]]:
    form = {
        "date": doc.entry.date if doc.entry else None,
        "invoice_date": doc.invoice_date,
        "partner_id": doc.partner_id or "",
        "partner_label": (doc.partner.label if doc.partner else ""),
        "partner_name": doc.partner_name,
        "address": doc.buyer_address,
        "tax_code": doc.buyer_tax_code,
        "contact_name": doc.buyer_name,
        "invoice_template": doc.invoice_template,
        "invoice_series": doc.invoice_series,
        "invoice_no": doc.invoice_no,
        "payment_method": doc.payment_method,
        "bank_account_id": doc.bank_account_id or "",
        "due_date": doc.due_date,
        "description": doc.entry.description if doc.entry else "",
        "note": doc.note,
        "original_entry_id": doc.original_entry_id or "",
    }
    lines = []
    for ln in doc.lines:
        lines.append(
            TradeLine(
                description=ln.description,
                amount=ln.amount + ln.discount_amount,
                vat_rate=ln.vat_rate,
                vat_amount=ln.vat_amount,
                item_id=ln.item_id,
                unit=ln.unit,
                quantity=ln.quantity,
                unit_price=ln.unit_price,
                discount_amount=ln.discount_amount,
                account=ln.revenue_account,
                item_label=ln.item.label if ln.item else "",
                warehouse_id=ln.warehouse_id,
            ).grid_dict()
        )
    return form, lines


def return_defaults(
    db: Session, ctx, entry_id: int
) -> tuple[dict, list[dict]]:
    """Suggest values for a return/allowance from the original sale."""
    entry, doc = get_doc(db, ctx, entry_id)
    if doc is None or doc.kind != SALE or not entry.is_posted:
        raise BusinessError(
            "Chỉ lập trả lại/giảm giá từ chứng từ bán hàng đã ghi sổ"
        )
    form, lines = doc_to_form(doc)
    form["original_entry_id"] = entry.id
    form["date"] = None
    form["description"] = f"Hàng bán trả lại / giảm giá theo {entry.number}"
    form["invoice_no"], form["invoice_series"] = "", ""
    return form, lines


@dataclass
class ListFilters:
    kind: str = SALE
    q: str = ""
    status: str = ""
    start: date | None = None
    end: date | None = None
    payment: str = ""
    partner_id: int | None = None


def list_docs(
    db: Session, ctx, f: ListFilters, *, limit: int = 50, offset: int = 0
):
    """List (SalesDoc, JournalEntry) rows plus totals."""
    je, sd = JournalEntry, SalesDoc
    stmt = (
        select(sd, je)
        .join(je, je.id == sd.entry_id)
        .where(sd.company_id == ctx.company_id, sd.kind == f.kind)
    )
    if f.q:
        term = f.q.strip().lower()
        stmt = stmt.where(
            or_(
                like_contains(ulower(je.number), term),
                like_contains(ulower(sd.partner_name), term),
                like_contains(ulower(sd.invoice_no), term),
                like_contains(ulower(je.description), term),
                like_prefix(sd.buyer_tax_code, f.q.strip()),
            )
        )
    if f.status:
        stmt = stmt.where(je.status == f.status)
    if f.start:
        stmt = stmt.where(je.date >= f.start)
    if f.end:
        stmt = stmt.where(je.date <= f.end)
    if f.payment:
        stmt = stmt.where(sd.payment_method == f.payment)
    if f.partner_id:
        stmt = stmt.where(sd.partner_id == f.partner_id)
    sub = stmt.subquery()
    totals = db.execute(
        select(
            func.count(),
            func.coalesce(
                func.sum(sub.c.goods_amount - sub.c.discount_amount), 0
            ),
            func.coalesce(func.sum(sub.c.vat_amount), 0),
            func.coalesce(func.sum(sub.c.total_amount), 0),
        ).where(sub.c.status != "cancelled")
    ).one()
    count_all = db.scalar(select(func.count()).select_from(sub)) or 0
    rows = db.execute(
        stmt.order_by(je.date.desc(), je.id.desc()).limit(limit).offset(offset)
    ).all()
    return rows, {
        "count": int(count_all),
        "posted_count": int(totals[0]),
        "net": int(totals[1]),
        "vat": int(totals[2]),
        "total": int(totals[3]),
    }


def delete_for_entry(db: Session, entry_id: int) -> None:
    for doc in db.scalars(
        select(SalesDoc).where(SalesDoc.entry_id == entry_id)
    ):
        db.delete(doc)


def get_by_entry_id(db: Session, entry_id: int) -> SalesDoc | None:
    return db.scalar(select(SalesDoc).where(SalesDoc.entry_id == entry_id))


def ensure_doc(entry: JournalEntry, doc: SalesDoc | None) -> SalesDoc:
    if doc is None:
        raise NotFoundError("Chứng từ bán hàng không có dữ liệu chi tiết")
    return doc
