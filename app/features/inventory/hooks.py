"""Sync stock movements with engine documents.

Covers stock receipts, sales, purchases, returns and adjustments.

Runs inside the posting/cancellation transaction (posting_validator):
creates/deletes StockMove rows, then checks negative stock
retroactively – on a violation it raises BusinessError and the engine
rolls back the whole document.

The sales/purchases models (group C) are imported INSIDE functions and
defensively: if the module or a required column is missing, the sync
is skipped.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select

from app.core.registry import on, posting_validator
from app.features.inventory.models import (
    CANCELLED,
    POSTED,
    RECEIPT,
    SRC_PURCHASES,
    SRC_SALES,
    StockDoc,
    StockMove,
)
from app.features.items.models import Item

log = logging.getLogger(__name__)
ZERO = Decimal(0)

# Stock direction (in/out) by group C document type.
OUT_TYPES = {"BH", "TM"}
IN_TYPES = {"TL", "MH"}


def _trade_models(source: str):
    try:
        if source == SRC_SALES:
            from app.features.sales import models as m

            doc, line = (
                getattr(m, "SalesDoc", None),
                getattr(m, "SalesLine", None),
            )
        else:
            from app.features.purchases import models as m

            doc, line = (
                getattr(m, "PurchaseDoc", None),
                getattr(m, "PurchaseLine", None),
            )
    except Exception:  # module missing or failing to import
        return None, None
    if line is None:
        return None, None
    cols = set(line.__table__.c.keys())
    if not {"doc_id", "item_id", "quantity"} <= cols:
        return None, None
    return doc, line


def _payment_account(
    entry, stock_account: str, item_id: int | None
) -> str | None:
    """Return the counter account of a purchase receipt line.

    The credit side of the entry pair that debits the stock account.
    """
    best = None
    for ln in entry.lines:
        if ln.debit_account == stock_account:
            if ln.item_id == item_id:
                return ln.credit_account
            best = best or ln.credit_account
        elif ln.credit_account == stock_account and ln.item_id == item_id:
            return ln.debit_account
    return best


def _trade_moves(db, entry) -> list[StockMove] | None:
    from app.features.inventory import service

    _doc_model, line_model = _trade_models(entry.source)
    if line_model is None or entry.source_id is None:
        return None
    lines = list(
        db.scalars(
            select(line_model).where(line_model.doc_id == entry.source_id)
        )
    )
    doc_type = entry.doc_type
    if doc_type not in OUT_TYPES | IN_TYPES:
        return []
    items = {
        it.id: it
        for it in db.scalars(
            select(Item).where(
                Item.company_id == entry.company_id,
                Item.id.in_({ln.item_id for ln in lines if ln.item_id}),
            )
        )
    }
    default_wh = None
    moves = []
    for ln in lines:
        it = items.get(ln.item_id)
        qty = service.to_decimal(getattr(ln, "quantity", None), ZERO) or ZERO
        if it is None or not service.tracks(it) or qty <= 0:
            continue
        stock_acc = service._stock_acc(it)
        if entry.source == SRC_PURCHASES:
            debit = getattr(ln, "debit_account", None) or stock_acc
            if not str(debit).startswith("15"):
                # Bought for immediate use (expense, fixed asset…),
                # not stocked.
                continue
            stock_acc = debit
        wh_id = getattr(ln, "warehouse_id", None)
        if not wh_id:
            default_wh = default_wh or service.default_warehouse(
                db, entry.company_id
            )
            wh_id = default_wh.id
        common = dict(
            company_id=entry.company_id,
            entry_id=entry.id,
            source=entry.source,
            source_line_id=ln.id,
            doc_type=doc_type,
            number=entry.number or "",
            date=entry.date,
            item_id=it.id,
            warehouse_id=wh_id,
            stock_account=stock_acc,
            description=(
                entry.description or getattr(ln, "description", "") or it.name
            )[:500],
        )
        if entry.source == SRC_SALES:
            cogs = it.cogs_account or "632"
            if doc_type == "BH":
                moves.append(
                    StockMove(
                        **common,
                        qty_in=ZERO,
                        qty_out=qty,
                        amount=0,
                        valued=False,
                        contra_account=cogs,
                    )
                )
            else:  # TL – sales return: back in at average cost
                moves.append(
                    StockMove(
                        **common,
                        qty_in=qty,
                        qty_out=ZERO,
                        amount=0,
                        valued=False,
                        contra_account=cogs,
                    )
                )
        else:
            amount = int(getattr(ln, "amount", 0) or 0)
            if getattr(ln, "deductible", True) is False:
                amount += int(getattr(ln, "vat_amount", 0) or 0)
            contra = _payment_account(entry, stock_acc, it.id)
            if doc_type == "MH":
                moves.append(
                    StockMove(
                        **common,
                        qty_in=qty,
                        qty_out=ZERO,
                        amount=amount,
                        valued=True,
                        unit_cost=(Decimal(amount) / qty).quantize(
                            Decimal("0.01")
                        ),
                        contra_account=contra,
                    )
                )
            else:  # TM – purchase return: out at document value
                moves.append(
                    StockMove(
                        **common,
                        qty_in=ZERO,
                        qty_out=qty,
                        amount=amount,
                        valued=True,
                        unit_cost=(Decimal(amount) / qty).quantize(
                            Decimal("0.01")
                        ),
                        contra_account=contra,
                    )
                )
    return moves


def _receipt_moves(db, entry) -> list[StockMove] | None:
    from app.features.inventory import service

    doc = db.scalar(
        select(StockDoc).where(
            StockDoc.company_id == entry.company_id,
            StockDoc.entry_id == entry.id,
        )
    )
    if doc is None:
        return None
    items = {ln.item_id: db.get(Item, ln.item_id) for ln in doc.lines}
    doc.number = entry.number
    return service._doc_moves(doc, items, entry_id=entry.id)


def _reversal_moves(db, entry) -> list[StockMove] | None:
    """Build the stock movements of a reversing adjustment entry.

    They mirror the original document's movements in the opposite
    direction.
    """
    orig = list(
        db.scalars(
            select(StockMove).where(
                StockMove.company_id == entry.company_id,
                StockMove.entry_id == entry.reverses_id,
            )
        )
    )
    if not orig:
        return None
    out = []
    for m in orig:
        out.append(
            StockMove(
                company_id=m.company_id,
                entry_id=entry.id,
                source=m.source,
                source_line_id=m.source_line_id,
                doc_type=entry.doc_type,
                number=entry.number or "",
                date=entry.date,
                item_id=m.item_id,
                warehouse_id=m.warehouse_id,
                qty_in=m.qty_out,
                qty_out=m.qty_in,
                amount=m.amount if m.valued else 0,
                unit_cost=m.unit_cost if m.valued else None,
                valued=m.valued,
                stock_account=m.stock_account,
                contra_account=m.contra_account,
                description=f"Điều chỉnh {m.number}: {m.description}"[:500],
            )
        )
    return out


def _sync_receipt_status(db, entry, status: str) -> None:
    doc = db.scalar(
        select(StockDoc).where(
            StockDoc.company_id == entry.company_id,
            StockDoc.entry_id == entry.id,
        )
    )
    if doc is not None:
        doc.status = status
        doc.number = entry.number
        if status == POSTED:
            doc.posted_by, doc.posted_at = entry.posted_by, entry.posted_at
        else:
            doc.cancelled_by, doc.cancelled_at = (
                entry.cancelled_by,
                entry.cancelled_at,
            )
            doc.cancel_reason = entry.cancel_reason


def _stock_entry(entry) -> bool:
    if entry.source in (SRC_SALES, SRC_PURCHASES):
        return True
    return entry.source == "inventory" and entry.doc_type == "NK"


@posting_validator
def sync_entry_moves(db, ctx, entry, action: str) -> None:
    from app.features.inventory import service

    if not _stock_entry(entry):
        return
    if action == "post":
        if entry.reverses_id:
            moves = _reversal_moves(db, entry)
        elif entry.source == "inventory":
            moves = _receipt_moves(db, entry)
            _sync_receipt_status(db, entry, POSTED)
        else:
            moves = _trade_moves(db, entry)
        if not moves:
            return
        service._delete_moves(db, StockMove.entry_id == entry.id)
        db.add_all(moves)
        db.flush()
        service.check_negative_stock(
            db,
            entry.company_id,
            {(m.item_id, m.warehouse_id) for m in moves},
            entry.date,
            action=f"ghi sổ {entry.number or ''}".strip(),
        )
        service.mark_stale(db, entry.company_id, entry.date)
    elif action == "cancel":
        if entry.source == "inventory":
            _sync_receipt_status(db, entry, CANCELLED)
        keys = service._delete_moves(db, StockMove.entry_id == entry.id)
        if not keys:
            return
        db.flush()
        service.check_negative_stock(
            db,
            entry.company_id,
            keys,
            entry.date,
            action=f"hủy {entry.number}",
        )
        service.mark_stale(db, entry.company_id, entry.date)


@on("entry.deleted")
def delete_receipt_doc(db, ctx, entry) -> None:
    """Delete the stock document along with its draft receipt entry.

    Handles drafts deleted from the shared journal entry page.
    """
    if entry.source == "inventory" and entry.doc_type == "NK":
        doc = db.scalar(
            select(StockDoc).where(
                StockDoc.company_id == entry.company_id,
                StockDoc.entry_id == entry.id,
            )
        )
        if doc is not None and doc.kind == RECEIPT:
            db.delete(doc)
