"""AI tools of the payroll module.

Payroll data is sensitive: the tools require the `payroll.view`
permission.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.dates import parse_date, today
from app.core.registry import ai_tool
from app.features.payroll import calc, params, service


@ai_tool(
    name="get_payroll_summary",
    label="Xem tổng hợp bảng lương",
    perm="payroll.view",
    description=(
        "Payroll totals for a month: gross pay, insurance "
        "(employee/employer), PIT, net pay, status."
    ),
    parameters={
        "type": "object",
        "properties": {
            "year": {"type": "integer"},
            "month": {"type": "integer"},
        },
        "required": ["year", "month"],
    },
)
def get_payroll_summary(db, ctx, year: int, month: int) -> dict:
    run = service.find_run(db, ctx.company_id, int(year), int(month))
    if run is None:
        return {"year": year, "month": month, "exists": False}
    t = service.totals(run)
    return {
        "year": run.year,
        "month": run.month,
        "exists": True,
        "status": run.status,
        "employees": len(run.lines),
        "gross": t["gross"],
        "employee_insurance": t["emp_insurance"],
        "employer_insurance": t["er_insurance"],
        "pit": t["pit"],
        "net_pay": t["net_pay"],
        "url": f"/payroll/runs/{run.id}",
    }


@ai_tool(
    name="calculate_pit",
    label="Tính thử thuế TNCN",
    description=(
        "Estimate monthly Vietnamese PIT and compulsory insurance for a "
        "resident employee with a labor contract of 3 months or more. "
        "Amounts in VND. Uses the company's effective-dated parameters."
    ),
    parameters={
        "type": "object",
        "properties": {
            "gross": {
                "type": "integer",
                "description": "Monthly taxable salary + allowances",
            },
            "dependents": {"type": "integer"},
            "insurance_salary": {
                "type": "integer",
                "description": "Salary for insurance; default = gross",
            },
            "date": {
                "type": "string",
                "description": "ISO date for parameters, default today",
            },
            "region": {"type": "string", "enum": ["I", "II", "III", "IV"]},
        },
        "required": ["gross"],
    },
)
def calculate_pit(
    db,
    ctx,
    gross: int,
    dependents: int = 0,
    insurance_salary: int | None = None,
    date: str | None = None,
    region: str = "I",
) -> dict:
    on = parse_date(date) if date else today()
    prm = params.resolve(db, ctx.company_id, on)
    res = calc.compute(
        calc.PayInput(
            base_salary=int(gross),
            standard_days=Decimal(1),
            days_worked=Decimal(1),
            insurance_salary=int(
                gross if insurance_salary is None else insurance_salary
            ),
            dependents=int(dependents or 0),
            region=region,
        ),
        prm,
    )
    return {
        "date": on.isoformat(),
        "gross": res.gross,
        "employee_insurance": res.emp_insurance,
        "self_deduction": res.self_deduction,
        "dependent_deduction": res.dependent_deduction,
        "assessable_income": res.assessable_income,
        "pit": res.pit,
        "net_pay": res.net_pay,
        "employer_insurance": res.er_bhxh
        + res.er_bhyt
        + res.er_bhtn
        + res.er_kpcd,
    }
