"""AI tools and data checks of the trade group.

Covers sales/purchases, tax and receivables/payables.
"""

from datetime import date

from app.core.registry import ACCOUNTANT, VIEWER, registry
from app.features.journal.models import JournalEntry
from app.features.partners import service as ps
from tests.conftest import post
from tests.sales.helpers import line, sell


def _tool(name):
    return registry.ai_tools[name]


def test_tools_registered_with_schema():
    from app.core.registry import load_features

    load_features()
    for name in (
        "create_draft_sales_invoice",
        "create_draft_purchase",
        "get_revenue_by_month",
        "get_receivables",
        "get_payables",
        "get_debt_aging",
        "get_vat_summary",
    ):
        t = registry.ai_tools[name]
        schema = t.openai_schema()
        assert (
            schema["type"] == "function"
            and schema["function"]["parameters"]["type"] == "object"
        )
    assert registry.ai_tools["create_draft_sales_invoice"].write
    assert not registry.ai_tools["get_vat_summary"].write


def test_draft_tools_create_drafts_only(db, ctxs):
    ctx = ctxs[ACCOUNTANT]
    res = _tool("create_draft_sales_invoice").func(
        db,
        ctx,
        date="2026-03-05",
        customer_name="Khách AI",
        lines=[
            {
                "description": "Dịch vụ",
                "quantity": 2,
                "unit_price": 1_000_000,
                "vat_rate": "10",
            }
        ],
    )
    e = db.get(JournalEntry, res["draft_id"])
    assert (
        e.status == "draft"
        and e.doc_type == "BH"
        and e.total == 2_200_000
        and res["url"] == f"/sales/{e.id}"
    )
    res = _tool("create_draft_purchase").func(
        db,
        ctx,
        date="2026-03-05",
        supplier_name="NCC AI",
        lines=[
            {
                "description": "Văn phòng phẩm",
                "amount": 500_000,
                "vat_rate": "8",
                "account": "6422",
            }
        ],
    )
    e = db.get(JournalEntry, res["draft_id"])
    assert e.status == "draft" and e.doc_type == "MH" and e.total == 540_000
    assert not ctxs[VIEWER].can(_tool("create_draft_purchase").perm)


def test_read_tools(db, ctx):
    c = ps.create(
        db, ctx, ps.PartnerInput(name="Khách A", code="KHA", is_customer=True)
    )
    sell(
        db,
        ctx,
        date(2026, 2, 1),
        [line(10_000_000, "10")],
        partner_id=c.id,
        invoice_no="1",
    )
    sell(
        db,
        ctx,
        date(2026, 3, 1),
        [line(2_000_000, "8")],
        partner_id=c.id,
        invoice_no="2",
    )
    rec = _tool("get_receivables").func(
        db, ctx, partner_code="KHA", date="2026-12-31"
    )
    assert rec["partners"][0]["balance"] == 13_160_000
    aging = _tool("get_debt_aging").func(
        db, ctx, kind="receivable", date="2026-12-31"
    )
    assert aging["totals"]["balance"] == 13_160_000
    rev = _tool("get_revenue_by_month").func(db, ctx, year=2026)
    assert (
        rev["months"][1]["sales_revenue"] == 10_000_000
        and rev["total_sales_revenue"] == 12_000_000
    )
    vat = _tool("get_vat_summary").func(
        db, ctx, period_start="2026-01-01", period_end="2026-03-31"
    )
    assert (
        vat["indicators"]["33"] == 1_160_000
        and vat["vat_payable"] == 1_160_000
    )


def test_checks(db, ctx):
    from app.features.checks.router import run_checks

    c = ps.create(db, ctx, ps.PartnerInput(name="Khách A", is_customer=True))
    sell(
        db,
        ctx,
        date(2026, 1, 5),
        [line(1_000_000)],
        partner_id=c.id,
        invoice_no="7",
        series="C26TAA",
    )
    sell(
        db,
        ctx,
        date(2026, 1, 6),
        [line(1_000_000)],
        partner_id=c.id,
        invoice_no="7",
        series="C26TAA",
    )
    # input VAT without an invoice
    post(db, ctx, date(2026, 1, 7), "1331", "3388", 100_000, "PK")
    results = {
        r["code"]: r
        for r in run_checks(
            db,
            ctx,
            codes={
                "vat_duplicate_invoice",
                "vat_missing_invoice",
                "debts_overdue_90",
                "partner_tax_code",
            },
        )
    }
    assert len(results["vat_duplicate_invoice"]["issues"]) == 1
    assert len(results["vat_missing_invoice"]["issues"]) == 1
    # sold in January, due date = voucher date
    assert len(results["debts_overdue_90"]["issues"]) == 1
    assert results["partner_tax_code"]["issues"] == []
