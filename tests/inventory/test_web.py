"""Inventory web.

Pages, receipt/issue forms, costing, stock ledgers, PDF/Excel export,
permissions.
"""

import io
from datetime import date
from decimal import Decimal

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader
from sqlalchemy import select

from app.core.registry import CASHIER, CHIEF, VIEWER
from app.features.inventory import service as inv
from app.features.inventory.models import StockDoc
from app.features.items import service as items_svc
from app.features.journal import opening as gl_opening
from tests.conftest import ROLE_EMAILS


@pytest.fixture()
def setup_stock(db, ctx):
    it = items_svc.create(
        db,
        ctx,
        items_svc.ItemInput(name="Chuột không dây", code="HH0002", unit="cái"),
    )
    wh = inv.default_warehouse(db, ctx.company_id)
    db.commit()
    gl_opening.replace_opening(
        db,
        ctx,
        [
            gl_opening.OpeningIn("156", debit=500_000),
            gl_opening.OpeningIn("4111", credit=500_000),
        ],
    )
    inv.replace_opening(
        db, ctx, [inv.OpeningRow(it.id, wh.id, Decimal(5), 500_000)]
    )
    return it, wh


def _lines(item, qty, price=None, amount=None):
    data = {
        "lines-count": "1",
        "lines-0-item_id": str(item.id),
        "lines-0-quantity": str(qty),
        "lines-0-description": item.name,
        "lines-0-unit": item.unit,
    }
    if price is not None:
        data["lines-0-unit_price"] = str(price)
    if amount is not None:
        data["lines-0-amount"] = str(amount)
    return data


def test_pages_and_flow(web, users, db, setup_stock):
    item, wh = setup_stock
    web.login(ROLE_EMAILS[CHIEF])
    pages = [
        "/inventory/warehouses",
        "/inventory/opening",
        "/inventory/receipts",
        "/inventory/issues",
        "/inventory/transfers",
        "/inventory/receipts/new",
        "/inventory/issues/new",
        "/inventory/transfers/new",
        "/inventory/costing",
        "/inventory/stock-card",
        "/inventory/ledger",
        "/inventory/summary",
        "/inventory/summary?mode=year&year=2024&by_warehouse=1",
    ]
    for url in pages:
        assert web.get(url).status_code == 200, url
    # Stock receipt: save a draft, then post via the shared endpoint
    data = {
        "date": "2024-03-05",
        "warehouse_id": str(wh.id),
        "counter_account": "3388",
        "contact_name": "Anh Giao",
        "description": "Nhập hàng thừa",
        **_lines(item, 10, price=120000),
    }
    r = web.post("/inventory/receipts", data, follow_redirects=False)
    assert r.status_code == 303, r.text[:400]
    url = r.headers["location"]
    entry_id = int(url.rsplit("/", 1)[1])
    page = web.get(url)
    assert page.status_code == 200 and "1.200.000" in page.text
    assert web.get(f"/inventory/receipts/{entry_id}/edit").status_code == 200
    r = web.post(f"/entries/{entry_id}/post", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == url
    assert "NK2024-0001" in web.get(url).text
    # Stock issue: save and post
    r = web.post(
        "/inventory/issues",
        {
            "date": "2024-03-10",
            "warehouse_id": str(wh.id),
            "counter_account": "6422",
            "description": "Xuất dùng văn phòng",
            "action": "post",
            **_lines(item, 12),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    doc = db.scalars(select(StockDoc).where(StockDoc.kind == "issue")).one()
    assert doc.status == "posted" and doc.number == "XK2024-0001"
    assert "Chưa tính giá" in web.get(doc.url).text
    # Issuing more than on hand -> saved as draft with an error
    r = web.post(
        "/inventory/issues",
        {
            "date": "2024-03-11",
            "warehouse_id": str(wh.id),
            "counter_account": "6422",
            "action": "post",
            **_lines(item, 100),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    draft = db.scalars(
        select(StockDoc)
        .where(StockDoc.kind == "issue")
        .order_by(StockDoc.id.desc())
    ).first()
    assert draft.status == "draft" and r.headers["location"] == draft.url
    web.post(f"/inventory/issues/{draft.id}/delete")
    # A form with errors keeps the entered data
    r = web.post(
        "/inventory/issues",
        {
            "date": "2024-03-11",
            "warehouse_id": str(wh.id),
            "counter_account": "",
            **_lines(item, 1),
        },
    )
    assert r.status_code == 422 and "Chọn TK Nợ" in r.text
    # March costing: (500,000 + 1,200,000) / 15 = 113,333.33 -> x 12 =
    # 1,360,000
    r = web.post(
        "/inventory/costing/run",
        {"year": "2024", "month": "3"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = web.get("/inventory/costing")
    assert "1.360.000" in page.text
    web.get(
        f"/inventory/stock-card?item_id={item.id}&mode=month&month=2024-03"
    )
    led = web.get(
        f"/inventory/ledger?item_id={item.id}&mode=month&month=2024-03"
    )
    assert "340.000" in led.text  # closing stock 3 pcs
    # Print / export
    for u in (
        f"/inventory/receipts/{entry_id}/print.pdf",
        f"/inventory/issues/{doc.id}/print.pdf",
        f"/inventory/stock-card.pdf?item_id={item.id}&mode=year&year=2024",
        f"/inventory/ledger.pdf?item_id={item.id}&mode=year&year=2024",
        "/inventory/summary.pdf?mode=year&year=2024",
    ):
        r = web.get(u)
        assert (
            r.status_code == 200
            and r.headers["content-type"] == "application/pdf"
        ), u
        assert len(PdfReader(io.BytesIO(r.content)).pages) >= 1
    for u in (
        f"/inventory/stock-card.xlsx?item_id={item.id}&mode=year&year=2024",
        f"/inventory/ledger.xlsx?item_id={item.id}&mode=year&year=2024",
        "/inventory/summary.xlsx?mode=year&year=2024",
    ):
        r = web.get(u)
        assert r.status_code == 200, u
        load_workbook(io.BytesIO(r.content))
    # Cancel the stock issue
    r = web.post(
        f"/inventory/issues/{doc.id}/cancel",
        {"reason": "Nhầm"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.get(StockDoc, doc.id).status == "cancelled"
    # English
    web.get("/lang/en")
    for url in pages + [doc.url, url, "/inventory/costing"]:
        assert web.get(url).status_code == 200, url


def test_warehouse_and_opening_forms(web, users, db, setup_stock):
    item, wh = setup_stock
    web.login(ROLE_EMAILS[CHIEF])
    r = web.post(
        "/inventory/warehouses",
        {"code": "kho02", "name": "Kho Hà Nội", "is_active": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "KHO02" in web.get("/inventory/warehouses").text
    r = web.post(
        "/inventory/opening",
        {"warehouse_id": str(wh.id), **_lines(item, 6, amount=600000)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        inv.on_hand(db, users[CHIEF].company_id, item_id=item.id)[0][
            "quantity"
        ]
        == 6
    )


def test_permissions(web, users, setup_stock):
    web.login(ROLE_EMAILS[CASHIER])
    assert web.get("/inventory/receipts").status_code == 403
    web.login(ROLE_EMAILS[VIEWER])
    assert web.get("/inventory/summary").status_code == 200
    assert web.get("/inventory/receipts/new").status_code == 403
    assert (
        web.post(
            "/inventory/costing/run", {"year": "2024", "month": "1"}
        ).status_code
        == 403
    )
    assert (
        web.post(
            "/inventory/warehouses", {"code": "X", "name": "X"}
        ).status_code
        == 403
    )


def test_ai_tools(db, ctx, setup_stock):
    from app.features.inventory import ai_tools

    item, _wh = setup_stock
    out = ai_tools.get_stock_on_hand(
        db, ctx, item_code="HH0002", date="2024-01-01"
    )
    assert (
        out["rows"][0]["quantity"] == "5.000"
        or Decimal(out["rows"][0]["quantity"]) == 5
    )
    assert out["total_value"] == 500_000
    mv = ai_tools.get_inventory_movements(
        db,
        ctx,
        item_code="HH0002",
        start="2023-01-01",
        end=date.today().isoformat(),
    )
    assert mv["opening"]["value"] == 500_000 or mv["rows"]


def test_checks(db, ctx, setup_stock):
    from app.features.inventory import checks

    assert checks.check_gl(db, ctx) == []
    item, wh = setup_stock
    inv.replace_opening(
        db, ctx, [inv.OpeningRow(item.id, wh.id, Decimal(5), 400_000)]
    )
    assert any("đầu kỳ" in i.message for i in checks.check_gl(db, ctx))
    assert checks.check_negative(db, ctx) == []
