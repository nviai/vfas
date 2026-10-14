"""AI tools (OpenAI functions) for sales and purchases.

These tools only create DRAFT documents.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.core.dates import parse_date
from app.core.db import ulower
from app.core.errors import BusinessError
from app.core.registry import ai_tool
from app.features.items.models import Item
from app.features.journal.queries import account_match, ledger_source
from app.features.partners.models import Partner
from app.features.sales.common import (
    TradeHeader,
    TradeLine,
    parse_decimal,
    round_vnd,
    vat_of,
)

LINE_SCHEMA = {
    "type": "object",
    "properties": {
        "item_code": {
            "type": "string",
            "description": "Item code in the catalog (optional)",
        },
        "description": {"type": "string"},
        "quantity": {"type": "number"},
        "unit_price": {
            "type": "number",
            "description": "Price before VAT, VND",
        },
        "amount": {
            "type": "integer",
            "description": (
                "Line amount before VAT, VND (if no quantity/price)"
            ),
        },
        "vat_rate": {
            "type": "string",
            "enum": ["0", "5", "8", "10", "KCT", "KKKNT"],
        },
        "account": {
            "type": "string",
            "description": (
                "Revenue account (sales) or debit account (purchases), "
                "optional"
            ),
        },
    },
    "required": ["description"],
}


def find_partner(
    db, company_id: int, code: str | None, name: str | None
) -> Partner | None:
    if code:
        p = db.scalar(
            select(Partner).where(
                Partner.company_id == company_id, Partner.code == code.strip()
            )
        )
        if p:
            return p
    if name:
        return db.scalar(
            select(Partner).where(
                Partner.company_id == company_id,
                ulower(Partner.name) == name.strip().lower(),
            )
        )
    return None


def trade_lines(
    db, company_id: int, lines: list[dict], default_account
) -> list[TradeLine]:
    out = []
    for ln in lines or []:
        item = None
        if ln.get("item_code"):
            item = db.scalar(
                select(Item).where(
                    Item.company_id == company_id,
                    Item.code == str(ln["item_code"]),
                )
            )
            if item is None:
                raise BusinessError(f"Không có mã hàng {ln['item_code']}")
        qty = (
            parse_decimal(ln.get("quantity"))
            if ln.get("quantity") is not None
            else None
        )
        price = (
            parse_decimal(ln.get("unit_price"))
            if ln.get("unit_price") is not None
            else None
        )
        amount = int(ln.get("amount") or 0) or (
            round_vnd(qty * price)
            if qty is not None and price is not None
            else 0
        )
        rate = str(ln.get("vat_rate") or (item.vat_rate if item else "10"))
        out.append(
            TradeLine(
                description=str(
                    ln.get("description") or (item.name if item else "")
                )[:500],
                amount=amount,
                vat_rate=rate,
                vat_amount=vat_of(amount, rate),
                item_id=item.id if item else None,
                unit=item.unit if item else "",
                quantity=qty,
                unit_price=price,
                account=str(ln.get("account") or default_account(item)),
            )
        )
    if not out:
        raise BusinessError("Cần ít nhất một dòng hàng hóa, dịch vụ")
    return out


def header_from(
    args: dict, partner: Partner | None, partner_name: str
) -> TradeHeader:
    d = parse_date(args.get("date")) or date.today()
    return TradeHeader(
        date=d,
        doc_date=d,
        partner_id=partner.id if partner else None,
        partner_name="" if partner else (partner_name or ""),
        address="",
        tax_code=args.get("tax_code") or "",
        contact_name="",
        invoice_template=str(args.get("invoice_template") or ""),
        invoice_series=str(args.get("invoice_series") or "").upper(),
        invoice_no=str(args.get("invoice_no") or ""),
        invoice_date=d,
        payment_method=args.get("payment_method")
        if args.get("payment_method") in ("credit", "cash")
        else "credit",
        bank_account_id=None,
        due_date=None,
        description=str(args.get("description") or ""),
        note="Lập bởi trợ lý AI",
    )


@ai_tool(
    name="create_draft_sales_invoice",
    label="Lập nháp chứng từ bán hàng",
    perm="sales.create",
    write=True,
    description=(
        "Create a DRAFT sales document (revenue + output VAT, receivable "
        "131 or cash). The user must review and post it. Customer by code "
        "or name (new name creates a customer)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Accounting date YYYY-MM-DD",
            },
            "customer_code": {"type": "string"},
            "customer_name": {"type": "string"},
            "tax_code": {"type": "string"},
            "payment_method": {"type": "string", "enum": ["credit", "cash"]},
            "invoice_series": {"type": "string"},
            "invoice_no": {"type": "string"},
            "description": {"type": "string"},
            "lines": {"type": "array", "items": LINE_SCHEMA},
        },
        "required": ["lines"],
    },
)
def create_draft_sales_invoice(db, ctx, **args) -> dict:
    from app.features.org.models import Company
    from app.features.sales import service as ss

    company = db.get(Company, ctx.company_id)
    partner = find_partner(
        db,
        ctx.company_id,
        args.get("customer_code"),
        args.get("customer_name"),
    )
    lines = trade_lines(
        db,
        ctx.company_id,
        args.get("lines") or [],
        ss.default_revenue_account(company),
    )
    doc = ss.create(
        db,
        ctx,
        ss.SalesInput(
            kind="sale",
            header=header_from(args, partner, args.get("customer_name")),
            lines=lines,
        ),
    )
    return {
        "draft_id": doc.entry_id,
        "url": f"/sales/{doc.entry_id}",
        "summary": (
            f"Chứng từ bán hàng nháp {doc.partner_name or 'khách lẻ'}: "
            f"tổng {doc.total_amount:,} đ"
        ).replace(",", "."),
    }


@ai_tool(
    name="create_draft_purchase",
    label="Lập nháp chứng từ mua hàng",
    perm="purchases.create",
    write=True,
    description=(
        "Create a DRAFT purchase document (goods/services, deductible "
        "input VAT, payable 331 or cash). Supplier by code or name. Lines "
        "may set the debit account (156, 152, 153, 6422, 2111, 242…)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Accounting date YYYY-MM-DD",
            },
            "supplier_code": {"type": "string"},
            "supplier_name": {"type": "string"},
            "tax_code": {"type": "string"},
            "payment_method": {"type": "string", "enum": ["credit", "cash"]},
            "invoice_series": {"type": "string"},
            "invoice_no": {"type": "string"},
            "description": {"type": "string"},
            "lines": {"type": "array", "items": LINE_SCHEMA},
        },
        "required": ["lines"],
    },
)
def create_draft_purchase(db, ctx, **args) -> dict:
    from app.features.purchases import service as ps

    partner = find_partner(
        db,
        ctx.company_id,
        args.get("supplier_code"),
        args.get("supplier_name"),
    )
    lines = trade_lines(
        db, ctx.company_id, args.get("lines") or [], ps.default_debit_account
    )
    doc = ps.create(
        db,
        ctx,
        ps.PurchaseInput(
            kind="purchase",
            header=header_from(args, partner, args.get("supplier_name")),
            lines=lines,
        ),
    )
    return {
        "draft_id": doc.entry_id,
        "url": f"/purchases/{doc.entry_id}",
        "summary": (
            f"Chứng từ mua hàng nháp {doc.seller_name or ''}: "
            f"tổng {doc.total_amount:,} đ"
        ).replace(",", "."),
    }


@ai_tool(
    name="get_revenue_by_month",
    label="Doanh thu theo tháng",
    perm="sales.view",
    description=(
        "Net revenue (accounts 511, 515, 711; excluding closing entries) "
        "per month of a year, in VND."
    ),
    parameters={
        "type": "object",
        "properties": {"year": {"type": "integer"}},
        "required": ["year"],
    },
)
def get_revenue_by_month(db, ctx, year: int) -> dict:
    lr = ledger_source(db, ctx.company_id, include_opening=False)
    month = func.extract("month", lr.c.date)
    rows = db.execute(
        select(month, lr.c.account, func.sum(lr.c.credit - lr.c.debit))
        .where(
            account_match(lr.c.account, ["511", "515", "711"]),
            lr.c.doc_type != "KC",
            lr.c.date >= date(int(year), 1, 1),
            lr.c.date <= date(int(year), 12, 31),
        )
        .group_by(month, lr.c.account)
    ).all()
    months = {
        m: {
            "month": m,
            "sales_revenue": 0,
            "financial_income": 0,
            "other_income": 0,
        }
        for m in range(1, 13)
    }
    for m, acc, v in rows:
        key = (
            "sales_revenue"
            if acc.startswith("511")
            else "financial_income"
            if acc.startswith("515")
            else "other_income"
        )
        months[int(m)][key] += int(v or 0)
    data = list(months.values())
    return {
        "year": int(year),
        "months": data,
        "total_sales_revenue": sum(x["sales_revenue"] for x in data),
    }
