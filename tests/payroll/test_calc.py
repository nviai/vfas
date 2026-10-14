"""Payroll formulas.

Progressive PIT 2025 vs 2026, capped insurance, 10% withholding,
non-residents.
"""

from datetime import date
from decimal import Decimal

from app.features.payroll import calc
from app.features.payroll.params import PIT_5, PIT_7, default_params


def _b(raw):
    return [(None if u is None else u, Decimal(r)) for u, r in raw]


def test_progressive_tax_tables():
    assert calc.progressive_tax(0, _b(PIT_7)) == 0
    assert calc.progressive_tax(5_000_000, _b(PIT_7)) == 250_000
    assert calc.progressive_tax(11_450_000, _b(PIT_7)) == 967_500
    # 7 brackets, taxable income 100M: 0.25+0.5+1.2+2.8+5+8.4 = 18.15M
    # + 20M x 35% = 25.15M
    assert calc.progressive_tax(100_000_000, _b(PIT_7)) == 25_150_000
    assert calc.progressive_tax(5_150_000, _b(PIT_5)) == 257_500
    # 5 brackets, 100M: 0.5 + 2 + 6 + 12 = 20.5M; 120M: + 20M x 35% =
    # 27.5M
    assert calc.progressive_tax(100_000_000, _b(PIT_5)) == 20_500_000
    assert calc.progressive_tax(120_000_000, _b(PIT_5)) == 27_500_000


def _resident(gross, ins, dep, on):
    return calc.compute(
        calc.PayInput(
            base_salary=gross,
            standard_days=Decimal(26),
            days_worked=Decimal(26),
            insurance_salary=ins,
            dependents=dep,
        ),
        default_params(on),
    )


def test_resident_2025_vs_2026():
    r25 = _resident(30_000_000, 30_000_000, 1, date(2025, 12, 31))
    assert (r25.emp_bhxh, r25.emp_bhyt, r25.emp_bhtn) == (
        2_400_000,
        450_000,
        300_000,
    )
    assert (r25.self_deduction, r25.dependent_deduction) == (
        11_000_000,
        4_400_000,
    )
    assert r25.assessable_income == 11_450_000 and r25.pit == 967_500
    assert r25.net_pay == 30_000_000 - 3_150_000 - 967_500
    assert (r25.er_bhxh, r25.er_bhyt, r25.er_bhtn, r25.er_kpcd) == (
        5_250_000,
        900_000,
        300_000,
        600_000,
    )
    r26 = _resident(30_000_000, 30_000_000, 1, date(2026, 1, 31))
    assert (r26.self_deduction, r26.dependent_deduction) == (
        15_500_000,
        6_200_000,
    )
    assert r26.assessable_income == 5_150_000 and r26.pit == 257_500


def test_insurance_caps():
    jan = _resident(60_000_000, 60_000_000, 0, date(2026, 1, 31))
    # cap 20 x 2,340,000
    assert jan.emp_bhxh == 3_744_000 and jan.emp_bhyt == 702_000
    assert jan.emp_bhtn == 600_000  # BHTN cap 20 x 5,310,000 not reached
    jul = _resident(60_000_000, 60_000_000, 0, date(2026, 7, 31))
    assert jul.emp_bhxh == 4_048_000  # cap 20 x 2,530,000
    huge = _resident(200_000_000, 200_000_000, 0, date(2026, 1, 31))
    assert huge.emp_bhtn == 1_062_000  # 1% x 20 x 5,310,000


def test_short_term_and_non_resident():
    p_mar = default_params(date(2026, 3, 31))
    p_aug = default_params(date(2026, 8, 31))

    def short(amount, prm):
        return calc.compute(
            calc.PayInput(
                base_salary=amount,
                standard_days=Decimal(1),
                days_worked=Decimal(1),
                contract_type="short",
            ),
            prm,
        ).pit

    assert short(4_000_000, p_mar) == 400_000
    assert short(1_999_999, p_mar) == 0
    assert short(4_000_000, p_aug) == 0  # 5M threshold from 01/7/2026
    assert short(6_000_000, p_aug) == 600_000
    nr = calc.compute(
        calc.PayInput(
            base_salary=50_000_000,
            standard_days=Decimal(1),
            days_worked=Decimal(1),
            is_resident=False,
        ),
        p_mar,
    )
    assert nr.pit == 10_000_000


def test_days_allowances_and_advance():
    r = calc.compute(
        calc.PayInput(
            base_salary=26_000_000,
            standard_days=Decimal(26),
            days_worked=Decimal("13.5"),
            taxable_allowance=1_000_000,
            nontaxable_allowance=730_000,
            insurance_salary=10_000_000,
            advance_deduction=2_000_000,
        ),
        default_params(date(2026, 2, 28)),
    )
    assert r.salary_by_days == 13_500_000
    assert r.gross == 15_230_000 and r.taxable_income == 14_500_000
    assert r.pit == 0  # below the 15.5M deduction
    assert r.net_pay == 15_230_000 - 1_050_000 - 2_000_000
