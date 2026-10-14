"""Bank statement models.

Statements, statement lines (matched against vouchers), column
mapping profiles and posting suggestion rules.
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

UNMATCHED = "unmatched"
MATCHED = "matched"
IGNORED = "ignored"


class BankStatement(TenantMixin, Base):
    __tablename__ = "bank_statements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255), default="")
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    # closing balance per the statement (if the file has one)
    closing_balance: Mapped[int | None] = mapped_column(Money)
    line_count: Mapped[int] = mapped_column(Integer, default=0)
    imported_by: Mapped[int | None] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[BankStatementLine]] = relationship(
        back_populates="statement",
        cascade="all, delete-orphan",
        order_by="BankStatementLine.line_no",
    )


class BankStatementLine(TenantMixin, Base):
    """One transaction on a bank statement.

    ``money_in`` is a credit to the bank account (money in),
    ``money_out`` a debit (money out).
    """

    __tablename__ = "bank_statement_lines"
    __table_args__ = (
        Index(
            "ix_bank_statement_lines_match",
            "company_id",
            "bank_account_id",
            "status",
            "date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("bank_statements.id", ondelete="CASCADE"), index=True
    )
    bank_account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id")
    )
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text, default="")
    reference: Mapped[str] = mapped_column(String(100), default="")
    money_in: Mapped[int] = mapped_column(Money, default=0)
    money_out: Mapped[int] = mapped_column(Money, default=0)
    balance: Mapped[int | None] = mapped_column(Money)
    # guards against importing the same line twice
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(12), default=UNMATCHED)
    matched_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_lines.id", ondelete="SET NULL")
    )
    matched_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="SET NULL")
    )
    matched_by: Mapped[int | None] = mapped_column(Integer)
    matched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    note: Mapped[str] = mapped_column(String(300), default="")

    statement: Mapped[BankStatement] = relationship(back_populates="lines")

    @property
    def amount(self) -> int:
        return self.money_in or self.money_out

    @property
    def incoming(self) -> bool:
        return self.money_in > 0


class BankImportProfile(TenantMixin, Base):
    """Column mapping of a statement file.

    Stored per bank account so it can be reused on the next import.
    """

    __tablename__ = "bank_import_profiles"
    __table_args__ = (UniqueConstraint("company_id", "bank_account_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bank_account_id: Mapped[int] = mapped_column(
        ForeignKey("bank_accounts.id")
    )
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class BankRule(TenantMixin, Base):
    """Posting suggestion rule for statement lines.

    A description containing the keyword maps to an offsetting
    account (and optionally a partner).
    """

    __tablename__ = "bank_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    keyword: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(
        String(4), default="any"
    )  # in | out | any
    account: Mapped[str] = mapped_column(String(20))
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"))
    description: Mapped[str] = mapped_column(String(300), default="")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
