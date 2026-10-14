"""Per-company chart of accounts.

Seeded from the accounting regime; detail (child) accounts can be
added.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin


class Account(TenantMixin, Base):
    """An account; used accounts are never deleted, only hidden.

    Hide an account by setting ``is_active=False``.
    """

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(255))
    name_en: Mapped[str] = mapped_column(String(255), default="")
    parent_code: Mapped[str | None] = mapped_column(String(20))
    level: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(
        String(16)
    )  # asset|liability|equity|revenue|expense|result
    normal: Mapped[str] = mapped_column(String(8))  # debit|credit|both|none
    is_postable: Mapped[bool] = mapped_column(Boolean, default=True)
    partner_mode: Mapped[str] = mapped_column(
        String(10), default="none"
    )  # none|optional|required
    needs_bank: Mapped[bool] = mapped_column(Boolean, default=False)
    tracks_item: Mapped[bool] = mapped_column(Boolean, default=False)
    tracks_asset: Mapped[bool] = mapped_column(Boolean, default=False)
    no_negative: Mapped[bool] = mapped_column(Boolean, default=False)
    # Part of the accounting regime (not added by the user).
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def label(self) -> str:
        return f"{self.code} – {self.name}"
