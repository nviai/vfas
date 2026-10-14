"""Sales service tests.

Journal lines, output VAT invoices, returns, cash sales, deleting
drafts.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.errors import BusinessError
from app.features.journal import queries
from app.features.journal import service as js
from app.features.journal.models import JournalEntry, VatRecord
from app.features.partners import service as ps
from app.features.sales.models import SalesDoc, SalesLine
from tests.conftest import post
from tests.sales.helpers import line, sell


@pytest.fixture()
def customer(db, ctx):
    return ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="Công ty Khách Hàng A",
            is_customer=True,
            tax_code="0101243150",
            payment_terms_days=30,
        ),
    )


def test_credit_sale_posts_revenue_vat_and_receivable(db, ctx, customer):
    doc = sell(
        db,
        ctx,
        date(2026, 3, 5),
        [
            line(10_000_000, "10"),
            line(5_000_000, "8", "5111", desc="Hàng hóa"),
            line(2_000_000, "KCT", desc="Dịch vụ không chịu thuế"),
        ],
        partner_id=customer.id,
        invoice_no="00000123",
    )
    entry = db.get(JournalEntry, doc.entry_id)
    assert (
        entry.status == "posted"
        and entry.number == "BH2026-0001"
        and entry.source == "sales"
    )
    assert entry.source_id == doc.id and entry.ref_no == "C26TAA-00000123"
    pairs = sorted(
        (ln.debit_account, ln.credit_account, ln.amount) for ln in entry.lines
    )
    assert pairs == sorted(
        [
            ("131", "5113", 10_000_000),
            ("131", "5111", 5_000_000),
            ("131", "5113", 2_000_000),
            ("131", "33311", 1_000_000),
            ("131", "33311", 400_000),
        ]
    )
    assert all(ln.debit_partner_id == customer.id for ln in entry.lines)
    assert doc.total_amount == 18_400_000 and doc.vat_amount == 1_400_000
    assert doc.due_date == date(2026, 4, 4)
    vats = {
        v.vat_rate: (v.goods_amount, v.vat_amount, v.direction)
        for v in entry.vat_records
    }
    assert vats == {
        "10": (10_000_000, 1_000_000, "out"),
        "8": (5_000_000, 400_000, "out"),
        "KCT": (2_000_000, 0, "out"),
    }
    assert (
        queries.balance(db, ctx.company_id, "131", partner_id=customer.id)
        == 18_400_000
    )
    # Item lines for the inventory group
    lines = db.scalars(
        select(SalesLine)
        .where(SalesLine.doc_id == doc.id)
        .order_by(SalesLine.line_no)
    ).all()
    assert [ln.amount for ln in lines] == [10_000_000, 5_000_000, 2_000_000]


def test_discount_and_cash_sale(db, ctx):
    doc = sell(
        db,
        ctx,
        date(2026, 3, 6),
        [line(1_000_000, "10", discount=100_000)],
        method="cash",
        partner_name="Khách lẻ",
    )
    entry = db.get(JournalEntry, doc.entry_id)
    pairs = sorted(
        (ln.debit_account, ln.credit_account, ln.amount) for ln in entry.lines
    )
    assert pairs == [("1111", "33311", 90_000), ("1111", "5113", 900_000)]
    assert (
        doc.goods_amount == 1_000_000
        and doc.discount_amount == 100_000
        and doc.total_amount == 990_000
    )
    assert queries.balance(db, ctx.company_id, "1111") == 990_000


def test_sale_return_reverses(db, ctx, customer):
    sale = sell(
        db,
        ctx,
        date(2026, 3, 5),
        [line(10_000_000, "10")],
        partner_id=customer.id,
        invoice_no="1",
    )
    ret = sell(
        db,
        ctx,
        date(2026, 3, 20),
        [line(2_000_000, "10")],
        kind="return",
        partner_id=customer.id,
        invoice_no="2",
        original_entry_id=sale.entry_id,
    )
    entry = db.get(JournalEntry, ret.entry_id)
    assert entry.doc_type == "TL" and entry.number == "TL2026-0001"
    pairs = sorted(
        (ln.debit_account, ln.credit_account, ln.amount) for ln in entry.lines
    )
    assert pairs == [("33311", "131", 200_000), ("5113", "131", 2_000_000)]
    assert entry.vat_records[0].group == "adjust_down"
    assert (
        queries.balance(db, ctx.company_id, "131", partner_id=customer.id)
        == 8_800_000
    )


def test_draft_update_delete_and_validation(db, ctx, customer):
    from app.features.sales import service as ss
    from tests.sales.helpers import header

    doc = sell(
        db,
        ctx,
        date(2026, 3, 5),
        [line(1_000_000)],
        post=False,
        partner_id=customer.id,
    )
    entry = db.get(JournalEntry, doc.entry_id)
    assert entry.status == "draft"
    ss.update(
        db,
        ctx,
        doc,
        ss.SalesInput(
            kind="sale",
            header=header(date(2026, 3, 6), partner_id=customer.id),
            lines=[line(3_000_000, "5")],
        ),
    )
    assert doc.total_amount == 3_150_000 and entry.total == 3_150_000
    js.delete_draft(db, ctx, entry)
    assert db.get(SalesDoc, doc.id) is None
    assert db.scalars(select(VatRecord)).all() == []
    # Credit sale without a customer: posting is blocked (TK 131
    # requires a party)
    with pytest.raises(BusinessError):
        sell(db, ctx, date(2026, 3, 7), [line(1_000_000)])


def test_posted_sale_cannot_be_updated(db, ctx, customer):
    from app.features.sales import service as ss
    from tests.sales.helpers import header

    doc = sell(
        db, ctx, date(2026, 3, 5), [line(1_000_000)], partner_id=customer.id
    )
    with pytest.raises(BusinessError):
        ss.update(
            db,
            ctx,
            doc,
            ss.SalesInput(
                kind="sale",
                header=header(date(2026, 3, 5), partner_id=customer.id),
                lines=[line(2_000_000)],
            ),
        )
    # Can be cancelled while the period is open
    js.cancel_entry(db, ctx, db.get(JournalEntry, doc.entry_id), "Lập nhầm")
    assert queries.balance(db, ctx.company_id, "131") == 0


def test_partner_auto_created_from_name(db, ctx):
    doc = sell(
        db,
        ctx,
        date(2026, 3, 5),
        [line(500_000)],
        partner_name="Công ty Mới Toanh",
        tax_code="0312345678",
    )
    assert doc.partner_id is not None
    p = ps.get_partner(db, ctx.company_id, doc.partner_id)
    assert p.is_customer and p.tax_code == "0312345678"
    _ = post
