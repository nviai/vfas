"""Prepared tax returns (01/GTGT, 03/TNDN).

Figures are stored so that [43] of one period can be carried forward
to [22] of the next.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin

FORM_VAT = "01/GTGT"
FORM_CIT = "03/TNDN"


class TaxDeclaration(TenantMixin, Base):
    __tablename__ = "tax_declarations"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "form", "year", "period_type", "period", "version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    form: Mapped[str] = mapped_column(String(16))
    # month | quarter | year
    period_type: Mapped[str] = mapped_column(String(8))
    year: Mapped[int] = mapped_column(Integer)
    # month 1..12 / quarter 1..4 / 0 (year)
    period: Mapped[int] = mapped_column(Integer)
    # 0 = original return, n = n-th supplementary return
    version: Mapped[int] = mapped_column(Integer, default=0)
    # {"22": 0, "23": ..., "manual": {...}}
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    # draft | final | submitted
    status: Mapped[str] = mapped_column(String(12), default="draft")
    created_by: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finalized_by: Mapped[int | None] = mapped_column(Integer)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
