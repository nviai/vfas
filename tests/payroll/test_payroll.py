"""Payroll runs.

Create, edit, post (334, 338x, 3335), cancel/recreate, pay salaries,
summary, parameters.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.core.errors import BusinessError
from app.core.registry import ACCOUNTANT
from app.features.journal import queries
from app.features.journal import service as js
from app.features.payroll import params, service
from app.features.payroll.service import EmployeeInput


@pytest.fixture()
def staff(db, ctxs):
    ctx = ctxs[ACCOUNTANT]
    a = service.create_employee(
        db,
        ctx,
        EmployeeInput(
            full_name="Nguyễn Văn An",
            base_salary=30_000_000,
            insurance_salary=30_000_000,
            dependents=1,
            start_date=date(2025, 1, 1),
        ),
    )
    b = service.create_employee(
        db,
        ctx,
        EmployeeInput(
            full_name="Trần Thị Bình",
            base_salary=12_000_000,
            insurance_salary=12_000_000,
            nontaxable_allowance=730_000,
            expense_account="6421",
            start_date=date(2025, 6, 1),
        ),
    )
    c = service.create_employee(
        db,
        ctx,
        EmployeeInput(
            full_name="Lê Thời Vụ",
            base_salary=4_000_000,
            contract_type="short",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        ),
    )
    return {"a": a, "b": b, "c": c}


def test_run_posting_and_ledger(db, ctxs, staff, company):
    ctx = ctxs[ACCOUNTANT]
    run = service.create_run(db, ctx, 2026, 1)
    assert len(run.lines) == 3
    la = next(ln for ln in run.lines if ln.employee_id == staff["a"].id)
    assert la.pit == 257_500 and la.emp_bhxh == 2_400_000
    lc = next(ln for ln in run.lines if ln.employee_id == staff["c"].id)
    assert lc.pit == 400_000 and lc.emp_bhxh == 0
    with pytest.raises(BusinessError):
        service.create_run(db, ctx, 2026, 1)
    # Edit working days and advances
    service.update_run(
        db,
        ctx,
        run,
        {
            "standard_days": "26",
            f"days-{la.id}": "13",
            f"advance-{la.id}": "1tr",
            f"other-{la.id}": "2.000.000",
        },
    )
    assert (
        la.salary_by_days == 15_000_000
        and la.gross == 17_000_000
        and la.advance_deduction == 1_000_000
    )
    t = service.totals(run)
    e = service.post_run(db, ctx, run)
    assert (
        e.status == "posted"
        and e.doc_type == "LG"
        and e.date == date(2026, 1, 31)
    )
    cid = company.id
    end = date(2026, 1, 31)
    assert -queries.balance(db, cid, "334", upto=end) == t["net_pay"]
    assert (
        -queries.balance(db, cid, "3383", upto=end)
        == t["emp_bhxh"] + t["er_bhxh"]
    )
    assert -queries.balance(db, cid, "3382", upto=end) == t["er_kpcd"]
    assert -queries.balance(db, cid, "3335", upto=end) == t["pit"]
    assert queries.balance(db, cid, "141", upto=end) == -1_000_000
    expense = queries.balance(db, cid, "642", upto=end)
    assert expense == t["gross"] + t["er_insurance"]
    # 334 detailed per employee
    pa = staff["a"].partner_id
    assert (
        -queries.balance(db, cid, "334", upto=end, partner_id=pa) == la.net_pay
    )
    # A posted payroll cannot be edited
    with pytest.raises(BusinessError):
        service.update_run(db, ctx, run, {})
    # Cancel the voucher -> edit -> post again
    js.cancel_entry(db, ctx, e, "Sửa ngày công")
    db.refresh(run)
    assert run.editable and run.status == "draft"
    service.update_run(db, ctx, run, {f"days-{la.id}": "26"})
    e2 = service.post_run(db, ctx, run)
    assert e2.id != e.id and run.status == "posted"


def test_payment_draft(db, ctxs, staff, company):
    ctx = ctxs[ACCOUNTANT]
    run = service.create_run(db, ctx, 2026, 1)
    with pytest.raises(BusinessError):
        service.create_payment(db, ctx, run, "cash", date(2026, 2, 5))
    service.post_run(db, ctx, run)
    pay = service.create_payment(db, ctx, run, "cash", date(2026, 2, 5))
    assert pay.status == "draft" and pay.doc_type == "PC"
    assert pay.total == service.totals(run)["net_pay"]
    assert all(
        ln.debit_account == "334" and ln.credit_account == "1111"
        for ln in pay.lines
    )
    with pytest.raises(BusinessError):
        service.create_payment(db, ctx, run, "cash", date(2026, 2, 5))
    # Delete the draft payment -> it can be created again
    js.delete_draft(db, ctx, pay)
    db.refresh(run)
    assert run.payment_entry_id is None


def test_year_summary_and_unposted(db, ctxs, staff, company):
    ctx = ctxs[ACCOUNTANT]
    r1 = service.create_run(db, ctx, 2026, 1)
    service.post_run(db, ctx, r1)
    service.create_run(db, ctx, 2026, 2)  # not posted
    rows = service.year_summary(db, company.id, 2026)
    assert {r.employee.full_name for r in rows} == {
        "Nguyễn Văn An",
        "Trần Thị Bình",
        "Lê Thời Vụ",
    }
    assert sum(r.pit for r in rows) == service.totals(r1)["pit"]
    unposted = service.months_unposted(db, company.id, date(2026, 10, 14))
    assert [r.month for r in unposted] == [2]


def test_params_versioning(db, ctxs, company):
    ctx = ctxs[ACCOUNTANT]
    p = params.resolve(db, company.id, date(2026, 3, 1))
    assert p.self_deduction == 15_500_000 and p.brackets[0] == (
        10_000_000,
        Decimal("5"),
    )
    params.save_version(
        db,
        ctx,
        "family_deduction",
        date(2027, 1, 1),
        params.parse_value(
            "family_deduction", {"self": "16tr", "dependent": "6.500.000"}
        ),
    )
    assert (
        params.resolve(db, company.id, date(2027, 2, 1)).self_deduction
        == 16_000_000
    )
    assert (
        params.resolve(db, company.id, date(2026, 12, 31)).self_deduction
        == 15_500_000
    )
    brackets = params.parse_value(
        "pit_brackets", {"brackets": "10tr;5\n30tr;10\n;20"}
    )
    assert brackets["brackets"] == [
        [10_000_000, "5"],
        [30_000_000, "10"],
        [None, "20"],
    ]
    with pytest.raises(BusinessError):
        params.parse_value(
            "pit_brackets", {"brackets": "30tr;5\n10tr;10\n;20"}
        )
    rows = params.list_params(db, company.id)["family_deduction"]
    params.delete_version(db, ctx, rows[-1].id)
    assert (
        params.resolve(db, company.id, date(2027, 2, 1)).self_deduction
        == 15_500_000
    )
