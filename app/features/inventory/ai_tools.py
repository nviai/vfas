"""AI tools of the inventory module (read-only)."""

from __future__ import annotations

from sqlalchemy import or_, select

from app.core.dates import parse_date, today
from app.core.db import like_contains, ulower
from app.core.registry import ai_tool
from app.features.inventory import reports, service
from app.features.inventory.models import Warehouse
from app.features.items.models import Item


def _item(db, company_id: int, code: str | None) -> Item | None:
    if not code:
        return None
    code = code.strip()
    it = db.scalar(
        select(Item).where(Item.company_id == company_id, Item.code == code)
    )
    if it is None:
        it = db.scalar(
            select(Item).where(
                Item.company_id == company_id,
                like_contains(ulower(Item.name), code.lower()),
            )
        )
    return it


def _warehouse(db, company_id: int, ref: str | None) -> Warehouse | None:
    if not ref:
        return None
    return db.scalar(
        select(Warehouse).where(
            Warehouse.company_id == company_id,
            or_(
                Warehouse.code == ref.strip().upper(),
                like_contains(ulower(Warehouse.name), ref.strip().lower()),
            ),
        )
    )


@ai_tool(
    name="get_stock_on_hand",
    description=(
        "Inventory on hand (quantity and known value in VND) by item and "
        "warehouse at a date. "
        "Values of issues are final only after month-end costing "
        "has been run."
    ),
    parameters={
        "type": "object",
        "properties": {
            "item_code": {
                "type": "string",
                "description": "Item code or part of the name (optional)",
            },
            "warehouse": {
                "type": "string",
                "description": "Warehouse code or name (optional)",
            },
            "date": {
                "type": "string",
                "description": "YYYY-MM-DD, default today",
            },
        },
    },
    perm="inventory.view",
    label="Xem tồn kho",
)
def get_stock_on_hand(
    db,
    ctx,
    item_code: str | None = None,
    warehouse: str | None = None,
    date: str | None = None,
) -> dict:
    d = parse_date(date) or today()
    it = _item(db, ctx.company_id, item_code)
    if item_code and it is None:
        return {"error": f"Không tìm thấy hàng '{item_code}'"}
    wh = _warehouse(db, ctx.company_id, warehouse)
    rows = service.on_hand(
        db,
        ctx.company_id,
        upto=d,
        item_id=it.id if it else None,
        warehouse_id=wh.id if wh else None,
    )
    items = {
        i.id: i
        for i in db.scalars(
            select(Item).where(Item.company_id == ctx.company_id)
        )
    }
    whs = {
        w.id: w
        for w in db.scalars(
            select(Warehouse).where(Warehouse.company_id == ctx.company_id)
        )
    }
    out = [
        {
            "item_code": items[r["item_id"]].code,
            "item_name": items[r["item_id"]].name,
            "unit": items[r["item_id"]].unit,
            "warehouse": whs[r["warehouse_id"]].name,
            "quantity": str(r["quantity"]),
            "value": r["amount"],
        }
        for r in rows[:200]
    ]
    return {
        "date": d.isoformat(),
        "rows": out,
        "total_value": sum(r["amount"] for r in rows),
    }


@ai_tool(
    name="get_inventory_movements",
    description=(
        "Stock card of one item: receipts, issues and running balance "
        "(quantity and value) between two dates."
    ),
    parameters={
        "type": "object",
        "required": ["item_code", "start", "end"],
        "properties": {
            "item_code": {"type": "string"},
            "start": {"type": "string", "description": "YYYY-MM-DD"},
            "end": {"type": "string", "description": "YYYY-MM-DD"},
            "warehouse": {"type": "string"},
        },
    },
    perm="inventory.view",
    label="Xem thẻ kho",
)
def get_inventory_movements(
    db, ctx, item_code: str, start: str, end: str, warehouse: str | None = None
) -> dict:
    it = _item(db, ctx.company_id, item_code)
    if it is None:
        return {"error": f"Không tìm thấy hàng '{item_code}'"}
    wh = _warehouse(db, ctx.company_id, warehouse)
    led = reports.item_ledger(
        db,
        ctx.company_id,
        it.id,
        parse_date(start),
        parse_date(end),
        wh.id if wh else None,
    )
    return {
        "item": {"code": it.code, "name": it.name, "unit": it.unit},
        "opening": {
            "quantity": str(led.opening_qty),
            "value": led.opening_val,
        },
        "rows": [
            {
                "date": r.date.isoformat(),
                "number": r.number,
                "description": r.description,
                "in_qty": str(r.qty_in),
                "out_qty": str(r.qty_out),
                "in_value": r.amount_in,
                "out_value": r.amount_out,
                "balance_qty": str(r.balance_qty),
                "balance_value": r.balance_val,
                "cost_pending": r.pending,
            }
            for r in led.rows[:300]
        ],
        "closing": {
            "quantity": str(led.closing_qty),
            "value": led.closing_val,
        },
    }
