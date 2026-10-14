"""Web: asset pages, permissions, PDF/Excel open, English."""

import io
from datetime import date

from openpyxl import load_workbook
from pypdf import PdfReader

from app.core.registry import ACCOUNTANT, CASHIER, CHIEF, VIEWER
from app.features.assets import service
from app.features.assets.models import RUN_ALLOCATION, RUN_DEPRECIATION
from tests.conftest import ROLE_EMAILS, post


def _seed(db, ctxs):
    ctx = ctxs[ACCOUNTANT]
    buy = post(
        db,
        ctx,
        date(2024, 1, 15),
        "2111",
        "3388",
        36_000_000,
        "PK",
        "Mua máy chủ",
    )
    a = service.create_asset(
        db,
        ctx,
        service.AssetInput(
            name="Máy chủ Dell",
            cost=36_000_000,
            useful_life_months=36,
            acquired_date=date(2024, 1, 15),
            entry_id=buy.id,
            source="purchase",
        ),
    )
    service.execute_run(db, ctx, RUN_DEPRECIATION, 2024, 1)
    tool = post(
        db,
        ctx,
        date(2024, 1, 20),
        "242",
        "3388",
        6_000_000,
        "PK",
        "Mua bàn ghế",
    )
    p = service.create_prepaid(
        db,
        ctx,
        service.PrepaidInput(
            name="Bàn ghế",
            amount=6_000_000,
            months=6,
            start_month=date(2024, 1, 1),
            entry_id=tool.id,
        ),
    )
    run = service.execute_run(db, ctx, RUN_ALLOCATION, 2024, 1)
    return a, p, buy, run


def test_pages_and_exports(web, db, users, ctxs):
    a, p, buy, run = _seed(db, ctxs)
    web.login(ROLE_EMAILS[CHIEF])
    pages = [
        "/assets",
        "/assets?status=all",
        "/assets/new",
        f"/assets/new?from_entry={buy.id}",
        f"/assets/{a.id}",
        f"/assets/{a.id}/edit",
        "/assets/depreciation",
        "/assets/depreciation?year=2024&month=1",
        "/prepaid",
        "/prepaid/new",
        f"/prepaid/new?from_entry={p.entry_id}",
        f"/prepaid/{p.id}",
        "/prepaid/allocation?year=2024&month=1",
    ]
    for url in pages:
        r = web.get(url)
        assert r.status_code == 200, (url, r.text[:300])
    assert "Máy chủ Dell" in web.get("/assets").text
    # KH voucher -> the month's depreciation page
    r = web.get(f"/assets/entries/{run.entry.id}", follow_redirects=False)
    assert (
        r.status_code == 303
        and "/prepaid/allocation?year=2024&month=1" in r.headers["location"]
    )
    for url in (
        f"/assets/{a.id}/card.pdf",
        "/assets/register.pdf?on=2024-01-31",
        "/assets/depreciation/sheet.pdf?year=2024&month=1",
        "/prepaid/allocation/sheet.pdf?year=2024&month=1",
    ):
        r = web.get(url)
        assert (
            r.status_code == 200
            and r.headers["content-type"] == "application/pdf"
        ), url
        text = "".join(
            pg.extract_text() for pg in PdfReader(io.BytesIO(r.content)).pages
        )
        assert "Đơn vị" in text, url
    r = web.get("/assets/register.xlsx?on=2024-01-31")
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.active.title == "S09-DNN"
    assert (
        web.get(
            "/assets/depreciation/sheet.xlsx?year=2024&month=1"
        ).status_code
        == 200
    )
    web.get("/lang/en")
    for url in pages:
        assert web.get(url).status_code == 200, url


def test_web_create_run_dispose(web, db, users, ctxs, company):
    web.login(ROLE_EMAILS[ACCOUNTANT])
    web.get("/assets/new")
    r = web.post(
        "/assets",
        {
            "name": "Laptop",
            "cost": "24tr",
            "useful_life_months": "24",
            "acquired_date": "2024-03-10",
            "category": "office",
            "asset_account": "2111",
            "expense_account": "6422",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303, r.text[:500]
    aid = int(r.headers["location"].rsplit("/", 1)[1])
    r = web.post(
        "/assets",
        {
            "name": "",
            "cost": "0",
            "useful_life_months": "0",
            "acquired_date": "2024-03-10",
        },
    )
    assert r.status_code == 422
    r = web.post(
        "/assets/depreciation/run",
        {"year": "2024", "month": "3"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert (
        "1.000.000" in web.get("/assets/depreciation?year=2024&month=3").text
    )
    r = web.post(
        f"/assets/{aid}/dispose",
        {"date": "2024-04-05", "reason": "Mất", "loss_account": "811"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Đã ghi giảm" in web.get(f"/assets/{aid}").text
    web.post(
        "/prepaid",
        {
            "name": "Máy pha cà phê",
            "amount": "3tr",
            "months": "3",
            "start_month": "2024-03",
            "kind": "tool",
            "expense_account": "6422",
        },
    )
    assert "Máy pha cà phê" in web.get("/prepaid").text


def test_permissions(web, db, users, ctxs):
    a, p, _buy, _run = _seed(db, ctxs)
    web.login(ROLE_EMAILS[VIEWER])
    assert web.get("/assets").status_code == 200
    assert web.get(f"/assets/{a.id}").status_code == 200
    assert web.get("/assets/new").status_code == 403
    assert (
        web.post(
            "/assets/depreciation/run", {"year": "2024", "month": "2"}
        ).status_code
        == 403
    )
    web.login(ROLE_EMAILS[CASHIER])
    assert web.get("/assets").status_code == 403
    assert web.get("/prepaid").status_code == 403
