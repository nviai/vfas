"""Companies (tenants), memberships and invitations."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
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

from app.core.db import Base

DEFAULT_NUMBER_FORMAT = "{prefix}{year}-{seq:04d}"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    short_name: Mapped[str] = mapped_column(String(100), default="")
    tax_code: Mapped[str] = mapped_column(String(20), default="", index=True)
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    # Signatories on vouchers, ledgers and reports
    legal_rep: Mapped[str] = mapped_column(String(200), default="")
    legal_rep_title: Mapped[str] = mapped_column(
        String(100), default="Giám đốc"
    )
    chief_accountant: Mapped[str] = mapped_column(String(200), default="")
    preparer: Mapped[str] = mapped_column(String(200), default="")
    cashier: Mapped[str] = mapped_column(String(200), default="")
    storekeeper: Mapped[str] = mapped_column(String(200), default="")
    # Accounting regime and tax settings
    regime: Mapped[str] = mapped_column(String(16), default="tt133")
    start_date: Mapped[date] = mapped_column(
        Date
    )  # date bookkeeping starts in the software
    vat_method: Mapped[str] = mapped_column(
        String(16), default="khau_tru"
    )  # khau_tru | truc_tiep
    vat_period: Mapped[str] = mapped_column(
        String(8), default="quarter"
    )  # month | quarter
    tax_office: Mapped[str] = mapped_column(String(200), default="")
    province: Mapped[str] = mapped_column(String(100), default="")
    wage_region: Mapped[str] = mapped_column(
        String(4), default="I"
    )  # minimum wage region I..IV
    business_type: Mapped[str] = mapped_column(
        String(32), default="service"
    )  # service|trade|production
    number_format: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_NUMBER_FORMAT
    )
    # Other settings: voucher number prefixes, print options, default
    # language/theme...
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    plan: Mapped[str] = mapped_column(String(32), default="free")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(Integer)
    # Bumped on every posting/cancellation to serialize per company
    # (row lock).
    lock_version: Mapped[int] = mapped_column(Integer, default=0)

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        return self.short_name or self.name

    def setting(self, key: str, default=None):
        return (self.settings or {}).get(key, default)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "company_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship()  # noqa: F821


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    company: Mapped[Company] = relationship()
