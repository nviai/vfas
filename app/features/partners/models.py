"""Partners: customers, suppliers, employees and other parties."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin


class Partner(TenantMixin, Base):
    __tablename__ = "partners"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255))
    is_customer: Mapped[bool] = mapped_column(Boolean, default=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, default=False)
    is_employee: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_code: Mapped[str] = mapped_column(String(20), default="")
    # Citizen ID number ("CCCD"), for individuals
    id_number: Mapped[str] = mapped_column(String(20), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    contact_person: Mapped[str] = mapped_column(String(200), default="")
    bank_account: Mapped[str] = mapped_column(String(50), default="")
    bank_name: Mapped[str] = mapped_column(String(200), default="")
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    @property
    def label(self) -> str:
        return f"{self.code} – {self.name}"

    @property
    def roles(self) -> list[str]:
        out = []
        if self.is_customer:
            out.append("customer")
        if self.is_supplier:
            out.append("supplier")
        if self.is_employee:
            out.append("employee")
        return out
