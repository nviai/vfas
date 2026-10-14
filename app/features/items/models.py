"""Catalog of materials, goods and services.

The UI lives in the sales and purchases modules.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, Price, TenantMixin

ITEM_KINDS = {
    "goods": "Hàng hóa",
    "material": "Nguyên vật liệu",
    "tool": "Công cụ dụng cụ",
    "product": "Thành phẩm",
    "service": "Dịch vụ",
}
STOCK_ACCOUNT = {
    "goods": "156",
    "material": "152",
    "tool": "153",
    "product": "155",
}
REVENUE_ACCOUNT = {
    "goods": "5111",
    "material": "5118",
    "tool": "5118",
    "product": "5112",
    "service": "5113",
}
# VAT rates: a percentage or a special code.
VAT_RATES = {
    "0": "0%",
    "5": "5%",
    "8": "8%",
    "10": "10%",
    "KCT": "Không chịu thuế",
    "KKKNT": "Không kê khai, tính nộp",
    "KHAC": "Khác",
}


class Item(TenantMixin, Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(30), default="")
    kind: Mapped[str] = mapped_column(String(16), default="goods")
    stock_account: Mapped[str | None] = mapped_column(String(20))
    revenue_account: Mapped[str | None] = mapped_column(String(20))
    cogs_account: Mapped[str | None] = mapped_column(String(20), default="632")
    # Expense account for purchased services.
    expense_account: Mapped[str | None] = mapped_column(String(20))
    vat_rate: Mapped[str] = mapped_column(String(8), default="10")
    sale_price: Mapped[Decimal] = mapped_column(Price, default=0)
    purchase_price: Mapped[Decimal] = mapped_column(Price, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def tracks_inventory(self) -> bool:
        return self.kind in STOCK_ACCOUNT

    @property
    def label(self) -> str:
        return f"{self.code} – {self.name}"
