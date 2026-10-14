"""Receivables/payables: party summary, detail ledger, FIFO aging."""

from datetime import date

import pytest

from app.features.debts import service as ds
from app.features.partners import service as ps
from tests.conftest import post
from tests.sales.helpers import buy, line, sell


@pytest.fixture()
def data(db, ctx):
    a = ps.create(
        db,
        ctx,
        ps.PartnerInput(
            name="Khách A", is_customer=True, payment_terms_days=30
        ),
    )
    b = ps.create(db, ctx, ps.PartnerInput(name="Khách B", is_customer=True))
    s = ps.create(db, ctx, ps.PartnerInput(name="NCC C", is_supplier=True))
    sell(
        db,
        ctx,
        date(2026, 6, 1),
        [line(10_000_000, "10")],
        partner_id=a.id,
        invoice_no="1",
    )  # 11M, due 01/07
    sell(
        db,
        ctx,
        date(2026, 8, 1),
        [line(5_000_000, "10")],
        partner_id=a.id,
        invoice_no="2",
    )  # 5.5M, due 31/08
    sell(
        db,
        ctx,
        date(2026, 9, 1),
        [line(1_000_000, "KCT")],
        partner_id=b.id,
        invoice_no="3",
        due=date(2026, 10, 30),
    )
    post(db, ctx, date(2026, 6, 1), "1111", "4111", 50_000_000, "PT")
    # Customer A pays 12M (all of invoice 1 and 1M of invoice 2);
    # customer B overpays in advance
    post(
        db,
        ctx,
        date(2026, 7, 15),
        "1111",
        "131",
        12_000_000,
        "PT",
        credit_partner_id=a.id,
    )
    post(
        db,
        ctx,
        date(2026, 9, 5),
        "1111",
        "131",
        1_500_000,
        "PT",
        credit_partner_id=b.id,
    )
    buy(
        db,
        ctx,
        date(2026, 7, 1),
        [line(3_000_000, "10", "156")],
        partner_id=s.id,
        invoice_no="7",
    )
    return a, b, s


def test_summary(db, company, data):
    a, b, s = data
    sm = ds.summary(
        db, company.id, "receivable", date(2026, 7, 1), date(2026, 9, 30)
    )
    rows = {
        r.partner.name: (r.opening, r.debit, r.credit, r.closing)
        for r in sm.rows
    }
    assert rows["Khách A"] == (11_000_000, 5_500_000, 12_000_000, 4_500_000)
    assert rows["Khách B"] == (0, 1_000_000, 1_500_000, -500_000)
    t = sm.totals
    assert t["closing_debit"] == 4_500_000 and t["closing_credit"] == 500_000
    pay = ds.summary(
        db, company.id, "payable", date(2026, 1, 1), date(2026, 12, 31)
    )
    assert [(r.partner.name, r.closing) for r in pay.rows] == [
        ("NCC C", -3_300_000)
    ]


def test_partner_ledger(db, company, data):
    a, _, _ = data
    led = ds.partner_ledger(
        db,
        company.id,
        "receivable",
        a.id,
        date(2026, 7, 1),
        date(2026, 12, 31),
    )
    assert led.opening == 11_000_000
    assert [(r.debit, r.credit, r.balance) for r in led.rows] == [
        (0, 12_000_000, -1_000_000),
        (5_000_000, 0, 4_000_000),
        (500_000, 0, 4_500_000),
    ]


def test_aging_fifo(db, company, data):
    a, b, s = data
    rows = {
        r.partner.name: r
        for r in ds.aging(db, company.id, "receivable", date(2026, 10, 14))
    }
    ra = rows["Khách A"]
    assert ra.balance == 4_500_000
    assert [(i.date, i.amount) for i in ra.items] == [
        (date(2026, 8, 1), 4_500_000)
    ]
    # 44 days overdue
    assert (
        ra.items[0].due == date(2026, 8, 31) and ra.buckets["d60"] == 4_500_000
    )
    rb = rows["Khách B"]
    assert (
        rb.balance == -500_000
        and rb.buckets["prepaid"] == 500_000
        and rb.items == []
    )
    pay = ds.aging(db, company.id, "payable", date(2026, 10, 14))
    assert (
        pay[0].balance == 3_300_000 and pay[0].buckets["over90"] == 3_300_000
    )
