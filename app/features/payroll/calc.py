"""Compute one employee's monthly pay.

Covers salary by days worked, insurance (with caps), personal income
tax (PIT) and net pay.

Conventions (see docs/requests/payroll.md):
- Insurance is computed on the full-month insurance salary (not
  prorated by days worked). The social/health insurance (BHXH/BHYT)
  cap is cap × base salary; the unemployment insurance (BHTN) cap is
  cap × regional minimum wage. Trade union fee (`KPCĐ`) is 2% of the
  BHXH insurance salary (after the cap).
- Resident with a labor contract of 3 months or more: progressive
  tax on assessable income = taxable income − personal deduction −
  dependent deductions − employee's compulsory insurance.
- Contract under 3 months / probation / no contract: 10% withholding
  when taxable income ≥ the threshold (VND 2 million; VND 5 million
  from 01/07/2026).
- Non-resident: 20% of taxable income.
- Parameters are those effective on the last day of the payroll
  month. Each amount is rounded to whole dong (ROUND_HALF_UP).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.features.payroll.params import Params

HUNDRED = Decimal(100)


def round_vnd(x) -> int:
    return int(Decimal(x).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def pct_of(amount: int, rate: Decimal) -> int:
    return round_vnd(Decimal(amount) * rate / HUNDRED)


def progressive_tax(
    income: int, brackets: list[tuple[int | None, Decimal]]
) -> int:
    """Progressive tax on monthly assessable income."""
    if income <= 0:
        return 0
    tax = Decimal(0)
    lower = 0
    for upper, rate in brackets:
        top = income if upper is None else min(income, upper)
        if top > lower:
            tax += Decimal(top - lower) * rate / HUNDRED
        if upper is None or income <= upper:
            break
        lower = upper
    return round_vnd(tax)


@dataclass
class PayInput:
    base_salary: int
    standard_days: Decimal
    days_worked: Decimal
    taxable_allowance: int = 0
    nontaxable_allowance: int = 0
    other_income: int = 0
    insurance_salary: int = 0
    dependents: int = 0
    contract_type: str = "long"
    is_resident: bool = True
    advance_deduction: int = 0
    region: str = "I"


@dataclass
class PayResult:
    salary_by_days: int
    gross: int
    insurance_salary: int
    emp_bhxh: int
    emp_bhyt: int
    emp_bhtn: int
    er_bhxh: int
    er_bhyt: int
    er_bhtn: int
    er_kpcd: int
    taxable_income: int
    self_deduction: int
    dependent_deduction: int
    assessable_income: int
    pit: int
    advance_deduction: int
    net_pay: int

    @property
    def emp_insurance(self) -> int:
        return self.emp_bhxh + self.emp_bhyt + self.emp_bhtn


def compute(inp: PayInput, p: Params) -> PayResult:
    std = Decimal(inp.standard_days or 0)
    days = Decimal(inp.days_worked or 0)
    if std > 0:
        salary = round_vnd(Decimal(inp.base_salary) * days / std)
    else:
        salary = inp.base_salary
    gross = (
        salary
        + inp.taxable_allowance
        + inp.nontaxable_allowance
        + inp.other_income
    )

    ins = max(0, int(inp.insurance_salary or 0))
    bhxh_base = min(ins, p.cap_multiple * p.base_salary)
    bhtn_base = min(ins, p.cap_multiple * p.min_wage(inp.region))
    r = p.rates
    emp_bhxh = pct_of(bhxh_base, r["emp_bhxh"])
    emp_bhyt = pct_of(bhxh_base, r["emp_bhyt"])
    emp_bhtn = pct_of(bhtn_base, r["emp_bhtn"])
    er_bhxh = pct_of(bhxh_base, r["er_bhxh"])
    er_bhyt = pct_of(bhxh_base, r["er_bhyt"])
    er_bhtn = pct_of(bhtn_base, r["er_bhtn"])
    er_kpcd = pct_of(bhxh_base, r["er_kpcd"])
    emp_ins = emp_bhxh + emp_bhyt + emp_bhtn

    taxable = max(0, gross - inp.nontaxable_allowance)
    self_ded = dep_ded = 0
    assessable = 0
    if not inp.is_resident:
        pit = pct_of(taxable, p.non_resident_rate)
        assessable = taxable
    elif inp.contract_type in ("short", "probation", "none"):
        pit = (
            pct_of(taxable, p.short_term_rate)
            if taxable >= p.short_term_threshold
            else 0
        )
        assessable = taxable
    else:
        self_ded = p.self_deduction
        dep_ded = p.dependent_deduction * max(0, int(inp.dependents or 0))
        assessable = max(0, taxable - self_ded - dep_ded - emp_ins)
        pit = progressive_tax(assessable, p.brackets)
    advance = max(0, int(inp.advance_deduction or 0))
    net = gross - emp_ins - pit - advance
    return PayResult(
        salary_by_days=salary,
        gross=gross,
        insurance_salary=bhxh_base,
        emp_bhxh=emp_bhxh,
        emp_bhyt=emp_bhyt,
        emp_bhtn=emp_bhtn,
        er_bhxh=er_bhxh,
        er_bhyt=er_bhyt,
        er_bhtn=er_bhtn,
        er_kpcd=er_kpcd,
        taxable_income=taxable,
        self_deduction=self_ded,
        dependent_deduction=dep_ded,
        assessable_income=assessable,
        pit=pit,
        advance_deduction=advance,
        net_pay=net,
    )
