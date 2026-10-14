"""Fixed assets, tools and prepaid expenses, and the monthly
depreciation / allocation runs.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
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

ASSET_CATEGORIES = {
    "building": "Nhà cửa, vật kiến trúc",
    "machine": "Máy móc, thiết bị",
    "vehicle": "Phương tiện vận tải, truyền dẫn",
    "office": "Thiết bị, dụng cụ quản lý",
    "intangible": "TSCĐ vô hình",
    "other": "TSCĐ khác",
}
# Cost account -> matching accumulated depreciation account
# Cost account -> accumulated depreciation account (TT133 details 211x,
# TT99 level-1 accounts 211/212/213).
DEPRECIATION_ACCOUNT = {
    "2111": "2141",
    "2112": "2142",
    "2113": "2143",
    "211": "2141",
    "212": "2142",
    "213": "2143",
}
FIXED_ASSET_PREFIXES = ("211", "212", "213")

IN_USE = "in_use"
DISPOSED = "disposed"
ASSET_STATUS = {IN_USE: "Đang sử dụng", DISPOSED: "Đã ghi giảm"}

PREPAID_KINDS = {"tool": "Công cụ dụng cụ", "expense": "Chi phí trả trước"}

RUN_DEPRECIATION = "depreciation"
RUN_ALLOCATION = "allocation"


class FixedAsset(TenantMixin, Base):
    __tablename__ = "fixed_assets"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(20), default="office")
    asset_account: Mapped[str] = mapped_column(String(20), default="2111")
    depreciation_account: Mapped[str] = mapped_column(
        String(20), default="2141"
    )
    expense_account: Mapped[str] = mapped_column(String(20), default="6422")
    department: Mapped[str] = mapped_column(String(200), default="")
    # country / manufacturer
    manufacturer: Mapped[str] = mapped_column(String(200), default="")
    serial_no: Mapped[str] = mapped_column(String(100), default="")
    year_made: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[int] = mapped_column(Money)  # historical cost
    # For assets brought in as opening balances: accumulated
    # depreciation and number of months depreciated before.
    opening_accumulated: Mapped[int] = mapped_column(Money, default=0)
    opening_months_used: Mapped[int] = mapped_column(Integer, default=0)
    useful_life_months: Mapped[int] = mapped_column(Integer)
    # date of acquisition / placed in service
    acquired_date: Mapped[date] = mapped_column(Date)
    # first day of the month depreciation starts
    depreciation_start: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default=IN_USE)
    # purchase | opening | manual
    source: Mapped[str] = mapped_column(String(12), default="manual")
    # entry that recorded the acquisition
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id")
    )
    # supplier
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    disposal_date: Mapped[date | None] = mapped_column(Date)
    disposal_reason: Mapped[str | None] = mapped_column(Text)
    disposal_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id")
    )
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    @property
    def label(self) -> str:
        return f"{self.code} – {self.name}"

    @property
    def category_label(self) -> str:
        return ASSET_CATEGORIES.get(self.category, self.category)

    @property
    def status_label(self) -> str:
        return ASSET_STATUS.get(self.status, self.status)


class PrepaidItem(TenantMixin, Base):
    """Tool or prepaid expense allocated over time through TK 242."""

    __tablename__ = "prepaid_items"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(12), default="tool")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    amount: Mapped[int] = mapped_column(Money)  # total amount to allocate
    months: Mapped[int] = mapped_column(Integer)  # total allocation months
    opening_allocated: Mapped[int] = mapped_column(Money, default=0)
    opening_months_used: Mapped[int] = mapped_column(Integer, default=0)
    # first day of the month allocation starts
    start_month: Mapped[date] = mapped_column(Date)
    prepaid_account: Mapped[str] = mapped_column(String(20), default="242")
    expense_account: Mapped[str] = mapped_column(String(20), default="6422")
    department: Mapped[str] = mapped_column(String(200), default="")
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    stopped_date: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    @property
    def label(self) -> str:
        return f"{self.code} – {self.name}"

    @property
    def kind_label(self) -> str:
        return PREPAID_KINDS.get(self.kind, self.kind)


class AssetRun(TenantMixin, Base):
    """One depreciation (KH) or allocation (PB) run for a month.

    Points to the latest journal entry.
    """

    __tablename__ = "asset_runs"
    __table_args__ = (UniqueConstraint("company_id", "kind", "year", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # depreciation | allocation
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id")
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_by: Mapped[int | None] = mapped_column(Integer)

    lines: Mapped[list[AssetRunLine]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AssetRunLine.id",
    )
    entry: Mapped["JournalEntry | None"] = relationship()  # noqa: F821 - resolved by SQLAlchemy


class AssetRunLine(TenantMixin, Base):
    __tablename__ = "asset_run_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("asset_runs.id", ondelete="CASCADE"), index=True
    )
    ref_kind: Mapped[str] = mapped_column(String(10))  # asset | prepaid
    ref_id: Mapped[int] = mapped_column(Integer, index=True)
    amount: Mapped[int] = mapped_column(Money)
    debit_account: Mapped[str] = mapped_column(String(20))
    credit_account: Mapped[str] = mapped_column(String(20))

    run: Mapped[AssetRun] = relationship(back_populates="lines")
