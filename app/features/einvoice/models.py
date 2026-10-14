"""E-invoice issuing: per-company provider settings and the outbox.

Every attempt to issue an invoice is written to `einvoice_issues`
BEFORE the provider is called, with an idempotency key that the
provider uses to recognize a repeated request. A lost answer therefore
never leads to two invoices: the row stays "unknown" until the invoice
is looked up by its key.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
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

from app.core.db import Base, Money, TenantMixin


class EInvoiceConfig(TenantMixin, Base):
    __tablename__ = "einvoice_configs"
    __table_args__ = (UniqueConstraint("company_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), default="")
    # test | production
    environment: Mapped[str] = mapped_column(String(12), default="test")
    # Empty = the provider's default address for the environment.
    base_url: Mapped[str] = mapped_column(String(300), default="")
    # Non-secret provider fields (user name, account...).
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    # Secret fields as JSON, encrypted with core.crypto.
    secrets: Mapped[str | None] = mapped_column(Text)
    # Invoice template number (1 = VAT invoice) and series (C26TAA).
    template: Mapped[str] = mapped_column(String(10), default="1")
    series: Mapped[str] = mapped_column(String(10), default="")
    # Post the sales voucher right after the invoice is issued.
    auto_post: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_by: Mapped[int | None] = mapped_column(Integer)


class EInvoiceIssue(TenantMixin, Base):
    __tablename__ = "einvoice_issues"
    __table_args__ = (
        UniqueConstraint("company_id", "provider", "idem_key"),
        Index("ix_einvoice_issues_doc", "company_id", "sales_doc_id"),
        Index("ix_einvoice_issues_status", "status", "next_check_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_doc_id: Mapped[int] = mapped_column(
        ForeignKey("sales_docs.id", ondelete="CASCADE")
    )
    entry_id: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(20))
    environment: Mapped[str] = mapped_column(String(12), default="test")
    # original | adjust_down | adjust_up
    kind: Mapped[str] = mapped_column(String(12), default="original")
    original_issue_id: Mapped[int | None] = mapped_column(Integer)
    idem_key: Mapped[str] = mapped_column(String(64))
    # pending | unknown | signed | issued | failed
    status: Mapped[str] = mapped_column(String(12), default="pending")
    template: Mapped[str] = mapped_column(String(10), default="")
    series: Mapped[str] = mapped_column(String(10), default="")
    number: Mapped[str] = mapped_column(String(20), default="")
    issue_date: Mapped[date | None] = mapped_column(Date)
    # Tax authority code ("mã của cơ quan thuế").
    tax_authority_code: Mapped[str] = mapped_column(String(64), default="")
    # Code the buyer uses on the provider's lookup page.
    lookup_code: Mapped[str] = mapped_column(String(64), default="")
    # Provider transaction / invoice id.
    provider_ref: Mapped[str] = mapped_column(String(120), default="")
    total_amount: Mapped[int] = mapped_column(Money, default=0)
    vat_amount: Mapped[int] = mapped_column(Money, default=0)
    # The canonical invoice sent (without secrets) and the last answer.
    request: Mapped[dict] = mapped_column(JSON, default=dict)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Signed XML kept as the legal original (path under the
    # attachments directory) and its SHA-256.
    xml_path: Mapped[str] = mapped_column(String(300), default="")
    xml_sha256: Mapped[str] = mapped_column(String(64), default="")
    next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    doc: Mapped["SalesDoc"] = relationship()  # noqa: F821

    @property
    def display_number(self) -> str:
        if not self.number:
            return ""
        return f"{self.template}{self.series}-{self.number}"
