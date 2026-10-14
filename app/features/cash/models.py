"""Cash counts (cash count sheet, form 08a-TT)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, Money, TenantMixin

# Vietnamese banknote denominations in circulation, from 500,000
# down to 1,000.
DENOMINATIONS = (
    500_000,
    200_000,
    100_000,
    50_000,
    20_000,
    10_000,
    5_000,
    2_000,
    1_000,
)


class CashCount(TenantMixin, Base):
    __tablename__ = "cash_counts"
    __table_args__ = (UniqueConstraint("company_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(40), default="")
    count_date: Mapped[date] = mapped_column(Date, index=True)
    count_time: Mapped[str] = mapped_column(String(5), default="")  # HH:MM
    # Book balance of TK 1111 at the time of the count (all posted
    # vouchers with a posting date <= count_date).
    book_balance: Mapped[int] = mapped_column(Money)
    actual_total: Mapped[int] = mapped_column(Money)
    accountant_name: Mapped[str] = mapped_column(String(120), default="")
    cashier_name: Mapped[str] = mapped_column(String(120), default="")
    other_member_name: Mapped[str] = mapped_column(String(120), default="")
    other_member_role: Mapped[str] = mapped_column(String(120), default="")
    responsible_name: Mapped[str] = mapped_column(String(120), default="")
    difference_reason: Mapped[str] = mapped_column(Text, default="")
    conclusion: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[CashCountLine]] = relationship(
        back_populates="cash_count",
        order_by="CashCountLine.denomination.desc()",
        cascade="all, delete-orphan",
    )

    @property
    def difference(self) -> int:
        """Return actual minus book (> 0 surplus, < 0 shortage)."""
        return self.actual_total - self.book_balance


class CashCountLine(TenantMixin, Base):
    __tablename__ = "cash_count_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cash_count_id: Mapped[int] = mapped_column(
        ForeignKey("cash_counts.id", ondelete="CASCADE"), index=True
    )
    denomination: Mapped[int] = mapped_column(Money)
    quantity: Mapped[int] = mapped_column(Integer, default=0)

    cash_count: Mapped[CashCount] = relationship(back_populates="lines")

    @property
    def amount(self) -> int:
        return self.denomination * self.quantity
