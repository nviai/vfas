"""Inventory data checks."""

from __future__ import annotations

from sqlalchemy import func, select

from app.core.dates import fmt_date, month_bounds, today
from app.core.money import format_vnd
from app.core.registry import CheckIssue, data_check
from app.core.web import fmt_qty
from app.features.inventory import costing, service
from app.features.inventory.models import SRC_OPENING, StockMove
from app.features.items.models import Item
from app.features.journal import queries
from app.features.journal.models import OpeningBalance
from app.features.org.models import Company


def _stock_accounts(db, company_id: int) -> list[str]:
    accs = set(
        db.scalars(
            select(StockMove.stock_account)
            .where(StockMove.company_id == company_id)
            .distinct()
        )
    )
    return sorted(a for a in accs if a)


@data_check(
    code="inventory_gl_mismatch",
    title="Giá trị kho khác số dư tài khoản hàng tồn kho",
    perm="inventory.view",
    order=210,
)
def check_gl(db, ctx):
    out = []
    company = db.get(Company, ctx.company_id)
    # Opening balances
    for acc in _stock_accounts(db, company.id):
        stock = int(
            db.scalar(
                select(func.coalesce(func.sum(StockMove.amount), 0)).where(
                    StockMove.company_id == company.id,
                    StockMove.source == SRC_OPENING,
                    StockMove.stock_account == acc,
                )
            )
            or 0
        )
        gl = int(
            db.scalar(
                select(
                    func.coalesce(
                        func.sum(OpeningBalance.debit - OpeningBalance.credit),
                        0,
                    )
                ).where(
                    OpeningBalance.company_id == company.id,
                    OpeningBalance.account == acc,
                )
            )
            or 0
        )
        if stock != gl:
            out.append(
                CheckIssue(
                    f"Tồn kho đầu kỳ TK {acc}: kho {format_vnd(stock)} "
                    f"≠ số dư đầu kỳ {format_vnd(gl)}",
                    "error",
                    "/inventory/opening",
                )
            )
    # End of the most recently costed month
    statuses = [
        s
        for s in costing.months_status(db, company)
        if s.run is not None and not s.run.is_stale and s.uncosted == 0
    ]
    if statuses:
        last = statuses[0]
        end = month_bounds(last.year, last.month)[1]
        for acc in _stock_accounts(db, company.id):
            stock = int(
                db.scalar(
                    select(
                        func.coalesce(
                            func.sum(service._sign() * StockMove.amount), 0
                        )
                    ).where(
                        StockMove.company_id == company.id,
                        StockMove.stock_account == acc,
                        StockMove.date <= end,
                    )
                )
                or 0
            )
            gl = queries.balance(db, company.id, acc, upto=end, exact=True)
            if stock != gl:
                out.append(
                    CheckIssue(
                        f"Ngày {fmt_date(end)} TK {acc}: "
                        f"giá trị kho {format_vnd(stock)} "
                        f"≠ số dư sổ cái {format_vnd(gl)}",
                        "warning",
                        "/inventory/summary",
                    )
                )
    return out


@data_check(
    code="inventory_costing_pending",
    title="Tháng đã qua chưa tính giá xuất kho",
    perm="inventory.view",
    order=220,
)
def check_costing(db, ctx):
    company = db.get(Company, ctx.company_id)
    out = []
    for s in costing.months_status(db, company):
        if not s.needs_run or s.locked:
            continue
        if month_bounds(s.year, s.month)[1] >= today():
            continue
        what = (
            "cần tính lại giá"
            if s.run and s.run.is_stale and not s.uncosted
            else f"{s.uncosted} dòng xuất chưa tính giá"
        )
        out.append(
            CheckIssue(
                f"Tháng {s.month:02d}/{s.year}: {what}",
                "warning",
                "/inventory/costing",
            )
        )
    return out


@data_check(
    code="inventory_negative",
    title="Tồn kho âm",
    perm="inventory.view",
    order=230,
)
def check_negative(db, ctx):
    keys = db.execute(
        select(StockMove.item_id, StockMove.warehouse_id)
        .where(StockMove.company_id == ctx.company_id)
        .distinct()
    ).all()
    out = []
    for iid, wid in keys:
        for d, bal in service.balance_by_day(db, ctx.company_id, iid, wid):
            if bal < 0:
                it = db.get(Item, iid)
                out.append(
                    CheckIssue(
                        f"{it.code} – {it.name}: tồn {fmt_qty(bal)} "
                        f"{it.unit} ngày {fmt_date(d)}",
                        "error",
                        f"/inventory/stock-card?item_id={iid}&warehouse_id={wid}",
                        d,
                    )
                )
                break
    return out
