"""Purchase documents for goods/services (MH) and purchase returns (TM).

The inventory feature reads `PurchaseLine` (item_id, quantity, amount,
warehouse_id); the fixed assets feature reads lines whose
debit_account is 211x.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
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

PURCHASE = "purchase"
RETURN = "return"
DOC_TYPE = {PURCHASE: "MH", RETURN: "TM"}


class PurchaseDoc(TenantMixin, Base):
    __tablename__ = "purchase_docs"
    __table_args__ = (
        UniqueConstraint("company_id", "einvoice_key"),
        Index("ix_purchase_docs_entry", "entry_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(
        String(8), default=PURCHASE
    )  # purchase | return
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    seller_name: Mapped[str] = mapped_column(String(255), default="")
    seller_address: Mapped[str] = mapped_column(String(300), default="")
    seller_tax_code: Mapped[str] = mapped_column(String(20), default="")
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
    goods_amount: Mapped[int] = mapped_column(Money, default=0)
    vat_amount: Mapped[int] = mapped_column(Money, default=0)
    total_amount: Mapped[int] = mapped_column(Money, default=0)
    # TM: the original purchase document.
    original_entry_id: Mapped[int | None] = mapped_column(Integer)
    einvoice_key: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    lines: Mapped[list[PurchaseLine]] = relationship(
        back_populates="doc",
        cascade="all, delete-orphan",
        order_by="PurchaseLine.line_no",
    )
    entry: Mapped["JournalEntry | None"] = relationship()  # noqa: F821
    partner: Mapped["Partner | None"] = relationship()  # noqa: F821

    @property
    def doc_type(self) -> str:
        return DOC_TYPE.get(self.kind, "MH")


class PurchaseLine(TenantMixin, Base):
    __tablename__ = "purchase_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_docs.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(String(500), default="")
    unit: Mapped[str] = mapped_column(String(30), default="")
    quantity: Mapped[Decimal | None] = mapped_column(Qty)
    unit_price: Mapped[Decimal | None] = mapped_column(Price)
    # Amount before VAT (inventory receipt cost).
    amount: Mapped[int] = mapped_column(Money, default=0)
    vat_rate: Mapped[str] = mapped_column(String(8), default="10")
    vat_amount: Mapped[int] = mapped_column(Money, default=0)
    deductible: Mapped[bool] = mapped_column(Boolean, default=True)
    debit_account: Mapped[str] = mapped_column(String(20), default="156")
    warehouse_id: Mapped[int | None] = mapped_column(Integer)

    doc: Mapped[PurchaseDoc] = relationship(back_populates="lines")
    item: Mapped["Item | None"] = relationship()  # noqa: F821
