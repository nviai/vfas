"""Inventory models.

Warehouses, stock documents (receipt/issue/transfer), stock movements
and issue costing runs.

- A stock receipt (NK) is a valued engine document (Dr 15x / Cr
  counter account); `StockDoc.entry_id` points to the entry and the
  status follows the entry.
- Stock issues (XK) and transfers (DC) are stock documents with their
  own status; issue values are posted by costing (month-end GV entry).
- `StockMove` rows exist only while the source document is posted
  (created on posting, deleted on cancellation).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

RECEIPT = "receipt"
ISSUE = "issue"
TRANSFER = "transfer"
KIND_DOC_TYPE = {RECEIPT: "NK", ISSUE: "XK", TRANSFER: "DC"}
KIND_LABELS = {
    RECEIPT: "Phiếu nhập kho",
    ISSUE: "Phiếu xuất kho",
    TRANSFER: "Phiếu chuyển kho",
}

DRAFT, POSTED, CANCELLED = "draft", "posted", "cancelled"

# Stock movement sources
SRC_OPENING = "opening"
SRC_SALES = "sales"
SRC_PURCHASES = "purchases"
SRC_RECEIPT = "receipt"
SRC_ISSUE = "issue"
SRC_TRANSFER = "transfer"


class Warehouse(TenantMixin, Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(Text, default="")
    # Storekeeper name.
    keeper: Mapped[str] = mapped_column(String(200), default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")

    @property
    def label(self) -> str:
        return f"{self.code} – {self.name}"


class StockDoc(TenantMixin, Base):
    __tablename__ = "stock_docs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'posted', 'cancelled')", name="status"
        ),
        Index("ix_stock_docs_kind_date", "company_id", "kind", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))  # receipt | issue | transfer
    doc_type: Mapped[str] = mapped_column(String(8))  # NK | XK | DC
    number: Mapped[str | None] = mapped_column(String(40))
    number_seq: Mapped[int | None] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    doc_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(500), default="")
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    to_warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id")
    )
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    # Person delivering / receiving the goods.
    contact_name: Mapped[str] = mapped_column(String(200), default="")
    # NK: credit account (3381, 711, 154, 1388…);
    # XK: debit account (6422, 6421, 154, 632, 1381…).
    counter_account: Mapped[str | None] = mapped_column(String(20))
    ref_no: Mapped[str] = mapped_column(String(100), default="")
    attached_docs: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default=DRAFT)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="SET NULL"), index=True
    )
    posting_seq: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    posted_by: Mapped[int | None] = mapped_column(Integer)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[int | None] = mapped_column(Integer)
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[StockDocLine]] = relationship(
        back_populates="doc",
        cascade="all, delete-orphan",
        order_by="StockDocLine.line_no",
    )
    warehouse: Mapped[Warehouse] = relationship(foreign_keys=[warehouse_id])
    to_warehouse: Mapped[Warehouse | None] = relationship(
        foreign_keys=[to_warehouse_id]
    )
    partner: Mapped["Partner | None"] = relationship()  # noqa: F821

    @property
    def display_number(self) -> str:
        return self.number or f"Nháp #{self.id}"

    @property
    def total(self) -> int:
        return sum(int(ln.amount or 0) for ln in self.lines)

    @property
    def total_qty(self) -> Decimal:
        return sum(
            (ln.quantity or Decimal(0) for ln in self.lines), Decimal(0)
        )

    @property
    def url(self) -> str:
        if self.kind == RECEIPT and self.entry_id:
            return f"/inventory/receipts/{self.entry_id}"
        path = "issues" if self.kind == ISSUE else "transfers"
        return f"/inventory/{path}/{self.id}"


class StockDocLine(TenantMixin, Base):
    __tablename__ = "stock_doc_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[int] = mapped_column(
        ForeignKey("stock_docs.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(String(500), default="")
    unit: Mapped[str] = mapped_column(String(30), default="")
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Price, default=0)
    amount: Mapped[int] = mapped_column(Money, default=0)

    doc: Mapped[StockDoc] = relationship(back_populates="lines")
    item: Mapped["Item"] = relationship()  # noqa: F821


class StockMove(TenantMixin, Base):
    """One stock-in or stock-out line of a posted document."""

    __tablename__ = "stock_moves"
    __table_args__ = (
        CheckConstraint(
            "qty_in >= 0 AND qty_out >= 0", name="qty_non_negative"
        ),
        Index(
            "ix_stock_moves_key",
            "company_id",
            "item_id",
            "warehouse_id",
            "date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True
    )
    stock_doc_id: Mapped[int | None] = mapped_column(
        ForeignKey("stock_docs.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(16))
    source_line_id: Mapped[int | None] = mapped_column(Integer)
    doc_type: Mapped[str] = mapped_column(String(8), default="")
    number: Mapped[str] = mapped_column(String(40), default="")
    date: Mapped[date] = mapped_column(Date)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouses.id"))
    qty_in: Mapped[Decimal] = mapped_column(Qty, default=0)
    qty_out: Mapped[Decimal] = mapped_column(Qty, default=0)
    unit_cost: Mapped[Decimal | None] = mapped_column(Price)
    amount: Mapped[int] = mapped_column(Money, default=0)
    # True: value fixed at posting (purchases, stock receipts, purchase
    # returns, opening stock); False: value set by month-end average
    # costing (sales, stock issues, transfers, sales returns).
    valued: Mapped[bool] = mapped_column(Boolean, default=True)
    costed: Mapped[bool] = mapped_column(Boolean, default=False)
    stock_account: Mapped[str] = mapped_column(String(20), default="")
    # Counter account shown in the stock ledger; for costed lines it is
    # the account debited (issue) / credited (return) in the GV entry.
    contra_account: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(500), default="")

    item: Mapped["Item"] = relationship()  # noqa: F821
    warehouse: Mapped[Warehouse] = relationship()

    @property
    def is_in(self) -> bool:
        return (self.qty_in or 0) > 0


class CostingRun(TenantMixin, Base):
    """Issue costing run of one month (periodic weighted average)."""

    __tablename__ = "costing_runs"
    __table_args__ = (UniqueConstraint("company_id", "year", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    total_cost: Mapped[int] = mapped_column(Money, default=0)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    computed_by: Mapped[int | None] = mapped_column(Integer)
    computed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
