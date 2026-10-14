"""Documents and double-entry journal lines.

The single source of figures for every book and report.

Each journal line is one debit/credit PAIR (debit account, credit
account, amount), the way Vietnamese accountants write entries
("Nợ 642 / Có 1111: 500.000"). This gives every book an exact
contra account and keeps every line balanced.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
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

from app.core.db import Base, Money, Qty, TenantMixin

DRAFT = "draft"
POSTED = "posted"
CANCELLED = "cancelled"
STATUSES = (DRAFT, POSTED, CANCELLED)
STATUS_LABELS = {DRAFT: "Nháp", POSTED: "Đã ghi sổ", CANCELLED: "Đã hủy"}

# Fields that must not change once a document is posted (a DB trigger
# enforces this as well).
LOCKED_FIELDS = (
    "company_id",
    "doc_type",
    "number",
    "number_seq",
    "date",
    "doc_date",
    "posting_seq",
)
AUDIT_FIELDS = (
    "doc_type",
    "number",
    "date",
    "doc_date",
    "status",
    "description",
    "partner_id",
    "contact_name",
    "contact_address",
    "ref_no",
    "attached_docs",
    "total",
    "cancel_reason",
    "source",
)


class JournalEntry(TenantMixin, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("company_id", "number"),
        CheckConstraint(
            "status IN ('draft', 'posted', 'cancelled')", name="status"
        ),
        Index(
            "ix_journal_entries_book",
            "company_id",
            "status",
            "date",
            "posting_seq",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(8), index=True)
    number: Mapped[str | None] = mapped_column(String(40))  # set on posting
    number_seq: Mapped[int | None] = mapped_column(Integer)
    # Posting (accounting) date.
    date: Mapped[date] = mapped_column(Date, index=True)
    doc_date: Mapped[date | None] = mapped_column(Date)  # document date
    description: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(12), default=DRAFT)
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id"), index=True
    )
    # Payer/payee or buyer/seller name.
    contact_name: Mapped[str] = mapped_column(String(200), default="")
    contact_address: Mapped[str] = mapped_column(String(300), default="")
    # Invoice number or other reference number.
    ref_no: Mapped[str] = mapped_column(String(100), default="")
    # Number of source documents ("Kèm theo … chứng từ gốc").
    attached_docs: Mapped[int] = mapped_column(Integer, default=0)
    # Sum of the line amounts (for fast listing).
    total: Mapped[int] = mapped_column(Money, default=0)
    # manual|cash|bank|sales|...|import|ai
    source: Mapped[str] = mapped_column(String(20), default="manual")
    source_id: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    posting_seq: Mapped[int | None] = mapped_column(Integer)
    reverses_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id")
    )
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

    lines: Mapped[list[JournalLine]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="JournalLine.line_no",
    )
    vat_records: Mapped[list[VatRecord]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="VatRecord.id",
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="Attachment.id",
    )
    partner: Mapped["Partner | None"] = relationship()  # noqa: F821

    @property
    def is_draft(self) -> bool:
        return self.status == DRAFT

    @property
    def is_posted(self) -> bool:
        return self.status == POSTED

    @property
    def is_cancelled(self) -> bool:
        return self.status == CANCELLED

    @property
    def display_number(self) -> str:
        return self.number or f"Nháp #{self.id}"

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def url(self) -> str:
        from app.core.registry import doc_url

        return doc_url(self.doc_type, self.id)

    @property
    def doc_type_label(self) -> str:
        from app.core.registry import doc_label

        return doc_label(self.doc_type)


class JournalLine(TenantMixin, Base):
    """One debit/credit pair.

    Detail references (partner, bank account) are kept per side.
    """

    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_journal_lines_debit", "company_id", "debit_account"),
        Index("ix_journal_lines_credit", "company_id", "credit_account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(String(500), default="")
    debit_account: Mapped[str] = mapped_column(String(20))
    credit_account: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(Money)
    debit_partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id")
    )
    credit_partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id")
    )
    debit_bank_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_accounts.id")
    )
    credit_bank_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_accounts.id")
    )
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    quantity: Mapped[Decimal | None] = mapped_column(Qty)
    # Fixed asset / tool reference (assets module).
    asset_id: Mapped[int | None] = mapped_column(Integer)
    # Cash flow statement (B03-DNN) line code overriding the default
    # for lines that touch a cash account.
    cash_flow: Mapped[str | None] = mapped_column(String(8))

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")


class OpeningBalance(TenantMixin, Base):
    """Opening balance at the accounting start date.

    Recorded on the day before start_date.
    """

    __tablename__ = "opening_balances"
    __table_args__ = (
        CheckConstraint("debit >= 0 AND credit >= 0", name="non_negative"),
        Index("ix_opening_balances_account", "company_id", "account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account: Mapped[str] = mapped_column(String(20))
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    bank_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_accounts.id")
    )
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    quantity: Mapped[Decimal | None] = mapped_column(Qty)
    debit: Mapped[int] = mapped_column(Money, default=0)
    credit: Mapped[int] = mapped_column(Money, default=0)
    note: Mapped[str] = mapped_column(String(300), default="")


class NumberSequence(TenantMixin, Base):
    __tablename__ = "number_sequences"
    __table_args__ = (UniqueConstraint("company_id", "series", "year"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series: Mapped[str] = mapped_column(String(16))
    year: Mapped[int] = mapped_column(Integer)  # 0 = no yearly reset
    last_seq: Mapped[int] = mapped_column(Integer, default=0)


class PeriodLock(TenantMixin, Base):
    """Lock of a month (month 1..12) or a whole year (month = 0)."""

    __tablename__ = "period_locks"
    __table_args__ = (UniqueConstraint("company_id", "year", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=True)
    locked_by: Mapped[int | None] = mapped_column(Integer)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlocked_by: Mapped[int | None] = mapped_column(Integer)
    unlocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    unlock_reason: Mapped[str | None] = mapped_column(Text)


class VatRecord(TenantMixin, Base):
    """One VAT invoice line attached to a document (input or output).

    Source of the VAT invoice listings and the VAT return.
    """

    __tablename__ = "vat_records"
    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out')", name="direction"),
        Index(
            "ix_vat_records_period", "company_id", "direction", "invoice_date"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True
    )
    # in = purchases, out = sales
    direction: Mapped[str] = mapped_column(String(4))
    # Invoice template number / template symbol.
    invoice_template: Mapped[str] = mapped_column(String(20), default="")
    # Invoice series symbol.
    invoice_series: Mapped[str] = mapped_column(String(20), default="")
    invoice_no: Mapped[str] = mapped_column(String(30), default="")
    invoice_date: Mapped[date | None] = mapped_column(Date)
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    partner_name: Mapped[str] = mapped_column(String(255), default="")
    partner_tax_code: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(String(500), default="")
    # Amount before VAT.
    goods_amount: Mapped[int] = mapped_column(Money, default=0)
    vat_rate: Mapped[str] = mapped_column(String(8), default="10")
    vat_amount: Mapped[int] = mapped_column(Money, default=0)
    # 1331/1332/33311
    vat_account: Mapped[str] = mapped_column(String(20), default="")
    deductible: Mapped[bool] = mapped_column(Boolean, default=True)
    # Group on the VAT return:
    # normal | import | investment | adjust_up | adjust_down
    group: Mapped[str] = mapped_column(String(16), default="normal")

    entry: Mapped[JournalEntry] = relationship(back_populates="vat_records")


class Attachment(TenantMixin, Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    stored_name: Mapped[str] = mapped_column(String(100), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[int | None] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    entry: Mapped[JournalEntry] = relationship(back_populates="attachments")
