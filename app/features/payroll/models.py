"""Payroll models.

Employees, effective-dated payroll and tax parameters, and monthly
payroll runs.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, Money, TenantMixin

if TYPE_CHECKING:
    from app.features.journal.models import JournalEntry

CONTRACT_TYPES = {
    "long": "HĐLĐ từ 3 tháng trở lên",
    "short": "HĐLĐ dưới 3 tháng / thời vụ",
    "probation": "Thử việc",
    "none": "Không ký HĐLĐ (cộng tác viên)",
}


class Employee(TenantMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("company_id", "partner_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partners.id"), index=True
    )
    department: Mapped[str] = mapped_column(String(200), default="")
    position: Mapped[str] = mapped_column(String(200), default="")
    contract_type: Mapped[str] = mapped_column(String(12), default="long")
    is_resident: Mapped[bool] = mapped_column(Boolean, default=True)
    dependents: Mapped[int] = mapped_column(Integer, default=0)
    # Contract salary per month.
    base_salary: Mapped[int] = mapped_column(Money, default=0)
    # 0 = not enrolled in compulsory insurance.
    insurance_salary: Mapped[int] = mapped_column(Money, default=0)
    # Taxable allowance per month.
    taxable_allowance: Mapped[int] = mapped_column(Money, default=0)
    # Non-taxable allowance per month.
    nontaxable_allowance: Mapped[int] = mapped_column(Money, default=0)
    expense_account: Mapped[str] = mapped_column(String(20), default="6422")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    partner: Mapped["Partner"] = relationship()  # noqa: F821 - resolved by SQLAlchemy

    @property
    def code(self) -> str:
        return self.partner.code if self.partner else ""

    @property
    def full_name(self) -> str:
        return self.partner.name if self.partner else ""

    @property
    def contract_label(self) -> str:
        return CONTRACT_TYPES.get(self.contract_type, self.contract_type)


class PayrollParam(TenantMixin, Base):
    """A payroll/tax parameter effective from `effective_from`.

    The value is JSON whose shape depends on `key`.
    """

    __tablename__ = "payroll_params"
    __table_args__ = (UniqueConstraint("company_id", "key", "effective_from"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(40))
    effective_from: Mapped[date] = mapped_column(Date)
    value: Mapped[dict] = mapped_column(JSON)
    # Legal basis.
    source: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class PayrollRun(TenantMixin, Base):
    __tablename__ = "payroll_runs"
    __table_args__ = (UniqueConstraint("company_id", "year", "month"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    standard_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=26)
    entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id")
    )
    payment_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id")
    )
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_by: Mapped[int | None] = mapped_column(Integer)

    lines: Mapped[list[PayrollLine]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="PayrollLine.id",
    )
    entry: Mapped[JournalEntry | None] = relationship(foreign_keys=[entry_id])
    payment_entry: Mapped[JournalEntry | None] = relationship(
        foreign_keys=[payment_entry_id]
    )

    @property
    def period_label(self) -> str:
        return f"{self.month:02d}/{self.year}"

    @property
    def status(self) -> str:
        """Run status derived from its journal entry.

        "draft" when there is no entry or the entry was cancelled;
        otherwise the entry's status ("posted" once posted).
        """
        if self.entry is None or self.entry.status == "cancelled":
            return "draft"
        return self.entry.status

    @property
    def editable(self) -> bool:
        return self.entry is None or self.entry.status in (
            "draft",
            "cancelled",
        )


class PayrollLine(TenantMixin, Base):
    __tablename__ = "payroll_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("payroll_runs.id", ondelete="CASCADE"), index=True
    )
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id"), index=True
    )
    # Snapshot of the employee profile when the run was prepared.
    contract_type: Mapped[str] = mapped_column(String(12), default="long")
    is_resident: Mapped[bool] = mapped_column(Boolean, default=True)
    dependents: Mapped[int] = mapped_column(Integer, default=0)
    expense_account: Mapped[str] = mapped_column(String(20), default="6422")
    base_salary: Mapped[int] = mapped_column(Money, default=0)
    days_worked: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=0)
    salary_by_days: Mapped[int] = mapped_column(Money, default=0)
    taxable_allowance: Mapped[int] = mapped_column(Money, default=0)
    nontaxable_allowance: Mapped[int] = mapped_column(Money, default=0)
    # Bonus, overtime (taxable).
    other_income: Mapped[int] = mapped_column(Money, default=0)
    gross: Mapped[int] = mapped_column(Money, default=0)
    insurance_salary: Mapped[int] = mapped_column(Money, default=0)
    emp_bhxh: Mapped[int] = mapped_column(Money, default=0)
    emp_bhyt: Mapped[int] = mapped_column(Money, default=0)
    emp_bhtn: Mapped[int] = mapped_column(Money, default=0)
    er_bhxh: Mapped[int] = mapped_column(Money, default=0)
    er_bhyt: Mapped[int] = mapped_column(Money, default=0)
    er_bhtn: Mapped[int] = mapped_column(Money, default=0)
    er_kpcd: Mapped[int] = mapped_column(Money, default=0)
    # Taxable income.
    taxable_income: Mapped[int] = mapped_column(Money, default=0)
    self_deduction: Mapped[int] = mapped_column(Money, default=0)
    dependent_deduction: Mapped[int] = mapped_column(Money, default=0)
    # Assessable income (taxable income after deductions).
    assessable_income: Mapped[int] = mapped_column(Money, default=0)
    pit: Mapped[int] = mapped_column(Money, default=0)
    # Deduction of salary advances (TK 141).
    advance_deduction: Mapped[int] = mapped_column(Money, default=0)
    net_pay: Mapped[int] = mapped_column(Money, default=0)
    note: Mapped[str] = mapped_column(String(300), default="")

    run: Mapped[PayrollRun] = relationship(back_populates="lines")
    employee: Mapped[Employee] = relationship()

    @property
    def emp_insurance(self) -> int:
        return self.emp_bhxh + self.emp_bhyt + self.emp_bhtn

    @property
    def er_insurance(self) -> int:
        return self.er_bhxh + self.er_bhyt + self.er_bhtn + self.er_kpcd
