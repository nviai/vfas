"""Business logic for purchases (MH) and purchase returns (TM).

Posting (MH): Dr each line's account (156/152/153/642x/211x/242...,
              plus non-deductible VAT) / Cr 331 | 1111 | 1121;
              Dr 1331 (1332 when the line increases fixed assets
              211x) / Cr 331 | 1111 | 1121 for the deductible VAT.
TM is the reverse. VatRecord direction="in" per (VAT rate,
deductible, VAT account); TM records belong to the "adjust_down"
group.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.db import like_contains, like_prefix, ulower
from app.core.errors import BusinessError
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.partners import service as partners
from app.features.partners.models import Partner
from app.features.purchases.models import (
    DOC_TYPE,
    PURCHASE,
    RETURN,
    PurchaseDoc,
    PurchaseLine,
)
from app.features.sales.common import (
    TradeHeader,
    TradeLine,
    build_lines,
    invoice_ref,
    parse_header,
    read_grid,
    vat_groups,
)

PAYMENT_ACCOUNT = {"credit": "331", "cash": "1111", "bank": "1121"}
# Valid debit account prefixes on purchase documents.
DEBIT_PREFIXES = (
    "15",
    "211",
    "2411",
    "2413",
    "242",
    "6",
    "811",
    "133",
    "138",
    "141",
    "335",
    "217",
)
DEFAULT_EXPENSE_ACCOUNT = "6422"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def default_debit_account(item) -> str:
    if item is None:
        return DEFAULT_EXPENSE_ACCOUNT
    if item.stock_account:
        return item.stock_account
    return item.expense_account or DEFAULT_EXPENSE_ACCOUNT


def input_vat_account(ln: TradeLine) -> str:
    return "1332" if ln.account.startswith("211") else "1331"


@dataclass
class PurchaseInput:
    kind: str
    header: TradeHeader
    lines: list[TradeLine] = field(default_factory=list)
    einvoice_key: str | None = None

    @property
    def goods_amount(self) -> int:
        return sum(ln.amount for ln in self.lines)

    @property
    def vat_amount(self) -> int:
        return sum(ln.vat_amount for ln in self.lines)

    @property
    def total_amount(self) -> int:
        return self.goods_amount + self.vat_amount


def parse_form(
    db: Session, ctx, form: dict, kind: str
) -> tuple[PurchaseInput | None, dict, list[TradeLine]]:
    errors: dict = {}
    header = parse_header(form, errors=errors)
    lines, line_errors = build_lines(
        db,
        ctx.company_id,
        read_grid(form),
        default_account=default_debit_account,
        account_prefixes=DEBIT_PREFIXES,
        use_discount=False,
    )
    if line_errors:
        errors["lines"] = "; ".join(line_errors[:5])
    if header is not None:
        if header.partner_id:
            p = db.get(Partner, header.partner_id)
            if p is None or p.company_id != ctx.company_id:
                errors["partner_id"] = "Nhà cung cấp không hợp lệ"
        elif header.payment_method == "credit" and not header.partner_name:
            errors["partner_id"] = (
                "Mua chịu (ghi có TK 331) phải chọn nhà cung cấp"
            )
        if header.due_date and header.due_date < header.date:
            errors["due_date"] = (
                "Hạn thanh toán không được trước ngày hạch toán"
            )
    if errors or header is None:
        return None, errors, lines
    return PurchaseInput(kind=kind, header=header, lines=lines), {}, lines


def _resolve_partner(db: Session, ctx, inp: PurchaseInput) -> Partner | None:
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
            role="supplier",
        )
    return None


def build_entry(
    inp: PurchaseInput, doc_id: int, partner: Partner | None
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
        if is_return:  # Dr payable/cash, Cr goods/VAT
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

    for ln in inp.lines:
        cost = ln.amount + (0 if ln.deductible else ln.vat_amount)
        if cost > 0:
            lines.append(
                pair(ln.account, cost, ln.description, ln.item_id, ln.quantity)
            )
    name = (partner.name if partner else h.partner_name) or ""
    tax_code = h.tax_code or (partner.tax_code if partner else "")
    vat: list[js.VatIn] = []
    groups = vat_groups(
        inp.lines,
        key=lambda ln: (ln.vat_rate, ln.deductible, input_vat_account(ln)),
    )
    for (rate, deductible, vat_acc), g in groups.items():
        if deductible and g.vat > 0:
            lines.append(pair(vat_acc, g.vat, f"Thuế GTGT đầu vào {rate}%"))
        vat.append(
            js.VatIn(
                direction="in",
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
                vat_account=vat_acc if deductible else "",
                deductible=deductible,
                group="adjust_down" if is_return else "normal",
            )
        )
    label = "Trả lại hàng mua" if is_return else "Mua hàng"
    desc = h.description or f"{label} – {name or 'người bán'}" + (
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
        contact_name=h.contact_name or name,
        contact_address=h.address or (partner.address if partner else ""),
        ref_no=invoice_ref(h.invoice_series, h.invoice_no),
        source="purchases",
        source_id=doc_id,
        meta={"doc": "purchases", "kind": inp.kind},
        vat=vat,
    )


def _apply_doc(
    doc: PurchaseDoc, inp: PurchaseInput, partner: Partner | None
) -> None:
    h = inp.header
    doc.kind = inp.kind
    doc.partner_id = partner.id if partner else None
    doc.seller_name = (partner.name if partner else h.partner_name) or ""
    doc.seller_address = h.address or (partner.address if partner else "")
    doc.seller_tax_code = h.tax_code or (partner.tax_code if partner else "")
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
    doc.goods_amount, doc.vat_amount, doc.total_amount = (
        inp.goods_amount,
        inp.vat_amount,
        inp.total_amount,
    )
    doc.original_entry_id = h.original_entry_id
    doc.note = h.note
    if inp.einvoice_key:
        doc.einvoice_key = inp.einvoice_key
    doc.lines.clear()
    for n, ln in enumerate(inp.lines, start=1):
        doc.lines.append(
            PurchaseLine(
                company_id=doc.company_id,
                line_no=n,
                item_id=ln.item_id,
                description=ln.description,
                unit=ln.unit,
                quantity=ln.quantity,
                unit_price=ln.unit_price,
                amount=ln.amount,
                vat_rate=ln.vat_rate,
                vat_amount=ln.vat_amount,
                deductible=ln.deductible,
                debit_account=ln.account,
                warehouse_id=ln.warehouse_id,
            )
        )


def create(
    db: Session,
    ctx,
    inp: PurchaseInput,
    *,
    post: bool = False,
    commit: bool = True,
) -> PurchaseDoc:
    if inp.einvoice_key and db.scalar(
        select(PurchaseDoc.id).where(
            PurchaseDoc.company_id == ctx.company_id,
            PurchaseDoc.einvoice_key == inp.einvoice_key,
        )
    ):
        raise BusinessError("Hóa đơn này đã được nhập trước đó")
    partner = _resolve_partner(db, ctx, inp)
    doc = PurchaseDoc(
        company_id=ctx.company_id, kind=inp.kind, created_at=_now()
    )
    db.add(doc)
    _apply_doc(doc, inp, partner)
    db.flush()
    entry = js.create_entry(
        db, ctx, build_entry(inp, doc.id, partner), commit=False
    )
    doc.entry_id = entry.id
    if commit:
        db.commit()
    if post:
        js.post_entry(db, ctx, entry)
    return doc


def update(
    db: Session, ctx, doc: PurchaseDoc, inp: PurchaseInput
) -> PurchaseDoc:
    entry = db.get(JournalEntry, doc.entry_id)
    if entry is None or not entry.is_draft:
        raise BusinessError(
            "Chứng từ đã ghi sổ không được sửa. Hãy hủy (kỳ đang mở) "
            "hoặc lập chứng từ điều chỉnh."
        )
    if inp.kind != doc.kind:
        raise BusinessError("Không đổi được loại chứng từ")
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
) -> tuple[JournalEntry, PurchaseDoc | None]:
    entry = js.get_entry(db, ctx, entry_id, doc_types=tuple(DOC_TYPE.values()))
    doc = db.scalar(
        select(PurchaseDoc).where(
            PurchaseDoc.company_id == ctx.company_id,
            PurchaseDoc.entry_id == entry.id,
        )
    )
    return entry, doc


def doc_to_form(doc: PurchaseDoc) -> tuple[dict, list[dict]]:
    form = {
        "date": doc.entry.date if doc.entry else None,
        "invoice_date": doc.invoice_date,
        "partner_id": doc.partner_id or "",
        "partner_label": (doc.partner.label if doc.partner else ""),
        "partner_name": doc.seller_name,
        "address": doc.seller_address,
        "tax_code": doc.seller_tax_code,
        "contact_name": doc.entry.contact_name if doc.entry else "",
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
    lines = [
        TradeLine(
            description=ln.description,
            amount=ln.amount,
            vat_rate=ln.vat_rate,
            vat_amount=ln.vat_amount,
            item_id=ln.item_id,
            unit=ln.unit,
            quantity=ln.quantity,
            unit_price=ln.unit_price,
            account=ln.debit_account,
            deductible=ln.deductible,
            item_label=ln.item.label if ln.item else "",
            warehouse_id=ln.warehouse_id,
        ).grid_dict()
        for ln in doc.lines
    ]
    return form, lines


def return_defaults(
    db: Session, ctx, entry_id: int
) -> tuple[dict, list[dict]]:
    entry, doc = get_doc(db, ctx, entry_id)
    if doc is None or doc.kind != PURCHASE or not entry.is_posted:
        raise BusinessError(
            "Chỉ lập trả lại hàng từ chứng từ mua hàng đã ghi sổ"
        )
    form, lines = doc_to_form(doc)
    form["original_entry_id"] = entry.id
    form["date"] = None
    form["description"] = f"Trả lại hàng mua theo {entry.number}"
    form["invoice_no"], form["invoice_series"] = "", ""
    return form, lines


@dataclass
class ListFilters:
    kind: str = PURCHASE
    q: str = ""
    status: str = ""
    start: date | None = None
    end: date | None = None
    payment: str = ""
    partner_id: int | None = None


def list_docs(
    db: Session, ctx, f: ListFilters, *, limit: int = 50, offset: int = 0
):
    je, pd = JournalEntry, PurchaseDoc
    stmt = (
        select(pd, je)
        .join(je, je.id == pd.entry_id)
        .where(pd.company_id == ctx.company_id, pd.kind == f.kind)
    )
    if f.q:
        term = f.q.strip().lower()
        stmt = stmt.where(
            or_(
                like_contains(ulower(je.number), term),
                like_contains(ulower(pd.seller_name), term),
                like_contains(ulower(pd.invoice_no), term),
                like_contains(ulower(je.description), term),
                like_prefix(pd.seller_tax_code, f.q.strip()),
            )
        )
    if f.status:
        stmt = stmt.where(je.status == f.status)
    if f.start:
        stmt = stmt.where(je.date >= f.start)
    if f.end:
        stmt = stmt.where(je.date <= f.end)
    if f.payment:
        stmt = stmt.where(pd.payment_method == f.payment)
    if f.partner_id:
        stmt = stmt.where(pd.partner_id == f.partner_id)
    sub = stmt.subquery()
    totals = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(sub.c.goods_amount), 0),
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
        select(PurchaseDoc).where(PurchaseDoc.entry_id == entry_id)
    ):
        db.delete(doc)
