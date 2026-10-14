"""Helpers that create sales/purchase vouchers in tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.features.sales.common import TradeHeader, TradeLine, vat_of


def header(
    d: date,
    *,
    partner_id=None,
    partner_name="",
    method="credit",
    invoice_no="",
    series="C26TAA",
    tax_code="",
    bank_account_id=None,
    due=None,
    original_entry_id=None,
) -> TradeHeader:
    return TradeHeader(
        date=d,
        doc_date=d,
        partner_id=partner_id,
        partner_name=partner_name,
        address="",
        tax_code=tax_code,
        contact_name="",
        invoice_template="1",
        invoice_series=series if invoice_no else "",
        invoice_no=invoice_no,
        invoice_date=d,
        payment_method=method,
        bank_account_id=bank_account_id,
        due_date=due,
        description="",
        note="",
        original_entry_id=original_entry_id,
    )


def line(
    amount: int,
    rate: str = "10",
    account: str = "5113",
    *,
    desc: str = "Dịch vụ",
    item_id=None,
    qty=None,
    price=None,
    discount: int = 0,
    deductible: bool = True,
    vat: int | None = None,
) -> TradeLine:
    return TradeLine(
        description=desc,
        amount=amount,
        vat_rate=rate,
        vat_amount=vat if vat is not None else vat_of(amount - discount, rate),
        item_id=item_id,
        quantity=Decimal(qty) if qty is not None else None,
        unit_price=Decimal(price) if price is not None else None,
        discount_amount=discount,
        account=account,
        deductible=deductible,
    )


def sell(db, ctx, d: date, lines, *, post=True, kind="sale", **kw):
    from app.features.sales import service as ss

    return ss.create(
        db,
        ctx,
        ss.SalesInput(kind=kind, header=header(d, **kw), lines=lines),
        post=post,
    )


def buy(db, ctx, d: date, lines, *, post=True, kind="purchase", **kw):
    from app.features.purchases import service as ps

    return ps.create(
        db,
        ctx,
        ps.PurchaseInput(kind=kind, header=header(d, **kw), lines=lines),
        post=post,
    )
