"""Purchase service tests.

Journal lines, non-deductible VAT, fixed assets 1332, returns, XML
e-invoice import.
"""

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.errors import BusinessError
from app.features.items import service as items
from app.features.journal import queries
from app.features.journal.models import Attachment, JournalEntry
from app.features.partners import service as ps
from app.features.purchases import einvoice
from app.features.purchases import einvoice_import as imp
from app.features.purchases.models import PurchaseDoc, PurchaseLine
from app.features.sales.models import SalesDoc
from tests.conftest import post
from tests.sales.helpers import buy, line

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def supplier(db, ctx):
    return ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="NCC Văn phòng phẩm", is_supplier=True, tax_code="0301234567"
        ),
    )


def test_purchase_goods_and_services(db, ctx, supplier):
    doc = buy(
        db,
        ctx,
        date(2026, 3, 10),
        [
            line(1_000_000, "10", "156", desc="Hàng A"),
            line(500_000, "8", "6422", desc="Văn phòng phẩm"),
            line(200_000, "10", "6422", desc="Tiếp khách", deductible=False),
        ],
        partner_id=supplier.id,
        invoice_no="457",
        series="C26TVP",
    )
    entry = db.get(JournalEntry, doc.entry_id)
    assert entry.number == "MH2026-0001" and entry.source == "purchases"
    pairs = sorted(
        (ln.debit_account, ln.credit_account, ln.amount) for ln in entry.lines
    )
    assert pairs == sorted(
        [
            ("156", "331", 1_000_000),
            ("6422", "331", 500_000),
            ("6422", "331", 220_000),
            ("1331", "331", 100_000),
            ("1331", "331", 40_000),
        ]
    )
    assert all(ln.credit_partner_id == supplier.id for ln in entry.lines)
    assert (
        queries.balance(db, ctx.company_id, "331", partner_id=supplier.id)
        == -1_860_000
    )
    recs = {
        (v.vat_rate, v.deductible): (v.goods_amount, v.vat_amount)
        for v in entry.vat_records
    }
    assert recs == {
        ("10", True): (1_000_000, 100_000),
        ("8", True): (500_000, 40_000),
        ("10", False): (200_000, 20_000),
    }
    lines = db.scalars(
        select(PurchaseLine)
        .where(PurchaseLine.doc_id == doc.id)
        .order_by(PurchaseLine.line_no)
    ).all()
    assert [(ln.debit_account, ln.amount, ln.deductible) for ln in lines] == [
        ("156", 1_000_000, True),
        ("6422", 500_000, True),
        ("6422", 200_000, False),
    ]


def test_fixed_asset_uses_1332_and_cash_payment_checks_balance(
    db, ctx, supplier
):
    with pytest.raises(BusinessError):
        buy(
            db,
            ctx,
            date(2026, 3, 10),
            [line(30_000_000, "10", "2111", desc="Máy chủ")],
            method="cash",
            partner_id=supplier.id,
        )
    post(db, ctx, date(2026, 3, 1), "1111", "4111", 50_000_000, "PT")
    doc = buy(
        db,
        ctx,
        date(2026, 3, 10),
        [line(30_000_000, "10", "2111", desc="Máy chủ")],
        method="cash",
        partner_id=supplier.id,
    )
    entry = db.get(JournalEntry, doc.entry_id)
    pairs = sorted(
        (ln.debit_account, ln.credit_account, ln.amount) for ln in entry.lines
    )
    assert pairs == [("1332", "1111", 3_000_000), ("2111", "1111", 30_000_000)]
    assert queries.balance(db, ctx.company_id, "1111") == 17_000_000


def test_purchase_return(db, ctx, supplier):
    buy(
        db,
        ctx,
        date(2026, 3, 10),
        [line(1_000_000, "10", "156")],
        partner_id=supplier.id,
    )
    ret = buy(
        db,
        ctx,
        date(2026, 3, 12),
        [line(400_000, "10", "156")],
        kind="return",
        partner_id=supplier.id,
    )
    entry = db.get(JournalEntry, ret.entry_id)
    assert entry.doc_type == "TM"
    pairs = sorted(
        (ln.debit_account, ln.credit_account, ln.amount) for ln in entry.lines
    )
    assert pairs == [("331", "1331", 40_000), ("331", "156", 400_000)]
    assert entry.vat_records[0].group == "adjust_down"
    assert (
        queries.balance(db, ctx.company_id, "331", partner_id=supplier.id)
        == -660_000
    )


def test_parse_einvoice_xml():
    inv = einvoice.parse_xml(
        (FIX / "hd_dau_vao_1.xml").read_bytes(), "hd_dau_vao_1.xml"
    )
    assert (inv.template, inv.series, inv.number, inv.issue_date) == (
        "1",
        "C26TVP",
        "00000457",
        date(2026, 3, 10),
    )
    assert (
        inv.seller.tax_code == "0301234567"
        and inv.buyer.tax_code == "0000000001"
    )
    assert [
        (ln.description, ln.amount, ln.vat_rate, ln.vat_amount)
        for ln in inv.lines
    ] == [
        ("Giấy in A4 Double A", 1_400_000, "8", 112_000),
        ("Mực in laser", 999_999, "10", 100_000),
    ]
    assert inv.total_payment == 2_611_999 and inv.tax_authority_code
    assert inv.key == "0301234567|1C26TVP|457"
    wrapped = einvoice.parse_xml((FIX / "hd_dau_ra_1.xml").read_bytes())
    assert (
        wrapped.number == "12"
        and wrapped.buyer.buyer_person == "Nguyễn Thị Mua"
    )
    with pytest.raises(einvoice.EInvoiceError):
        einvoice.parse_xml((FIX / "xxe.xml").read_bytes(), "xxe.xml")
    with pytest.raises(einvoice.EInvoiceError):
        einvoice.parse_xml(b"<root/>", "x.xml")


def test_import_einvoices_creates_drafts_and_dedupes(db, ctx, company):
    items.create(
        db,
        ctx,
        items.ItemInput(
            name="Giấy in A4 Double A",
            code="GIAY-A4",
            kind="material",
            unit="Ram",
            vat_rate="8",
        ),
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "in/hd_dau_vao_1.xml", (FIX / "hd_dau_vao_1.xml").read_bytes()
        )
        zf.writestr(
            "hd_nguoi_khac.xml", (FIX / "hd_nguoi_khac.xml").read_bytes()
        )
        zf.writestr("readme.txt", b"bo qua")
    files = [
        ("hoa_don.zip", buf.getvalue()),
        ("hd_dau_ra_1.xml", (FIX / "hd_dau_ra_1.xml").read_bytes()),
        ("xxe.xml", (FIX / "xxe.xml").read_bytes()),
    ]
    prev = imp.preview(db, ctx, files)
    status = {r["ref"]: (r["direction"], r["status"]) for r in prev.rows}
    assert status == {
        "1C26TVP-00000457": ("in", "new"),
        "1C26TXX-99": (None, "foreign"),
        "1C26TNV-12": ("out", "new"),
    }
    assert len(prev.errors) == 1 and "xxe.xml" in prev.errors[0]
    res = imp.confirm(
        db,
        ctx,
        prev.token,
        {r["key"] for r in prev.rows},
        purchase_account="6422",
    )
    assert res["in"] == 1 and res["out"] == 1 and not res["errors"]
    pdoc = db.scalars(select(PurchaseDoc)).one()
    assert (
        pdoc.invoice_no == "00000457"
        and pdoc.seller_tax_code == "0301234567"
        and pdoc.partner.is_supplier
    )
    lines = sorted(
        (
            ln.description,
            ln.debit_account,
            ln.amount,
            ln.vat_amount,
            ln.item_id is not None,
        )
        for ln in pdoc.lines
    )
    assert lines == [
        ("Giấy in A4 Double A", "152", 1_400_000, 112_000, True),
        ("Mực in laser", "6422", 999_999, 100_000, False),
    ]
    sdoc = db.scalars(select(SalesDoc)).one()
    assert (
        sdoc.total_amount == 27_500_000 and sdoc.buyer_name == "Nguyễn Thị Mua"
    )
    assert db.get(JournalEntry, sdoc.entry_id).status == "draft"
    assert len(db.scalars(select(Attachment)).all()) == 2
    # Upload again: duplicate
    prev2 = imp.preview(
        db, ctx, [("a.xml", (FIX / "hd_dau_vao_1.xml").read_bytes())]
    )
    assert prev2.rows[0]["status"] == "duplicate"
    assert imp.confirm(db, ctx, prev2.token, {prev2.rows[0]["key"]})["in"] == 0
