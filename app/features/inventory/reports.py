"""Stock ledgers.

Stock card (S08-DNN), detailed ledger of materials, tools, products
and goods (S06-DNN), and the detailed summary of materials, tools,
products and goods – receipts, issues, balance (S07-DNN).

Ledger order: date, receipts before issues within a day, then entry
order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.registry import doc_url
from app.features.inventory.models import (
    SRC_OPENING,
    SRC_TRANSFER,
    StockDoc,
    StockMove,
    Warehouse,
)
from app.features.items.models import Item

ZERO = Decimal(0)
FORMS = {
    "card": ("S08-DNN", "THẺ KHO"),
    "ledger": (
        "S06-DNN",
        "SỔ CHI TIẾT VẬT LIỆU, DỤNG CỤ (SẢN PHẨM, HÀNG HÓA)",
    ),
    "summary": (
        "S07-DNN",
        "BẢNG TỔNG HỢP CHI TIẾT VẬT LIỆU, DỤNG CỤ, SẢN PHẨM, HÀNG HÓA",
    ),
}


def _d(v) -> Decimal:
    if v is None:
        return ZERO
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _signed_amount():
    return case(
        (StockMove.qty_in > 0, StockMove.amount), else_=-StockMove.amount
    )


def move_url(m: StockMove, doc_urls: dict[int, str]) -> str | None:
    if m.source == SRC_OPENING:
        return "/inventory/opening"
    if m.stock_doc_id and m.stock_doc_id in doc_urls:
        return doc_urls[m.stock_doc_id]
    if m.entry_id:
        return doc_url(m.doc_type, m.entry_id)
    return None


@dataclass
class LedgerRow:
    date: date
    number: str
    doc_type: str
    description: str
    contra: str | None
    unit_cost: Decimal | None
    qty_in: Decimal
    qty_out: Decimal
    amount_in: int
    amount_out: int
    balance_qty: Decimal
    balance_val: int
    pending: bool  # issue not costed yet
    url: str | None
    warehouse: str = ""


@dataclass
class ItemLedger:
    item: Item
    warehouse: Warehouse | None
    start: date
    end: date
    opening_qty: Decimal = ZERO
    opening_val: int = 0
    rows: list[LedgerRow] = field(default_factory=list)
    in_qty: Decimal = ZERO
    out_qty: Decimal = ZERO
    in_val: int = 0
    out_val: int = 0

    @property
    def closing_qty(self) -> Decimal:
        return self.opening_qty + self.in_qty - self.out_qty

    @property
    def closing_val(self) -> int:
        return self.opening_val + self.in_val - self.out_val

    @property
    def pending(self) -> bool:
        return any(r.pending for r in self.rows)


def item_ledger(
    db: Session,
    company_id: int,
    item_id: int,
    start: date,
    end: date,
    warehouse_id: int | None = None,
) -> ItemLedger:
    item = db.get(Item, item_id)
    wh = db.get(Warehouse, warehouse_id) if warehouse_id else None
    base = [StockMove.company_id == company_id, StockMove.item_id == item_id]
    if warehouse_id:
        base.append(StockMove.warehouse_id == warehouse_id)
    q, v = db.execute(
        select(
            func.sum(StockMove.qty_in - StockMove.qty_out),
            func.sum(_signed_amount()),
        ).where(*base, StockMove.date < start)
    ).one()
    led = ItemLedger(
        item=item,
        warehouse=wh,
        start=start,
        end=end,
        opening_qty=_d(q),
        opening_val=int(v or 0),
    )
    period = [*base, StockMove.date >= start, StockMove.date <= end]
    if not warehouse_id:
        # Internal transfers do not change company-wide stock.
        period.append(StockMove.source != SRC_TRANSFER)
    moves = list(
        db.scalars(
            select(StockMove)
            .where(*period)
            .order_by(StockMove.date, StockMove.qty_in.desc(), StockMove.id)
        )
    )
    doc_ids = {m.stock_doc_id for m in moves if m.stock_doc_id}
    doc_urls = (
        {
            d.id: d.url
            for d in db.scalars(
                select(StockDoc).where(StockDoc.id.in_(doc_ids))
            )
        }
        if doc_ids
        else {}
    )
    whs = {
        w.id: w.name
        for w in db.scalars(
            select(Warehouse).where(Warehouse.company_id == company_id)
        )
    }
    bq, bv = led.opening_qty, led.opening_val
    for m in moves:
        qi, qo = _d(m.qty_in), _d(m.qty_out)
        ai = int(m.amount) if qi > 0 else 0
        ao = int(m.amount) if qo > 0 else 0
        bq += qi - qo
        bv += ai - ao
        led.in_qty += qi
        led.out_qty += qo
        led.in_val += ai
        led.out_val += ao
        led.rows.append(
            LedgerRow(
                date=m.date,
                number=m.number,
                doc_type=m.doc_type,
                description=m.description,
                contra=m.contra_account,
                unit_cost=m.unit_cost,
                qty_in=qi,
                qty_out=qo,
                amount_in=ai,
                amount_out=ao,
                balance_qty=bq,
                balance_val=bv,
                pending=(not m.valued and not m.costed),
                url=move_url(m, doc_urls),
                warehouse=whs.get(m.warehouse_id, ""),
            )
        )
    return led


@dataclass
class SummaryRow:
    item: Item
    warehouse: Warehouse | None
    opening_qty: Decimal
    opening_val: int
    in_qty: Decimal
    in_val: int
    out_qty: Decimal
    out_val: int

    @property
    def closing_qty(self) -> Decimal:
        return self.opening_qty + self.in_qty - self.out_qty

    @property
    def closing_val(self) -> int:
        return self.opening_val + self.in_val - self.out_val


@dataclass
class Summary:
    start: date
    end: date
    rows: list[SummaryRow]
    by_account: dict[str, int]  # closing value per stock account
    pending: int  # issue lines not costed yet in the period

    def total(self, attr: str):
        return sum(
            (getattr(r, attr) for r in self.rows), ZERO if "qty" in attr else 0
        )


def summary(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    *,
    warehouse_id: int | None = None,
    by_warehouse: bool = False,
) -> Summary:
    keys = [StockMove.item_id] + (
        [StockMove.warehouse_id] if by_warehouse else []
    )
    in_period = (StockMove.date >= start) & (StockMove.date <= end)
    if not by_warehouse and not warehouse_id:
        # Company-wide totals: internal transfers are not
        # receipts/issues.
        in_period = in_period & (StockMove.source != SRC_TRANSFER)
    stmt = (
        select(
            *keys,
            func.sum(
                case(
                    (
                        StockMove.date < start,
                        StockMove.qty_in - StockMove.qty_out,
                    ),
                    else_=0,
                )
            ),
            func.sum(
                case((StockMove.date < start, _signed_amount()), else_=0)
            ),
            func.sum(case((in_period, StockMove.qty_in), else_=0)),
            func.sum(
                case(
                    (in_period & (StockMove.qty_in > 0), StockMove.amount),
                    else_=0,
                )
            ),
            func.sum(case((in_period, StockMove.qty_out), else_=0)),
            func.sum(
                case(
                    (in_period & (StockMove.qty_out > 0), StockMove.amount),
                    else_=0,
                )
            ),
        )
        .where(StockMove.company_id == company_id, StockMove.date <= end)
        .group_by(*keys)
    )
    if warehouse_id:
        stmt = stmt.where(StockMove.warehouse_id == warehouse_id)
    items = {
        it.id: it
        for it in db.scalars(select(Item).where(Item.company_id == company_id))
    }
    whs = {
        w.id: w
        for w in db.scalars(
            select(Warehouse).where(Warehouse.company_id == company_id)
        )
    }
    rows = []
    for r in db.execute(stmt):
        iid = r[0]
        wid = r[1] if by_warehouse else None
        oq, ov, iq, iv, xq, xv = r[-6:]
        row = SummaryRow(
            item=items[iid],
            warehouse=whs.get(wid) if wid else None,
            opening_qty=_d(oq),
            opening_val=int(ov or 0),
            in_qty=_d(iq),
            in_val=int(iv or 0),
            out_qty=_d(xq),
            out_val=int(xv or 0),
        )
        if not any(
            (
                row.opening_qty,
                row.opening_val,
                row.in_qty,
                row.out_qty,
                row.in_val,
                row.out_val,
            )
        ):
            continue
        rows.append(row)
    rows.sort(
        key=lambda r: (
            r.item.stock_account or "",
            r.item.code,
            r.warehouse.code if r.warehouse else "",
        )
    )
    by_account: dict[str, int] = {}
    for r in rows:
        acc = r.item.stock_account or "156"
        by_account[acc] = by_account.get(acc, 0) + r.closing_val
    pq = select(func.count(StockMove.id)).where(
        StockMove.company_id == company_id,
        in_period,
        StockMove.valued.is_(False),
        StockMove.costed.is_(False),
    )
    if warehouse_id:
        pq = pq.where(StockMove.warehouse_id == warehouse_id)
    return Summary(
        start=start,
        end=end,
        rows=rows,
        by_account=by_account,
        pending=int(db.scalar(pq) or 0),
    )


def stock_items(db: Session, company_id: int) -> list[Item]:
    """Items with stock movements (for the stock card/ledger picker)."""
    ids = (
        select(StockMove.item_id)
        .where(StockMove.company_id == company_id)
        .distinct()
    )
    return list(
        db.scalars(
            select(Item)
            .where(Item.company_id == company_id, Item.id.in_(ids))
            .order_by(Item.code)
        )
    )
