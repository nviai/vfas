"""Materials, goods and services."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import snapshot, write_audit
from app.core.db import like_contains, ulower
from app.core.errors import BusinessError, NotFoundError
from app.features.items.models import (
    ITEM_KINDS,
    REVENUE_ACCOUNT,
    STOCK_ACCOUNT,
    VAT_RATES,
    Item,
)

FIELDS = (
    "code",
    "name",
    "unit",
    "kind",
    "stock_account",
    "revenue_account",
    "cogs_account",
    "expense_account",
    "vat_rate",
    "sale_price",
    "purchase_price",
    "note",
    "is_active",
)
KIND_PREFIX = {
    "goods": "HH",
    "material": "VT",
    "tool": "CC",
    "product": "TP",
    "service": "DV",
}


@dataclass
class ItemInput:
    name: str
    code: str = ""
    unit: str = ""
    kind: str = "goods"
    stock_account: str | None = None
    revenue_account: str | None = None
    cogs_account: str | None = "632"
    expense_account: str | None = None
    vat_rate: str = "10"
    sale_price: Decimal = Decimal(0)
    purchase_price: Decimal = Decimal(0)
    note: str = ""
    is_active: bool = True


def get_item(db: Session, company_id: int, item_id: int) -> Item:
    it = db.get(Item, item_id)
    if it is None or it.company_id != company_id:
        raise NotFoundError("Không tìm thấy vật tư hàng hóa")
    return it


def search(
    db: Session,
    company_id: int,
    q: str = "",
    *,
    kind: str | None = None,
    active: bool | None = True,
    limit: int = 20,
    offset: int = 0,
) -> list[Item]:
    stmt = select(Item).where(Item.company_id == company_id)
    if q:
        term = q.strip().lower()
        stmt = stmt.where(
            or_(
                like_contains(ulower(Item.name), term),
                like_contains(ulower(Item.code), term),
            )
        )
    if kind:
        stmt = stmt.where(Item.kind == kind)
    if active is not None:
        stmt = stmt.where(Item.is_active.is_(active))
    return list(
        db.scalars(stmt.order_by(Item.code).limit(limit).offset(offset))
    )


def next_code(db: Session, company_id: int, kind: str) -> str:
    prefix = KIND_PREFIX.get(kind, "HH")
    n = 0
    for c in db.scalars(
        select(Item.code).where(
            Item.company_id == company_id, Item.code.like(f"{prefix}%")
        )
    ):
        tail = c[len(prefix) :]
        if tail.isdigit():
            n = max(n, int(tail))
    return f"{prefix}{n + 1:04d}"


def _normalize(
    db: Session, company_id: int, inp: ItemInput, existing: Item | None
) -> ItemInput:
    if not inp.name.strip():
        raise BusinessError("Nhập tên vật tư hàng hóa")
    if inp.kind not in ITEM_KINDS:
        raise BusinessError("Loại vật tư hàng hóa không hợp lệ")
    if inp.vat_rate not in VAT_RATES:
        raise BusinessError("Thuế suất GTGT không hợp lệ")
    if inp.sale_price < 0 or inp.purchase_price < 0:
        raise BusinessError("Đơn giá không được âm")
    inp.stock_account = inp.stock_account or STOCK_ACCOUNT.get(inp.kind)
    inp.revenue_account = inp.revenue_account or REVENUE_ACCOUNT.get(inp.kind)
    if inp.kind == "service":
        inp.stock_account = None
    code = inp.code.strip()
    if code:
        dup = db.scalar(
            select(Item.id).where(
                Item.company_id == company_id, Item.code == code
            )
        )
        if dup and (existing is None or dup != existing.id):
            raise BusinessError(f"Mã {code} đã có")
    return inp


def create(db: Session, ctx, inp: ItemInput, *, commit: bool = True) -> Item:
    inp = _normalize(db, ctx.company_id, inp, None)
    it = Item(company_id=ctx.company_id)
    for f in FIELDS:
        setattr(it, f, getattr(inp, f))
    it.name = inp.name.strip()[:255]
    it.code = inp.code.strip() or next_code(db, ctx.company_id, inp.kind)
    db.add(it)
    db.flush()
    write_audit(
        db,
        ctx,
        action="item.create",
        entity_type="item",
        entity_id=it.id,
        summary=f"Thêm {ITEM_KINDS[it.kind].lower()} {it.code} – {it.name}",
    )
    if commit:
        db.commit()
    return it


def update(db: Session, ctx, it: Item, inp: ItemInput) -> Item:
    if it.company_id != ctx.company_id:
        raise NotFoundError()
    inp = _normalize(db, ctx.company_id, inp, it)
    before = snapshot(it, FIELDS)
    for f in FIELDS:
        setattr(it, f, getattr(inp, f))
    it.code = inp.code.strip() or it.code
    after = snapshot(it, FIELDS)
    changed = {k for k in after if after[k] != before[k]}
    if changed:
        write_audit(
            db,
            ctx,
            action="item.update",
            entity_type="item",
            entity_id=it.id,
            summary=f"Sửa {it.code}",
            before={k: before[k] for k in changed},
            after={k: after[k] for k in changed},
        )
    db.commit()
    return it
