"""Issue costing: periodic (monthly) weighted average per item and
warehouse.

Average unit cost =
    (opening value + value of costed receipts in the month)
    / (opening quantity + quantity of costed receipts in the month)

- Costed receipts: purchases (MH) and stock receipts (NK); purchase
  returns (TM) are deducted from receipts.
- Transfers: the source warehouse uses its own average (pass 1); the
  destination warehouse adds the transfer to its receipts (pass 2).
- Sales issues (BH), stock issues (XK) and sales returns (TL) are
  valued at the average unit cost.
- When an item runs out at month end (quantity = 0), the rounding
  difference goes to the last issue line so the closing value is 0.

Results are written to StockMove and to a month-end GV entry (Dr 632 /
expense account, Cr 15x; the reverse for sales returns). Locked months
cannot be recalculated; recalculating an earlier month marks later
months as needing recalculation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.dates import iter_months, month_bounds, today
from app.core.errors import BusinessError
from app.core.money import format_vnd
from app.features.inventory.models import SRC_TRANSFER, CostingRun, StockMove
from app.features.journal import locks
from app.features.journal import service as js
from app.features.journal.models import DRAFT, POSTED, JournalEntry

ZERO = Decimal(0)
CENT = Decimal("0.01")


def _d(v) -> Decimal:
    if v is None:
        return ZERO
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _round(v: Decimal) -> int:
    return int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass
class KeyState:
    opening_qty: Decimal = ZERO
    opening_val: int = 0
    base_qty: Decimal = ZERO
    base_val: int = 0
    avg: Decimal = ZERO
    in_qty: Decimal = ZERO
    in_val: int = 0
    out_qty: Decimal = ZERO
    out_val: int = 0
    moves: list = field(default_factory=list)

    @property
    def closing_qty(self) -> Decimal:
        return self.opening_qty + self.in_qty - self.out_qty

    @property
    def closing_val(self) -> int:
        return self.opening_val + self.in_val - self.out_val


def _avg(qty: Decimal, val: int, fallback: Decimal) -> Decimal:
    if qty > 0:
        return (Decimal(val) / qty).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    return fallback


def compute(
    db: Session, company_id: int, year: int, month: int
) -> dict[tuple[int, int], KeyState]:
    """Compute issue costing without writing to the database.

    Returns the state of each (item, warehouse); the computed amounts
    are set on the costed StockMove objects themselves.
    """
    start, end = month_bounds(year, month)
    states: dict[tuple[int, int], KeyState] = defaultdict(KeyState)
    sign = func.sum(StockMove.qty_in - StockMove.qty_out)
    val = func.sum(
        case((StockMove.qty_in > 0, StockMove.amount), else_=-StockMove.amount)
    )
    for iid, wid, q, v in db.execute(
        select(StockMove.item_id, StockMove.warehouse_id, sign, val)
        .where(StockMove.company_id == company_id, StockMove.date < start)
        .group_by(StockMove.item_id, StockMove.warehouse_id)
    ):
        st = states[(iid, wid)]
        st.opening_qty, st.opening_val = _d(q), int(v or 0)
    moves = list(
        db.scalars(
            select(StockMove)
            .where(
                StockMove.company_id == company_id,
                StockMove.date >= start,
                StockMove.date <= end,
            )
            .order_by(StockMove.date, StockMove.qty_in.desc(), StockMove.id)
        )
    )
    for m in moves:
        states[(m.item_id, m.warehouse_id)].moves.append(m)

    # Pass 1: average from opening stock + valued receipts/issues
    # (excluding transfers).
    for st in states.values():
        st.base_qty, st.base_val = st.opening_qty, st.opening_val
        for m in st.moves:
            if m.valued:
                if m.qty_in > 0:
                    st.base_qty += _d(m.qty_in)
                    st.base_val += int(m.amount)
                else:
                    st.base_qty -= _d(m.qty_out)
                    st.base_val -= int(m.amount)
        fallback = _avg(st.opening_qty, st.opening_val, ZERO)
        st.avg = _avg(st.base_qty, st.base_val, fallback)

    # Transfers: the source warehouse uses the pass-1 average; the
    # destination warehouse receives the same value.
    transfer_out: dict[tuple, StockMove] = {}
    transfer_in: dict[tuple, StockMove] = {}
    for m in moves:
        if m.source == SRC_TRANSFER and not m.valued:
            key = (m.stock_doc_id, m.source_line_id, m.entry_id)
            (transfer_in if m.qty_in > 0 else transfer_out)[key] = m
    for key, out in transfer_out.items():
        amount = _round(
            _d(out.qty_out) * states[(out.item_id, out.warehouse_id)].avg
        )
        out.amount, out.unit_cost = (
            amount,
            states[(out.item_id, out.warehouse_id)].avg.quantize(CENT),
        )
        inn = transfer_in.get(key)
        if inn is not None:
            inn.amount, inn.unit_cost = amount, out.unit_cost
            dest = states[(inn.item_id, inn.warehouse_id)]
            dest.base_qty += _d(inn.qty_in)
            dest.base_val += amount
    # Pass 2: destination average including transferred-in goods.
    if transfer_in:
        for iid, wid in {
            (m.item_id, m.warehouse_id) for m in transfer_in.values()
        }:
            st = states[(iid, wid)]
            st.avg = _avg(st.base_qty, st.base_val, st.avg)

    # Value the remaining costed lines + rounding difference.
    for st in states.values():
        last_out = None
        for m in st.moves:
            if not m.valued and m.source != SRC_TRANSFER:
                qty = _d(m.qty_in) + _d(m.qty_out)
                m.amount = _round(qty * st.avg)
                m.unit_cost = st.avg.quantize(CENT)
            if m.qty_in > 0:
                st.in_qty += _d(m.qty_in)
                st.in_val += int(m.amount)
            else:
                st.out_qty += _d(m.qty_out)
                st.out_val += int(m.amount)
                if not m.valued:
                    last_out = m
        if (
            st.closing_qty == 0
            and st.closing_val != 0
            and last_out is not None
        ):
            adj = int(last_out.amount) + st.closing_val
            if adj >= 0:
                st.out_val += st.closing_val
                last_out.amount = adj
                if last_out.source == SRC_TRANSFER:
                    key = (
                        last_out.stock_doc_id,
                        last_out.source_line_id,
                        last_out.entry_id,
                    )
                    inn = transfer_in.get(key)
                    if inn is not None:
                        inn.amount = adj
    return states


def _gv_lines(moves: list[StockMove]) -> list[js.LineIn]:
    agg: dict[tuple[str, str, int], int] = defaultdict(int)
    for m in moves:
        if m.valued or not m.amount or m.source == SRC_TRANSFER:
            continue
        if m.qty_out > 0:
            # Sale/usage issue: Dr counter account / Cr stock account.
            agg[(m.contra_account or "632", m.stock_account, m.item_id)] += (
                int(m.amount)
            )
        else:
            # Sales return: Dr stock account / Cr cost of goods sold.
            agg[(m.stock_account, m.contra_account or "632", m.item_id)] += (
                int(m.amount)
            )
    lines = []
    for (debit, credit, item_id), amount in sorted(agg.items()):
        if amount > 0:
            lines.append(
                js.LineIn(
                    debit_account=debit,
                    credit_account=credit,
                    amount=amount,
                    item_id=item_id,
                )
            )
    return lines


def _require_previous(db: Session, company_id: int, start: date) -> None:
    # Are there uncosted issue lines in earlier months?
    row = db.execute(
        select(func.min(StockMove.date)).where(
            StockMove.company_id == company_id,
            StockMove.date < start,
            StockMove.valued.is_(False),
            StockMove.costed.is_(False),
        )
    ).scalar()
    if row is not None:
        raise BusinessError(
            f"Phải tính giá xuất kho tháng {row.month:02d}/{row.year} trước"
        )
    stale = db.scalars(
        select(CostingRun)
        .where(
            CostingRun.company_id == company_id,
            CostingRun.is_stale.is_(True),
            (CostingRun.year < start.year)
            | (
                (CostingRun.year == start.year)
                & (CostingRun.month < start.month)
            ),
        )
        .order_by(CostingRun.year, CostingRun.month)
    ).first()
    if stale is not None:
        raise BusinessError(
            f"Tháng {stale.month:02d}/{stale.year} cần tính lại giá trước"
        )


def run_costing(db: Session, ctx, year: int, month: int) -> CostingRun:
    """Run issue costing for a month.

    Updates the issue values and (re)creates the posted GV entry.
    """
    company = js.get_company(db, ctx.company_id)
    start, end = month_bounds(year, month)
    if end < company.start_date:
        raise BusinessError("Tháng này trước ngày bắt đầu hạch toán")
    if start > today():
        raise BusinessError("Chưa đến tháng này")
    if locks.is_locked(db, company.id, start):
        raise BusinessError(
            f"Tháng {month:02d}/{year} đã khóa sổ, "
            "không tính lại giá xuất kho được"
        )
    _require_previous(db, company.id, start)
    run = db.scalar(
        select(CostingRun).where(
            CostingRun.company_id == company.id,
            CostingRun.year == year,
            CostingRun.month == month,
        )
    )
    # Cancel the old GV entry.
    if run is not None and run.entry_id:
        old = db.get(JournalEntry, run.entry_id)
        if old is not None and old.status == POSTED:
            js.cancel_entry(
                db, ctx, old, f"Tính lại giá xuất kho tháng {month:02d}/{year}"
            )
        elif old is not None and old.status == DRAFT:
            js.delete_draft(db, ctx, old)
        run = db.scalar(
            select(CostingRun).where(
                CostingRun.company_id == company.id,
                CostingRun.year == year,
                CostingRun.month == month,
            )
        )
    if run is None:
        run = CostingRun(company_id=company.id, year=year, month=month)
        db.add(run)
    before = {
        m.id: (m.amount, m.costed)
        for m in db.scalars(
            select(StockMove).where(
                StockMove.company_id == company.id,
                StockMove.date >= start,
                StockMove.date <= end,
            )
        )
    }
    states = compute(db, company.id, year, month)
    month_moves = [m for st in states.values() for m in st.moves]
    changed = False
    for m in month_moves:
        if not m.valued:
            m.costed = True
        if before.get(m.id) != (m.amount, m.costed):
            changed = True
    lines = _gv_lines(month_moves)
    total = sum(
        int(m.amount) * (1 if m.qty_out > 0 else -1)
        for m in month_moves
        if not m.valued and m.source != SRC_TRANSFER
    )
    run.total_cost = total
    run.entry_id = None
    run.is_stale = True  # reset to False once the GV entry is posted
    run.computed_by = ctx.user_id
    run.computed_at = datetime.now(timezone.utc)
    db.flush()
    if lines:
        entry = js.create_entry(
            db,
            ctx,
            js.EntryIn(
                doc_type="GV",
                date=end,
                doc_date=end,
                lines=lines,
                description=f"Giá vốn hàng xuất tháng {month:02d}/{year}",
                source="inventory",
                source_id=run.id,
                meta={"costing_run": run.id},
            ),
        )
        run.entry_id = entry.id
        db.commit()
        js.post_entry(db, ctx, entry, action_label="ghi sổ giá vốn hàng xuất")
    run.is_stale = False
    if changed:
        # The closing stock value changed → later months must be
        # recalculated.
        later = db.scalars(
            select(CostingRun).where(
                CostingRun.company_id == company.id,
                (CostingRun.year > year)
                | ((CostingRun.year == year) & (CostingRun.month > month)),
            )
        )
        for r in later:
            r.is_stale = True
    write_audit(
        db,
        ctx,
        action="inventory.costing",
        entity_type="costing",
        entity_id=f"{year}-{month:02d}",
        summary=(
            f"Tính giá xuất kho tháng {month:02d}/{year}: "
            f"giá vốn {format_vnd(total)} đồng"
        ),
    )
    db.commit()
    return run


@dataclass
class MonthStatus:
    year: int
    month: int
    moves: int
    uncosted: int
    run: CostingRun | None
    locked: bool
    entry_number: str | None

    @property
    def needs_run(self) -> bool:
        return self.uncosted > 0 or (
            self.run is not None and self.run.is_stale
        )


def months_status(db: Session, company) -> list[MonthStatus]:
    """Return the costing status of each month, newest first.

    Covers the months from the company start date to the current month.
    """
    runs = {
        (r.year, r.month): r
        for r in db.scalars(
            select(CostingRun).where(CostingRun.company_id == company.id)
        )
    }
    counts: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    for d, valued, costed in db.execute(
        select(StockMove.date, StockMove.valued, StockMove.costed).where(
            StockMove.company_id == company.id,
            StockMove.date >= company.start_date,
        )
    ):
        c = counts[(d.year, d.month)]
        if not valued:
            c[0] += 1
            if not costed:
                c[1] += 1
    locked = locks.locked_set(db, company.id)
    numbers = {}
    ids = [r.entry_id for r in runs.values() if r.entry_id]
    if ids:
        numbers = dict(
            db.execute(
                select(JournalEntry.id, JournalEntry.number).where(
                    JournalEntry.id.in_(ids)
                )
            ).all()
        )
    out = []
    for y, m in iter_months(
        company.start_date, max(today(), company.start_date)
    ):
        run = runs.get((y, m))
        out.append(
            MonthStatus(
                year=y,
                month=m,
                moves=counts[(y, m)][0],
                uncosted=counts[(y, m)][1],
                run=run,
                locked=locks.month_locked(locked, y, m),
                entry_number=numbers.get(run.entry_id)
                if run and run.entry_id
                else None,
            )
        )
    return list(reversed(out))
