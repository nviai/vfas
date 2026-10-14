"""Inventory operations.

Warehouses, stock receipts/issues/transfers, stock movements,
negative-stock blocking and opening stock.

Negative stock is checked retroactively per (item, warehouse) by DAY:
within one day receipts count before issues, i.e. the closing balance
of each day (from the document date onwards) must not be negative.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.dates import fmt_date
from app.core.errors import BusinessError, NotFoundError
from app.core.money import format_vnd
from app.core.web import fmt_qty
from app.features.accounts.models import Account
from app.features.inventory.models import (
    CANCELLED,
    DRAFT,
    ISSUE,
    KIND_DOC_TYPE,
    KIND_LABELS,
    POSTED,
    RECEIPT,
    SRC_ISSUE,
    SRC_OPENING,
    SRC_RECEIPT,
    SRC_TRANSFER,
    TRANSFER,
    CostingRun,
    StockDoc,
    StockDocLine,
    StockMove,
    Warehouse,
)
from app.features.items.models import STOCK_ACCOUNT, Item
from app.features.journal import locks
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.org.models import Company
from app.features.partners.models import Partner

ZERO = Decimal(0)
MAX_QTY = Decimal("1000000000")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def money_round(v: Decimal) -> int:
    return int(Decimal(v).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_decimal(v, default: Decimal | None = None) -> Decimal | None:
    """Parse a quantity or unit price.

    Accepts '1.234,5' (VN), '1234.5' and 1234.5 (JS).
    """
    if v is None or v == "":
        return default
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(str(v))
    s = str(v).strip().replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise BusinessError(f"Số không hợp lệ: {v}") from e


def tracks(item: Item) -> bool:
    return item.kind in STOCK_ACCOUNT


# --------------------------------------------------------- warehouses


def list_warehouses(
    db: Session, company_id: int, *, active: bool | None = True
) -> list[Warehouse]:
    stmt = select(Warehouse).where(Warehouse.company_id == company_id)
    if active is not None:
        stmt = stmt.where(Warehouse.is_active.is_(active))
    return list(
        db.scalars(stmt.order_by(Warehouse.is_default.desc(), Warehouse.code))
    )


def get_warehouse(db: Session, company_id: int, wh_id: int) -> Warehouse:
    w = db.get(Warehouse, wh_id)
    if w is None or w.company_id != company_id:
        raise NotFoundError("Không tìm thấy kho")
    return w


def default_warehouse(db: Session, company_id: int) -> Warehouse:
    """Return the default warehouse.

    Creates "Kho chính" when the company has no warehouse yet.
    """
    w = db.scalar(
        select(Warehouse).where(
            Warehouse.company_id == company_id,
            Warehouse.is_default.is_(True),
            Warehouse.is_active.is_(True),
        )
    )
    if w is None:
        w = db.scalar(
            select(Warehouse)
            .where(
                Warehouse.company_id == company_id,
                Warehouse.is_active.is_(True),
            )
            .order_by(Warehouse.id)
        )
    if w is None:
        w = Warehouse(
            company_id=company_id,
            code="KHO01",
            name="Kho chính",
            is_default=True,
            is_active=True,
        )
        db.add(w)
        db.flush()
    return w


def save_warehouse(
    db: Session,
    ctx,
    *,
    wh: Warehouse | None,
    code: str,
    name: str,
    address: str = "",
    keeper: str = "",
    is_default: bool = False,
    is_active: bool = True,
    note: str = "",
) -> Warehouse:
    code, name = (code or "").strip().upper(), (name or "").strip()
    if not code or not name:
        raise BusinessError("Nhập mã và tên kho")
    dup = db.scalar(
        select(Warehouse.id).where(
            Warehouse.company_id == ctx.company_id, Warehouse.code == code
        )
    )
    if dup and (wh is None or dup != wh.id):
        raise BusinessError(f"Mã kho {code} đã có")
    created = wh is None
    if wh is None:
        wh = Warehouse(company_id=ctx.company_id)
        db.add(wh)
    elif wh.company_id != ctx.company_id:
        raise NotFoundError()
    wh.code, wh.name, wh.address, wh.keeper, wh.note = (
        code,
        name,
        address.strip(),
        keeper.strip(),
        note.strip(),
    )
    wh.is_active = is_active
    if is_default:
        db.execute(
            update(Warehouse)
            .where(Warehouse.company_id == ctx.company_id)
            .values(is_default=False)
        )
        wh.is_default = True
        wh.is_active = True
    elif not created and wh.is_default and not is_default:
        wh.is_default = False
    db.flush()
    write_audit(
        db,
        ctx,
        action="warehouse.create" if created else "warehouse.update",
        entity_type="warehouse",
        entity_id=wh.id,
        summary=("Thêm kho " if created else "Sửa kho ")
        + f"{wh.code} – {wh.name}",
    )
    db.commit()
    return wh


# ---------------------------------------------------- stock documents


@dataclass
class LineInput:
    item_id: int
    quantity: Decimal
    unit_price: Decimal = ZERO
    amount: int = 0
    description: str = ""
    unit: str = ""


@dataclass
class DocInput:
    kind: str
    date: date
    warehouse_id: int
    lines: list[LineInput]
    doc_date: date | None = None
    to_warehouse_id: int | None = None
    partner_id: int | None = None
    contact_name: str = ""
    counter_account: str | None = None
    description: str = ""
    ref_no: str = ""
    attached_docs: int = 0
    meta: dict = field(default_factory=dict)


def get_doc(
    db: Session, ctx, doc_id: int, kind: str | None = None
) -> StockDoc:
    d = db.get(StockDoc, doc_id)
    if (
        d is None
        or d.company_id != ctx.company_id
        or (kind and d.kind != kind)
    ):
        raise NotFoundError("Không tìm thấy chứng từ kho")
    return d


def doc_for_entry(db: Session, ctx, entry_id: int) -> StockDoc:
    d = db.scalar(
        select(StockDoc).where(
            StockDoc.company_id == ctx.company_id,
            StockDoc.entry_id == entry_id,
        )
    )
    if d is None:
        raise NotFoundError("Không tìm thấy phiếu nhập kho")
    return d


def _validate(db: Session, company: Company, inp: DocInput) -> dict[int, Item]:
    errors: list[str] = []
    if inp.kind not in KIND_DOC_TYPE:
        errors.append("Loại chứng từ kho không hợp lệ")
    if inp.date is None:
        errors.append("Nhập ngày chứng từ")
    elif inp.date < company.start_date:
        errors.append(
            "Ngày chứng từ trước ngày bắt đầu hạch toán "
            f"{fmt_date(company.start_date)}. "
            "Tồn kho trước ngày này nhập vào Tồn kho đầu kỳ."
        )
    if inp.doc_date and inp.date and inp.doc_date > inp.date:
        errors.append("Ngày chứng từ không được sau ngày hạch toán")
    wh_ids = {inp.warehouse_id} | (
        {inp.to_warehouse_id} if inp.to_warehouse_id else set()
    )
    whs = {
        w.id: w
        for w in db.scalars(
            select(Warehouse).where(
                Warehouse.company_id == company.id, Warehouse.id.in_(wh_ids)
            )
        )
    }
    if inp.warehouse_id not in whs:
        errors.append("Chọn kho")
    elif not whs[inp.warehouse_id].is_active:
        errors.append("Kho đang ngừng sử dụng")
    if inp.kind == TRANSFER:
        if not inp.to_warehouse_id or inp.to_warehouse_id not in whs:
            errors.append("Chọn kho nhận")
        elif inp.to_warehouse_id == inp.warehouse_id:
            errors.append("Kho nhận phải khác kho xuất")
    if inp.partner_id and not db.scalar(
        select(Partner.id).where(
            Partner.company_id == company.id, Partner.id == inp.partner_id
        )
    ):
        errors.append("Đối tượng không hợp lệ")
    if inp.kind in (RECEIPT, ISSUE):
        code = (inp.counter_account or "").strip()
        acc = (
            db.scalar(
                select(Account).where(
                    Account.company_id == company.id, Account.code == code
                )
            )
            if code
            else None
        )
        label = "TK Có (đối ứng)" if inp.kind == RECEIPT else "TK Nợ (đối ứng)"
        if not code:
            errors.append(f"Chọn {label}")
        elif acc is None:
            errors.append(f"TK {code} không có trong hệ thống tài khoản")
        elif not acc.is_postable:
            errors.append(
                f"TK {code} là tài khoản tổng hợp, hãy chọn tài khoản chi tiết"
            )
        elif code.startswith(("15",)) and inp.kind == ISSUE:
            errors.append(
                "TK Nợ của phiếu xuất không được là tài khoản hàng tồn kho "
                "(dùng chuyển kho)"
            )
    if not inp.lines:
        errors.append("Chứng từ phải có ít nhất một dòng hàng")
    item_ids = {ln.item_id for ln in inp.lines if ln.item_id}
    items = (
        {
            it.id: it
            for it in db.scalars(
                select(Item).where(
                    Item.company_id == company.id, Item.id.in_(item_ids)
                )
            )
        }
        if item_ids
        else {}
    )
    for n, ln in enumerate(inp.lines, start=1):
        it = items.get(ln.item_id)
        if it is None:
            errors.append(f"Dòng {n}: chọn vật tư hàng hóa")
            continue
        if not tracks(it):
            errors.append(
                f"Dòng {n}: {it.code} là dịch vụ, không theo dõi tồn kho"
            )
        if ln.quantity is None or ln.quantity <= 0:
            errors.append(f"Dòng {n}: số lượng phải lớn hơn 0")
        elif ln.quantity > MAX_QTY:
            errors.append(f"Dòng {n}: số lượng quá lớn")
        if inp.kind == RECEIPT:
            if ln.amount is None or ln.amount <= 0:
                errors.append(f"Dòng {n}: thành tiền phải lớn hơn 0")
            if ln.unit_price is not None and ln.unit_price < 0:
                errors.append(f"Dòng {n}: đơn giá không được âm")
    if errors:
        raise js.EntryValidationError(errors)
    return items


def _entry_input(
    db: Session, doc: StockDoc, items: dict[int, Item]
) -> js.EntryIn:
    """Build the journal entry for a stock receipt.

    Debits each item's stock account and credits the counter account.
    """
    lines = [
        js.LineIn(
            debit_account=items[ln.item_id].stock_account
            or STOCK_ACCOUNT[items[ln.item_id].kind],
            credit_account=doc.counter_account or "",
            amount=int(ln.amount),
            description=ln.description or items[ln.item_id].name,
            credit_partner_id=doc.partner_id
            if _partner_side(db, doc)
            else None,
            item_id=ln.item_id,
            quantity=ln.quantity,
        )
        for ln in doc.lines
    ]
    return js.EntryIn(
        doc_type="NK",
        date=doc.date,
        doc_date=doc.doc_date,
        lines=lines,
        description=doc.description or f"Nhập kho {doc.warehouse.name}",
        partner_id=doc.partner_id,
        contact_name=doc.contact_name,
        ref_no=doc.ref_no,
        attached_docs=doc.attached_docs,
        source="inventory",
        source_id=doc.id,
        meta={"stock_doc": doc.id},
    )


def _partner_side(db: Session, doc: StockDoc) -> bool:
    if not doc.partner_id or not doc.counter_account:
        return False
    mode = db.scalar(
        select(Account.partner_mode).where(
            Account.company_id == doc.company_id,
            Account.code == doc.counter_account,
        )
    )
    return mode in ("required", "optional")


def _apply(doc: StockDoc, inp: DocInput, items: dict[int, Item]) -> None:
    doc.kind = inp.kind
    doc.doc_type = KIND_DOC_TYPE[inp.kind]
    doc.date = inp.date
    doc.doc_date = inp.doc_date or inp.date
    doc.warehouse_id = inp.warehouse_id
    doc.to_warehouse_id = inp.to_warehouse_id if inp.kind == TRANSFER else None
    doc.partner_id = inp.partner_id
    doc.contact_name = (inp.contact_name or "").strip()[:200]
    doc.counter_account = (
        (inp.counter_account or "").strip() or None
        if inp.kind != TRANSFER
        else None
    )
    doc.description = (inp.description or "").strip()[:500]
    doc.ref_no = (inp.ref_no or "").strip()[:100]
    doc.attached_docs = max(0, int(inp.attached_docs or 0))
    doc.lines.clear()
    for n, ln in enumerate(inp.lines, start=1):
        it = items[ln.item_id]
        amount = int(ln.amount or 0) if inp.kind == RECEIPT else 0
        price = ln.unit_price if inp.kind == RECEIPT else ZERO
        if (
            inp.kind == RECEIPT
            and (price is None or price == 0)
            and ln.quantity
        ):
            price = (Decimal(amount) / ln.quantity).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        doc.lines.append(
            StockDocLine(
                company_id=doc.company_id,
                line_no=n,
                item_id=it.id,
                description=(ln.description or it.name)[:500],
                unit=(ln.unit or it.unit)[:30],
                quantity=ln.quantity,
                unit_price=price or ZERO,
                amount=amount,
            )
        )


def create_doc(
    db: Session, ctx, inp: DocInput, *, post: bool = False
) -> StockDoc:
    company = js.get_company(db, ctx.company_id)
    items = _validate(db, company, inp)
    doc = StockDoc(
        company_id=company.id,
        kind=inp.kind,
        doc_type=KIND_DOC_TYPE[inp.kind],
        date=inp.date,
        warehouse_id=inp.warehouse_id,
        status=DRAFT,
        created_by=ctx.user_id,
        created_at=_now(),
    )
    db.add(doc)
    db.flush()
    _apply(doc, inp, items)
    db.flush()
    db.refresh(doc, ["warehouse"])
    if inp.kind == RECEIPT:
        entry = js.create_entry(
            db, ctx, _entry_input(db, doc, items), commit=False
        )
        doc.entry_id = entry.id
        db.flush()
    write_audit(
        db,
        ctx,
        action="stock_doc.create",
        entity_type="stock_doc",
        entity_id=doc.id,
        summary=(
            f"Tạo {KIND_LABELS[doc.kind].lower()} nháp ({len(doc.lines)} dòng)"
        ),
    )
    db.commit()
    if post:
        return post_doc(db, ctx, doc)
    return doc


def update_doc(db: Session, ctx, doc: StockDoc, inp: DocInput) -> StockDoc:
    if doc.company_id != ctx.company_id:
        raise NotFoundError()
    if effective_status(db, doc) != DRAFT:
        raise BusinessError(
            "Chứng từ đã ghi sổ không được sửa. "
            "Hãy hủy (kỳ đang mở) rồi lập chứng từ mới."
        )
    if inp.kind != doc.kind:
        raise BusinessError("Không đổi được loại chứng từ kho")
    company = js.get_company(db, ctx.company_id)
    items = _validate(db, company, inp)
    _apply(doc, inp, items)
    doc.updated_at = _now()
    db.flush()
    db.refresh(doc, ["warehouse"])
    if doc.kind == RECEIPT and doc.entry_id:
        entry = js.get_entry(db, ctx, doc.entry_id)
        js.update_draft(
            db, ctx, entry, _entry_input(db, doc, items), commit=False
        )
    write_audit(
        db,
        ctx,
        action="stock_doc.update",
        entity_type="stock_doc",
        entity_id=doc.id,
        summary=f"Sửa {KIND_LABELS[doc.kind].lower()} nháp #{doc.id}",
    )
    db.commit()
    return doc


def delete_doc(db: Session, ctx, doc: StockDoc) -> None:
    if effective_status(db, doc) != DRAFT:
        raise BusinessError("Chỉ được xóa chứng từ nháp")
    if doc.kind == RECEIPT and doc.entry_id:
        entry = js.get_entry(db, ctx, doc.entry_id)
        # The entry.deleted hook removes the stock document.
        js.delete_draft(db, ctx, entry)
        return
    write_audit(
        db,
        ctx,
        action="stock_doc.delete",
        entity_type="stock_doc",
        entity_id=doc.id,
        summary=f"Xóa {KIND_LABELS[doc.kind].lower()} nháp #{doc.id}",
    )
    db.delete(doc)
    db.commit()


def effective_status(db: Session, doc: StockDoc) -> str:
    if doc.kind == RECEIPT and doc.entry_id:
        st = db.scalar(
            select(JournalEntry.status).where(JournalEntry.id == doc.entry_id)
        )
        return st or doc.status
    return doc.status


def _next_doc_posting_seq(db: Session, company_id: int) -> int:
    return (
        int(
            db.scalar(
                select(func.coalesce(func.max(StockDoc.posting_seq), 0)).where(
                    StockDoc.company_id == company_id
                )
            )
            or 0
        )
        + 1
    )


def post_doc(db: Session, ctx, doc: StockDoc) -> StockDoc:
    """Post a stock document.

    For a receipt, the journal entry is posted and the engine creates
    the stock movements via posting_validator.
    """
    if doc.company_id != ctx.company_id:
        raise NotFoundError()
    if doc.kind == RECEIPT:
        entry = js.get_entry(db, ctx, doc.entry_id)
        js.post_entry(db, ctx, entry, action_label="ghi sổ phiếu nhập kho")
        db.refresh(doc)
        return doc
    company = js.get_company(db, ctx.company_id)
    with js.company_tx(db, company.id):
        db.refresh(doc)
        if doc.status != DRAFT:
            raise BusinessError("Chứng từ này không ở trạng thái nháp")
        items = _validate(db, company, _doc_input(doc))
        locks.assert_open(db, company.id, doc.date, "ghi sổ")
        if not doc.number:
            doc.number, doc.number_seq = js.next_number(
                db,
                company,
                doc.doc_type,
                doc.date,
                is_taken=lambda n: (
                    db.scalar(
                        select(StockDoc.id).where(
                            StockDoc.company_id == company.id,
                            StockDoc.number == n,
                        )
                    )
                    is not None
                ),
            )
        doc.posting_seq = _next_doc_posting_seq(db, company.id)
        doc.status = POSTED
        doc.posted_by = ctx.user_id
        doc.posted_at = _now()
        moves = _doc_moves(doc, items)
        db.add_all(moves)
        db.flush()
        check_negative_stock(
            db,
            company.id,
            {(m.item_id, m.warehouse_id) for m in moves},
            doc.date,
            action=f"ghi sổ {KIND_LABELS[doc.kind].lower()}",
        )
        mark_stale(db, company.id, doc.date)
        write_audit(
            db,
            ctx,
            action="stock_doc.post",
            entity_type="stock_doc",
            entity_id=doc.id,
            summary=(
                f"Ghi sổ {KIND_LABELS[doc.kind].lower()} {doc.number} "
                f"ngày {fmt_date(doc.date)}"
            ),
        )
    return doc


def cancel_doc(db: Session, ctx, doc: StockDoc, reason: str) -> StockDoc:
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("Hủy chứng từ bắt buộc phải nhập lý do")
    if doc.company_id != ctx.company_id:
        raise NotFoundError()
    if doc.kind == RECEIPT:
        entry = js.get_entry(db, ctx, doc.entry_id)
        js.cancel_entry(db, ctx, entry, reason)
        db.refresh(doc)
        return doc
    with js.company_tx(db, ctx.company_id):
        db.refresh(doc)
        if doc.status != POSTED:
            raise BusinessError("Chỉ hủy được chứng từ đã ghi sổ")
        locks.assert_open(db, ctx.company_id, doc.date, "hủy chứng từ")
        keys = _delete_moves(db, StockMove.stock_doc_id == doc.id)
        doc.status = CANCELLED
        doc.cancel_reason = reason
        doc.cancelled_by = ctx.user_id
        doc.cancelled_at = _now()
        db.flush()
        check_negative_stock(
            db, ctx.company_id, keys, doc.date, action=f"hủy {doc.number}"
        )
        mark_stale(db, ctx.company_id, doc.date)
        write_audit(
            db,
            ctx,
            action="stock_doc.cancel",
            entity_type="stock_doc",
            entity_id=doc.id,
            summary=f"Hủy {KIND_LABELS[doc.kind].lower()} {doc.number}",
            reason=reason,
        )
    return doc


def _doc_input(doc: StockDoc) -> DocInput:
    return DocInput(
        kind=doc.kind,
        date=doc.date,
        doc_date=doc.doc_date,
        warehouse_id=doc.warehouse_id,
        to_warehouse_id=doc.to_warehouse_id,
        partner_id=doc.partner_id,
        contact_name=doc.contact_name,
        counter_account=doc.counter_account,
        description=doc.description,
        lines=[
            LineInput(
                item_id=ln.item_id,
                quantity=ln.quantity,
                unit_price=ln.unit_price,
                amount=ln.amount,
                description=ln.description,
                unit=ln.unit,
            )
            for ln in doc.lines
        ],
    )


def _stock_acc(it: Item) -> str:
    return it.stock_account or STOCK_ACCOUNT.get(it.kind, "156")


def _doc_moves(
    doc: StockDoc, items: dict[int, Item], *, entry_id: int | None = None
) -> list[StockMove]:
    """Build the stock movements of a posted receipt/issue/transfer."""
    out = []
    for ln in doc.lines:
        it = items[ln.item_id]
        common = dict(
            company_id=doc.company_id,
            stock_doc_id=doc.id,
            entry_id=entry_id,
            source_line_id=ln.id,
            doc_type=doc.doc_type,
            number=doc.number or "",
            date=doc.date,
            item_id=it.id,
            stock_account=_stock_acc(it),
            description=doc.description or ln.description,
        )
        if doc.kind == RECEIPT:
            out.append(
                StockMove(
                    **common,
                    source=SRC_RECEIPT,
                    warehouse_id=doc.warehouse_id,
                    qty_in=ln.quantity,
                    qty_out=ZERO,
                    amount=int(ln.amount),
                    unit_cost=ln.unit_price,
                    valued=True,
                    contra_account=doc.counter_account,
                )
            )
        elif doc.kind == ISSUE:
            out.append(
                StockMove(
                    **common,
                    source=SRC_ISSUE,
                    warehouse_id=doc.warehouse_id,
                    qty_in=ZERO,
                    qty_out=ln.quantity,
                    amount=0,
                    valued=False,
                    contra_account=doc.counter_account,
                )
            )
        else:
            out.append(
                StockMove(
                    **common,
                    source=SRC_TRANSFER,
                    warehouse_id=doc.warehouse_id,
                    qty_in=ZERO,
                    qty_out=ln.quantity,
                    amount=0,
                    valued=False,
                    contra_account=_stock_acc(it),
                )
            )
            out.append(
                StockMove(
                    **common,
                    source=SRC_TRANSFER,
                    warehouse_id=doc.to_warehouse_id,
                    qty_in=ln.quantity,
                    qty_out=ZERO,
                    amount=0,
                    valued=False,
                    contra_account=_stock_acc(it),
                )
            )
    return out


def _delete_moves(db: Session, cond) -> set[tuple[int, int]]:
    rows = db.execute(
        select(StockMove.item_id, StockMove.warehouse_id).where(cond)
    ).all()
    db.execute(delete(StockMove).where(cond))
    return {(i, w) for i, w in rows}


# -------------------------------------- negative stock, stale costing


def balance_by_day(
    db: Session, company_id: int, item_id: int, warehouse_id: int
) -> list[tuple[date, Decimal]]:
    rows = db.execute(
        select(StockMove.date, func.sum(StockMove.qty_in - StockMove.qty_out))
        .where(
            StockMove.company_id == company_id,
            StockMove.item_id == item_id,
            StockMove.warehouse_id == warehouse_id,
        )
        .group_by(StockMove.date)
        .order_by(StockMove.date)
    ).all()
    out, cum = [], ZERO
    for d, net in rows:
        cum += to_decimal(net, ZERO)
        out.append((d, cum))
    return out


def inventory_settings(db: Session, company_id: int) -> dict:
    c = db.get(Company, company_id)
    return dict((c.settings or {}).get("inventory") or {}) if c else {}


def allow_negative(db: Session, company_id: int) -> bool:
    """Return whether the company allows negative stock issues.

    Used when stock is not managed yet; off by default.
    """
    return bool(inventory_settings(db, company_id).get("allow_negative_stock"))


def check_negative_stock(
    db: Session,
    company_id: int,
    keys: Iterable[tuple[int, int]],
    from_date: date,
    action: str = "ghi sổ",
) -> None:
    """Ensure no (item, warehouse) balance goes negative.

    Call after the movements are flushed: the closing balance of each
    day from ``from_date`` onwards must be ≥ 0.
    """
    if allow_negative(db, company_id):
        return
    for item_id, wh_id in sorted(set(keys)):
        for d, bal in balance_by_day(db, company_id, item_id, wh_id):
            if d >= from_date and bal < 0:
                it = db.get(Item, item_id)
                wh = db.get(Warehouse, wh_id)
                raise BusinessError(
                    f"Không thể {action}: tồn kho của {it.code} – "
                    f"{it.name} tại {wh.name} sẽ âm "
                    f"{fmt_qty(bal)} {it.unit} vào ngày {fmt_date(d)}.",
                    code="negative_stock",
                    details={
                        "item_id": item_id,
                        "warehouse_id": wh_id,
                        "date": d,
                        "balance": str(bal),
                    },
                )


def mark_stale(db: Session, company_id: int, d: date) -> None:
    """Mark computed costing runs from the month of ``d`` as stale."""
    db.execute(
        update(CostingRun)
        .where(
            CostingRun.company_id == company_id,
            (CostingRun.year > d.year)
            | ((CostingRun.year == d.year) & (CostingRun.month >= d.month)),
        )
        .values(is_stale=True)
    )


# ------------------------------------------------------ opening stock


@dataclass
class OpeningRow:
    item_id: int
    warehouse_id: int
    quantity: Decimal
    amount: int


def opening_date(company: Company) -> date:
    return company.start_date - timedelta(days=1)


def list_opening(db: Session, company_id: int) -> list[StockMove]:
    return list(
        db.scalars(
            select(StockMove)
            .where(
                StockMove.company_id == company_id,
                StockMove.source == SRC_OPENING,
            )
            .order_by(StockMove.warehouse_id, StockMove.item_id)
        )
    )


def replace_opening(
    db: Session,
    ctx,
    rows: list[OpeningRow],
    *,
    warehouse_id: int | None = None,
) -> None:
    """Replace the opening stock.

    Replaces all of it, or only that of one warehouse when
    ``warehouse_id`` is given.
    """
    company = js.get_company(db, ctx.company_id)
    rows = [r for r in rows if r.quantity or r.amount]
    errors = []
    item_ids = {r.item_id for r in rows}
    items = (
        {
            it.id: it
            for it in db.scalars(
                select(Item).where(
                    Item.company_id == company.id, Item.id.in_(item_ids)
                )
            )
        }
        if item_ids
        else {}
    )
    whs = set(
        db.scalars(
            select(Warehouse.id).where(Warehouse.company_id == company.id)
        )
    )
    seen = set()
    for n, r in enumerate(rows, start=1):
        it = items.get(r.item_id)
        if it is None or not tracks(it):
            errors.append(f"Dòng {n}: vật tư hàng hóa không hợp lệ")
        if r.warehouse_id not in whs:
            errors.append(f"Dòng {n}: kho không hợp lệ")
        if r.quantity is None or r.quantity < 0 or (r.amount or 0) < 0:
            errors.append(f"Dòng {n}: số lượng, giá trị không được âm")
        if r.quantity == 0 and r.amount:
            errors.append(f"Dòng {n}: có giá trị nhưng số lượng bằng 0")
        if (r.item_id, r.warehouse_id) in seen:
            errors.append(f"Dòng {n}: trùng hàng và kho")
        seen.add((r.item_id, r.warehouse_id))
    if errors:
        raise js.EntryValidationError(errors)
    with js.company_tx(db, company.id):
        if locks.any_locked(db, company.id):
            raise BusinessError(
                "Đã khóa sổ, không sửa được tồn kho đầu kỳ. "
                "Mở khóa toàn bộ trước."
            )
        cond = (StockMove.company_id == company.id) & (
            StockMove.source == SRC_OPENING
        )
        if warehouse_id is not None:
            cond = cond & (StockMove.warehouse_id == warehouse_id)
            rows = [r for r in rows if r.warehouse_id == warehouse_id]
        keys = _delete_moves(db, cond)
        d = opening_date(company)
        for r in rows:
            it = items[r.item_id]
            db.add(
                StockMove(
                    company_id=company.id,
                    source=SRC_OPENING,
                    doc_type="OB",
                    number="",
                    date=d,
                    item_id=it.id,
                    warehouse_id=r.warehouse_id,
                    qty_in=r.quantity,
                    qty_out=ZERO,
                    amount=int(r.amount or 0),
                    valued=True,
                    stock_account=_stock_acc(it),
                    unit_cost=(
                        Decimal(int(r.amount or 0)) / r.quantity
                    ).quantize(Decimal("0.01"))
                    if r.quantity
                    else None,
                    description="Tồn kho đầu kỳ",
                )
            )
            keys.add((it.id, r.warehouse_id))
        db.flush()
        check_negative_stock(
            db, company.id, keys, d, action="sửa tồn kho đầu kỳ"
        )
        mark_stale(db, company.id, d)
        total = sum(int(r.amount or 0) for r in rows)
        write_audit(
            db,
            ctx,
            action="inventory.opening",
            entity_type="inventory",
            entity_id=None,
            summary=(
                f"Cập nhật tồn kho đầu kỳ ({len(rows)} dòng, "
                f"{format_vnd(total)} đồng)"
            ),
        )


# ------------------------------------------------------ stock on hand


def on_hand(
    db: Session,
    company_id: int,
    *,
    upto: date | None = None,
    item_id: int | None = None,
    warehouse_id: int | None = None,
) -> list[dict]:
    """Return stock on hand per item and warehouse.

    Quantity and known value up to the end of day ``upto``.
    """
    stmt = (
        select(
            StockMove.item_id,
            StockMove.warehouse_id,
            func.sum(StockMove.qty_in - StockMove.qty_out),
            func.sum(StockMove.amount * _sign()),
        )
        .where(StockMove.company_id == company_id)
        .group_by(StockMove.item_id, StockMove.warehouse_id)
    )
    if upto is not None:
        stmt = stmt.where(StockMove.date <= upto)
    if item_id is not None:
        stmt = stmt.where(StockMove.item_id == item_id)
    if warehouse_id is not None:
        stmt = stmt.where(StockMove.warehouse_id == warehouse_id)
    out = []
    for iid, wid, qty, val in db.execute(stmt):
        q = to_decimal(qty, ZERO)
        if q == 0 and not val:
            continue
        out.append(
            {
                "item_id": iid,
                "warehouse_id": wid,
                "quantity": q,
                "amount": int(val or 0),
            }
        )
    return out


def _sign():
    from sqlalchemy import case

    return case((StockMove.qty_in > 0, 1), else_=-1)
