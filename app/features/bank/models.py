"""Company bank accounts (sub-ledgers of TK 1121/1122)."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin


class BankAccount(TenantMixin, Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (UniqueConstraint("company_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_code: Mapped[str] = mapped_column(String(20), default="1121")
    number: Mapped[str] = mapped_column(String(50))
    bank_name: Mapped[str] = mapped_column(String(200))
    branch: Mapped[str] = mapped_column(String(200), default="")
    # e.g. "VCB", "TCB"
    short_name: Mapped[str] = mapped_column(String(50), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def label(self) -> str:
        return f"{self.short_name or self.bank_name} – {self.number}"
