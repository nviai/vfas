"""Sales documents (BH) and sales returns / allowances (TL).

Document status comes from JournalEntry (entry_id). The inventory
feature reads `SalesLine` (item_id, quantity, warehouse_id...) when
entry.source == "sales" and entry.source_id == SalesDoc.id.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, Money, Price, Qty, TenantMixin

SALE = "sale"
RETURN = "return"
DOC_TYPE = {SALE: "BH", RETURN: "TL"}
PAYMENT_METHODS = {
    "credit": "Ghi nợ",
    "cash": "Tiền mặt",
    "bank": "Chuyển khoản",
}


class SalesDoc(TenantMixin, Base):
    __tablename__ = "sales_docs"
    __table_args__ = (
        UniqueConstraint("company_id", "einvoice_key"),
        Index("ix_sales_docs_entry", "entry_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(8), default=SALE)  # sale | return
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    # Buyer organization name.
    partner_name: Mapped[str] = mapped_column(String(255), default="")
    # Full name of the individual buyer.
    buyer_name: Mapped[str] = mapped_column(String(200), default="")
    buyer_address: Mapped[str] = mapped_column(String(300), default="")
    buyer_tax_code: Mapped[str] = mapped_column(String(20), default="")
    invoice_template: Mapped[str] = mapped_column(String(20), default="")
    invoice_series: Mapped[str] = mapped_column(String(20), default="")
    invoice_no: Mapped[str] = mapped_column(String(30), default="")
    invoice_date: Mapped[date | None] = mapped_column(Date)
    payment_method: Mapped[str] = mapped_column(
        String(8), default="credit"
    )  # credit | cash | bank
    bank_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_accounts.id")
    )
    due_date: Mapped[date | None] = mapped_column(Date)
    # Total goods amount before discount.
    goods_amount: Mapped[int] = mapped_column(Money, default=0)
    discount_amount: Mapped[int] = mapped_column(Money, default=0)
    vat_amount: Mapped[int] = mapped_column(Money, default=0)
    # Total amount payable.
    total_amount: Mapped[int] = mapped_column(Money, default=0)
    # TL: the original sales document.
    original_entry_id: Mapped[int | None] = mapped_column(Integer)
    # Seller tax code|series|number (duplicate guard).
    einvoice_key: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    lines: Mapped[list[SalesLine]] = relationship(
        back_populates="doc",
        cascade="all, delete-orphan",
        order_by="SalesLine.line_no",
    )
    entry: Mapped["JournalEntry | None"] = relationship()  # noqa: F821
    partner: Mapped["Partner | None"] = relationship()  # noqa: F821

    @property
    def doc_type(self) -> str:
        return DOC_TYPE.get(self.kind, "BH")

    @property
    def net_amount(self) -> int:
        return self.goods_amount - self.discount_amount


class SalesLine(TenantMixin, Base):
    __tablename__ = "sales_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(
        ForeignKey("sales_docs.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(String(500), default="")
    unit: Mapped[str] = mapped_column(String(30), default="")
    quantity: Mapped[Decimal | None] = mapped_column(Qty)
    unit_price: Mapped[Decimal | None] = mapped_column(Price)
    # Line amount AFTER discount (revenue).
    amount: Mapped[int] = mapped_column(Money, default=0)
    discount_amount: Mapped[int] = mapped_column(Money, default=0)
    vat_rate: Mapped[str] = mapped_column(String(8), default="10")
    vat_amount: Mapped[int] = mapped_column(Money, default=0)
    revenue_account: Mapped[str] = mapped_column(String(20), default="5111")
    warehouse_id: Mapped[int | None] = mapped_column(Integer)

    doc: Mapped[SalesDoc] = relationship(back_populates="lines")
    item: Mapped["Item | None"] = relationship()  # noqa: F821

    @property
    def gross_amount(self) -> int:
        return self.amount + self.discount_amount
