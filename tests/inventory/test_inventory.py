"""Inventory service tests.

Receipts/issues/transfers, periodic weighted average cost,
retroactive negative stock blocking, sync with sales/purchases.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.db import Money, Qty
from app.core.errors import BusinessError
from app.features.inventory import costing, hooks, reports
from app.features.inventory import service as inv
from app.features.inventory.models import CostingRun, StockMove
from app.features.items import service as items_svc
from app.features.journal import locks, queries
from app.features.journal import opening as gl_opening
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.partners import service as partners

D = Decimal


@pytest.fixture()
def item(db, ctx):
    return items_svc.create(
        db,
        ctx,
        items_svc.ItemInput(
            name="Bàn phím cơ", code="HH0001", unit="cái", kind="goods"
        ),
    )


@pytest.fixture()
def wh(db, ctx):
    return inv.default_warehouse(db, ctx.company_id)


def receipt(db, ctx, wh, item, d, qty, amount, post=True, counter="3388"):
    return inv.create_doc(
        db,
        ctx,
        inv.DocInput(
            kind="receipt",
            date=d,
            warehouse_id=wh.id,
            counter_account=counter,
            lines=[
                inv.LineInput(item_id=item.id, quantity=D(qty), amount=amount)
            ],
        ),
        post=post,
    )


def issue(db, ctx, wh, item, d, qty, post=True, counter="6422"):
    return inv.create_doc(
        db,
        ctx,
        inv.DocInput(
            kind="issue",
            date=d,
            warehouse_id=wh.id,
            counter_account=counter,
            lines=[inv.LineInput(item_id=item.id, quantity=D(qty))],
        ),
        post=post,
    )


def opening_stock(db, ctx, wh, item, qty, amount):
    gl_opening.replace_opening(
        db,
        ctx,
        [
            gl_opening.OpeningIn("156", debit=amount),
            gl_opening.OpeningIn("4111", credit=amount),
        ],
    )
    inv.replace_opening(
        db,
        ctx,
        [
            inv.OpeningRow(
                item_id=item.id,
                warehouse_id=wh.id,
                quantity=D(qty),
                amount=amount,
            )
        ],
    )


def test_weighted_average_hand_calculated(db, ctx, item, wh, company):
    opening_stock(db, ctx, wh, item, 10, 1_000_000)  # 10 pcs x 100,000
    # 20 pcs x 130,000
    nk = receipt(db, ctx, wh, item, date(2024, 3, 5), 20, 2_600_000)
    assert nk.status == "posted" and nk.number == "NK2024-0001"
    entry = db.get(JournalEntry, nk.entry_id)
    assert [
        (ln.debit_account, ln.credit_account, ln.amount) for ln in entry.lines
    ] == [("156", "3388", 2_600_000)]
    x1 = issue(db, ctx, wh, item, date(2024, 3, 10), 15)
    x2 = issue(db, ctx, wh, item, date(2024, 3, 20), 5)
    assert (x1.number, x2.number) == ("XK2024-0001", "XK2024-0002")
    run = costing.run_costing(db, ctx, 2024, 3)
    # Average = (1,000,000 + 2,600,000) / (10 + 20) = 120,000
    outs = db.scalars(
        select(StockMove).where(StockMove.qty_out > 0).order_by(StockMove.date)
    ).all()
    assert [(o.amount, o.unit_cost) for o in outs] == [
        (1_800_000, D("120000.00")),
        (600_000, D("120000.00")),
    ]
    gv = db.get(JournalEntry, run.entry_id)
    assert (
        gv.status == "posted"
        and gv.doc_type == "GV"
        and gv.date == date(2024, 3, 31)
    )
    assert [
        (ln.debit_account, ln.credit_account, ln.amount) for ln in gv.lines
    ] == [("6422", "156", 2_400_000)]
    assert run.total_cost == 2_400_000 and not run.is_stale
    # Stock value = balance of TK 156
    s = reports.summary(db, company.id, date(2024, 3, 1), date(2024, 3, 31))
    row = s.rows[0]
    assert (row.opening_qty, row.opening_val, row.in_qty, row.in_val) == (
        10,
        1_000_000,
        20,
        2_600_000,
    )
    assert (row.out_qty, row.out_val, row.closing_qty, row.closing_val) == (
        20,
        2_400_000,
        10,
        1_200_000,
    )
    assert (
        queries.balance(db, company.id, "156", upto=date(2024, 3, 31))
        == 1_200_000
        == s.by_account["156"]
    )
    # Stock card / detail ledger
    led = reports.item_ledger(
        db, company.id, item.id, date(2024, 3, 1), date(2024, 3, 31), wh.id
    )
    assert [r.balance_qty for r in led.rows] == [30, 15, 10]
    assert led.closing_val == 1_200_000 and not led.pending


def test_rerun_after_backdated_receipt_and_rounding(db, ctx, item, wh):
    opening_stock(db, ctx, wh, item, 3, 1_000_000)  # 333,333.33 / pc
    issue(db, ctx, wh, item, date(2024, 5, 2), 1)
    issue(db, ctx, wh, item, date(2024, 5, 3), 2)
    costing.run_costing(db, ctx, 2024, 5)
    outs = db.scalars(
        select(StockMove).where(StockMove.qty_out > 0).order_by(StockMove.date)
    ).all()
    # Out of stock: the last line takes the rounding so stock value = 0
    assert [o.amount for o in outs] == [333_333, 666_667]
    # Backdated receipt -> May needs recosting; rerunning cancels the
    # old COGS entry and creates a new one
    receipt(db, ctx, wh, item, date(2024, 5, 1), 1, 200_000)
    run = db.scalar(select(CostingRun))
    assert run.is_stale
    old_entry = run.entry_id
    run = costing.run_costing(db, ctx, 2024, 5)
    assert db.get(JournalEntry, old_entry).status == "cancelled"
    # (1,000,000 + 200,000) / 4 = 300,000
    outs = db.scalars(
        select(StockMove).where(StockMove.qty_out > 0).order_by(StockMove.date)
    ).all()
    assert [o.amount for o in outs] == [300_000, 600_000]
    assert run.total_cost == 900_000


def test_costing_requires_previous_month(db, ctx, item, wh):
    opening_stock(db, ctx, wh, item, 10, 1_000_000)
    issue(db, ctx, wh, item, date(2024, 1, 10), 1)
    issue(db, ctx, wh, item, date(2024, 2, 10), 1)
    with pytest.raises(BusinessError, match="01/2024"):
        costing.run_costing(db, ctx, 2024, 2)
    costing.run_costing(db, ctx, 2024, 1)
    costing.run_costing(db, ctx, 2024, 2)


def test_negative_stock_retroactive(db, ctx, item, wh):
    opening_stock(db, ctx, wh, item, 10, 1_000_000)
    issue(db, ctx, wh, item, date(2024, 3, 10), 8)
    with pytest.raises(BusinessError) as ei:
        # backdated: on 10/03 the stock is 10 - 5 - 8 = -3
        issue(db, ctx, wh, item, date(2024, 3, 5), 5)
    assert "10/03/2024" in ei.value.message and "-3" in ei.value.message
    db.rollback()
    nk = receipt(db, ctx, wh, item, date(2024, 4, 1), 20, 2_000_000)
    issue(db, ctx, wh, item, date(2024, 4, 15), 20)
    with pytest.raises(BusinessError) as ei:
        inv.cancel_doc(db, ctx, nk, "Nhập nhầm")
    assert ei.value.code == "negative_stock"
    db.rollback()
    assert db.get(JournalEntry, nk.entry_id).status == "posted"
    # Same day: receipts count before issues
    receipt(db, ctx, wh, item, date(2024, 5, 2), 5, 500_000)
    issue(db, ctx, wh, item, date(2024, 5, 2), 7)


def test_transfer_between_warehouses(db, ctx, item, wh, company):
    wh2 = inv.save_warehouse(db, ctx, wh=None, code="KHO02", name="Kho Hà Nội")
    opening_stock(db, ctx, wh, item, 10, 1_000_000)
    dc = inv.create_doc(
        db,
        ctx,
        inv.DocInput(
            kind="transfer",
            date=date(2024, 6, 3),
            warehouse_id=wh.id,
            to_warehouse_id=wh2.id,
            lines=[inv.LineInput(item_id=item.id, quantity=D(4))],
        ),
        post=True,
    )
    assert dc.number == "DC2024-0001"
    issue(db, ctx, wh2, item, date(2024, 6, 5), 2)
    with pytest.raises(BusinessError):
        issue(db, ctx, wh2, item, date(2024, 6, 6), 3)
    db.rollback()
    run = costing.run_costing(db, ctx, 2024, 6)
    assert run.total_cost == 200_000
    s = reports.summary(
        db, company.id, date(2024, 6, 1), date(2024, 6, 30), by_warehouse=True
    )
    vals = {r.warehouse.code: (r.closing_qty, r.closing_val) for r in s.rows}
    assert vals == {"KHO01": (6, 600_000), "KHO02": (2, 200_000)}
    gv = db.get(JournalEntry, run.entry_id)
    assert [
        (ln.debit_account, ln.credit_account, ln.amount) for ln in gv.lines
    ] == [("6422", "156", 200_000)]


def test_receipt_reversal_and_draft_delete(db, ctx, item, wh):
    nk = receipt(db, ctx, wh, item, date(2024, 7, 1), 5, 500_000)
    adj = js.reverse_entry(
        db, ctx, db.get(JournalEntry, nk.entry_id), date(2024, 7, 2), "Trả lại"
    )
    js.post_entry(db, ctx, adj)
    assert inv.on_hand(db, ctx.company_id, item_id=item.id) == []
    draft = receipt(
        db, ctx, wh, item, date(2024, 7, 3), 1, 100_000, post=False
    )
    js.delete_draft(db, ctx, db.get(JournalEntry, draft.entry_id))
    from app.features.inventory.models import StockDoc

    assert db.get(StockDoc, draft.id) is None


# -------------------- sales/purchases (fake lines if group C is absent)


class FakeBase(DeclarativeBase):
    pass


class FakeTradeLine(FakeBase):
    __tablename__ = "inv_test_trade_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(200), default="")
    quantity: Mapped[Decimal] = mapped_column(Qty)
    amount: Mapped[int] = mapped_column(Money, default=0)
    vat_amount: Mapped[int] = mapped_column(Money, default=0)
    debit_account: Mapped[str | None] = mapped_column(String(20))
    warehouse_id: Mapped[int | None] = mapped_column(Integer)


@pytest.fixture()
def fake_trade(engine, db, monkeypatch):
    db.commit()
    FakeBase.metadata.create_all(engine)
    monkeypatch.setattr(
        hooks, "_trade_models", lambda source: (None, FakeTradeLine)
    )
    yield FakeTradeLine
    db.rollback()
    db.close()
    FakeBase.metadata.drop_all(engine)


def trade(
    db,
    ctx,
    doc_type,
    d,
    partner,
    item,
    qty,
    amount,
    debit_account=None,
    doc_id=None,
):
    doc_id = (
        doc_id
        or (
            db.scalar(
                select(FakeTradeLine.doc_id).order_by(
                    FakeTradeLine.doc_id.desc()
                )
            )
            or 0
        )
        + 1
    )
    db.add(
        FakeTradeLine(
            doc_id=doc_id,
            item_id=item.id,
            quantity=D(qty),
            amount=amount,
            debit_account=debit_account,
        )
    )
    db.commit()
    source = "sales" if doc_type in ("BH", "TL") else "purchases"
    if doc_type == "BH":
        lines = [
            js.LineIn(
                "131",
                "5111",
                amount,
                debit_partner_id=partner.id,
                item_id=item.id,
            )
        ]
    elif doc_type == "TL":
        lines = [
            js.LineIn(
                "5111",
                "131",
                amount,
                credit_partner_id=partner.id,
                item_id=item.id,
            )
        ]
    elif doc_type == "MH":
        lines = [
            js.LineIn(
                "156",
                "331",
                amount,
                credit_partner_id=partner.id,
                item_id=item.id,
            )
        ]
    else:
        lines = [
            js.LineIn(
                "331",
                "156",
                amount,
                debit_partner_id=partner.id,
                item_id=item.id,
            )
        ]
    return js.create_entry(
        db,
        ctx,
        js.EntryIn(
            doc_type=doc_type,
            date=d,
            lines=lines,
            source=source,
            source_id=doc_id,
        ),
        post=True,
    )


def test_sales_purchases_flow(db, ctx, item, wh, company, fake_trade):
    kh = partners.create(
        db, ctx, partners.PartnerInput(name="Khách lẻ", is_customer=True)
    )
    ncc = partners.create(
        db, ctx, partners.PartnerInput(name="NCC A", is_supplier=True)
    )
    trade(
        db,
        ctx,
        "MH",
        date(2024, 8, 1),
        ncc,
        item,
        10,
        1_000_000,
        debit_account="156",
    )
    trade(
        db,
        ctx,
        "MH",
        date(2024, 8, 2),
        ncc,
        item,
        10,
        1_400_000,
        debit_account="156",
    )
    with pytest.raises(BusinessError) as ei:
        trade(db, ctx, "BH", date(2024, 8, 3), kh, item, 25, 5_000_000)
    assert ei.value.code == "negative_stock"
    db.rollback()
    bh = trade(db, ctx, "BH", date(2024, 8, 5), kh, item, 15, 4_500_000)
    trade(db, ctx, "TL", date(2024, 8, 6), kh, item, 1, 300_000)
    run = costing.run_costing(db, ctx, 2024, 8)
    # Average (1,000,000 + 1,400,000) / 20 = 120,000; sell 15 ->
    # 1,800,000; return 1 -> 120,000
    gv = db.get(JournalEntry, run.entry_id)
    assert sorted(
        (ln.debit_account, ln.credit_account, ln.amount) for ln in gv.lines
    ) == [("156", "632", 120_000), ("632", "156", 1_800_000)]
    assert queries.balance(db, company.id, "632") == 1_680_000
    assert queries.balance(db, company.id, "156") == 2_400_000 - 1_680_000
    # Cancel the sale: issue lines are gone, August needs recosting
    js.cancel_entry(db, ctx, bh, "Hủy đơn")
    assert db.scalar(select(CostingRun)).is_stale
    # Cancelling a purchase that makes stock negative (1 returned, 21
    # left - ...): cancelling the 10-unit purchase with 21 left is fine;
    # create a new sale, then try
    trade(db, ctx, "BH", date(2024, 8, 20), kh, item, 15, 4_500_000)
    first_mh = db.scalars(
        select(JournalEntry)
        .where(JournalEntry.doc_type == "MH")
        .order_by(JournalEntry.id)
    ).first()
    with pytest.raises(BusinessError):
        js.cancel_entry(db, ctx, first_mh, "Sai")
    db.rollback()


def test_purchase_expense_lines_skip_inventory(db, ctx, item, fake_trade):
    ncc = partners.create(
        db, ctx, partners.PartnerInput(name="NCC B", is_supplier=True)
    )
    trade(
        db,
        ctx,
        "MH",
        date(2024, 9, 1),
        ncc,
        item,
        3,
        300_000,
        debit_account="6422",
    )
    assert inv.on_hand(db, ctx.company_id) == []


def test_opening_validation_and_locks(db, ctx, item, wh):
    with pytest.raises(BusinessError):
        inv.replace_opening(
            db, ctx, [inv.OpeningRow(item.id, wh.id, D(0), 1000)]
        )
    inv.replace_opening(
        db, ctx, [inv.OpeningRow(item.id, wh.id, D(2), 200_000)]
    )
    issue(db, ctx, wh, item, date(2023, 2, 1), 2)
    # lowering the opening stock would make later stock negative
    with pytest.raises(BusinessError):
        inv.replace_opening(
            db, ctx, [inv.OpeningRow(item.id, wh.id, D(1), 100_000)]
        )
    db.rollback()
    locks.lock_period(db, ctx, 2023, 1)
    with pytest.raises(BusinessError):
        inv.replace_opening(
            db, ctx, [inv.OpeningRow(item.id, wh.id, D(5), 500_000)]
        )


def test_real_sales_purchases_modules(db, ctx, item, wh, company):
    """Integrate with group C's real sales/purchase modules.

    Skipped if they are not available.
    """
    sales = pytest.importorskip("app.features.sales.service")
    purchases = pytest.importorskip("app.features.purchases.service")
    common = pytest.importorskip("app.features.sales.common")
    kh = partners.create(
        db, ctx, partners.PartnerInput(name="Công ty Khách", is_customer=True)
    )
    ncc = partners.create(
        db, ctx, partners.PartnerInput(name="Công ty NCC", is_supplier=True)
    )

    def header(d, p):
        return common.TradeHeader(
            date=d,
            doc_date=d,
            partner_id=p.id,
            partner_name=p.name,
            address="",
            tax_code="",
            contact_name="",
            invoice_template="",
            invoice_series="C26TAA",
            invoice_no="1",
            invoice_date=d,
            payment_method="credit",
            bank_account_id=None,
            due_date=None,
            description="",
            note="",
        )

    purchases.create(
        db,
        ctx,
        purchases.PurchaseInput(
            kind="purchase",
            header=header(date(2024, 10, 1), ncc),
            lines=[
                common.TradeLine(
                    description=item.name,
                    amount=1_000_000,
                    vat_rate="10",
                    vat_amount=100_000,
                    item_id=item.id,
                    unit="cái",
                    quantity=D(10),
                    unit_price=D(100_000),
                    account="156",
                )
            ],
        ),
        post=True,
    )
    assert inv.on_hand(db, company.id, item_id=item.id)[0]["quantity"] == 10
    with pytest.raises(BusinessError):
        sales.create(
            db,
            ctx,
            sales.SalesInput(
                kind="sale",
                header=header(date(2024, 10, 2), kh),
                lines=[
                    common.TradeLine(
                        description=item.name,
                        amount=5_000_000,
                        vat_rate="10",
                        vat_amount=500_000,
                        item_id=item.id,
                        unit="cái",
                        quantity=D(11),
                        unit_price=D(500_000),
                        account="5111",
                    )
                ],
            ),
            post=True,
        )
    db.rollback()
    sales.create(
        db,
        ctx,
        sales.SalesInput(
            kind="sale",
            header=header(date(2024, 10, 3), kh),
            lines=[
                common.TradeLine(
                    description=item.name,
                    amount=3_000_000,
                    vat_rate="10",
                    vat_amount=300_000,
                    item_id=item.id,
                    unit="cái",
                    quantity=D(6),
                    unit_price=D(500_000),
                    account="5111",
                )
            ],
        ),
        post=True,
    )
    run = costing.run_costing(db, ctx, 2024, 10)
    gv = db.get(JournalEntry, run.entry_id)
    assert [
        (ln.debit_account, ln.credit_account, ln.amount) for ln in gv.lines
    ] == [("632", "156", 600_000)]
    assert queries.balance(db, company.id, "156") == 400_000


def test_allow_negative_setting(db, ctx, item, wh):
    from app.features.org.service import update_company_settings

    with pytest.raises(BusinessError):
        issue(db, ctx, wh, item, date(2024, 2, 1), 1)
    db.rollback()
    update_company_settings(
        db, ctx, "inventory", {"allow_negative_stock": True}
    )
    doc = issue(db, ctx, wh, item, date(2024, 2, 1), 1)
    assert doc.status == "posted"
