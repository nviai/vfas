"""Payroll services.

Employees, monthly payroll runs, payroll posting (LG), salary payment,
and insurance and PIT summaries.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.audit import write_audit
from app.core.dates import month_bounds
from app.core.errors import BusinessError, NotFoundError
from app.core.money import MoneyError, format_vnd, parse_vnd
from app.core.regimes import account as regime_account
from app.core.regimes import regime_of
from app.features.accounts.models import Account
from app.features.journal import service as js
from app.features.journal.models import CANCELLED, DRAFT, POSTED, JournalEntry
from app.features.org.models import Company
from app.features.partners import service as partners
from app.features.partners.models import Partner
from app.features.payroll import calc, params
from app.features.payroll.models import (
    CONTRACT_TYPES,
    ID_TYPES,
    RELATIONSHIPS,
    Employee,
    EmployeeDependent,
    PayrollLine,
    PayrollRun,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def money_field(form: dict, name: str, default: int = 0) -> int:
    raw = form.get(name)
    if raw in (None, ""):
        return default
    try:
        v = parse_vnd(raw)
    except MoneyError as e:
        raise BusinessError(str(e)) from e
    if v < 0:
        raise BusinessError("Số tiền không được âm")
    return v


def decimal_field(
    form: dict, name: str, default: Decimal = Decimal(0)
) -> Decimal:
    raw = (form.get(name) or "").strip().replace(",", ".")
    if not raw:
        return default
    try:
        d = Decimal(raw)
    except InvalidOperation as e:
        raise BusinessError(f"Số không hợp lệ: {raw}") from e
    if d < 0 or d > 1000:
        raise BusinessError("Số ngày công không hợp lệ")
    return d


# ------------------------------------------------------------ employees


@dataclass
class EmployeeInput:
    full_name: str
    code: str = ""
    department: str = ""
    position: str = ""
    contract_type: str = "long"
    is_resident: bool = True
    dependents: int = 0
    base_salary: int = 0
    insurance_salary: int = 0
    taxable_allowance: int = 0
    nontaxable_allowance: int = 0
    # Empty = the regime's default (staff expenses).
    expense_account: str = ""
    start_date: date | None = None
    end_date: date | None = None
    tax_code: str = ""
    id_number: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    bank_account: str = ""
    bank_name: str = ""
    is_active: bool = True
    note: str = ""


EMP_FIELDS = (
    "department",
    "position",
    "contract_type",
    "is_resident",
    "dependents",
    "base_salary",
    "insurance_salary",
    "taxable_allowance",
    "nontaxable_allowance",
    "expense_account",
    "start_date",
    "end_date",
    "is_active",
    "note",
)


def list_employees(
    db: Session, company_id: int, *, active: bool | None = True, q: str = ""
) -> list[Employee]:
    stmt = (
        select(Employee)
        .join(Partner, Partner.id == Employee.partner_id)
        .where(Employee.company_id == company_id)
        .options(selectinload(Employee.partner))
    )
    if active is not None:
        stmt = stmt.where(Employee.is_active.is_(active))
    if q:
        from app.core.db import ulower

        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            ulower(Partner.name).like(like) | ulower(Partner.code).like(like)
        )
    return list(db.scalars(stmt.order_by(Partner.code)))


def get_employee(db: Session, company_id: int, employee_id: int) -> Employee:
    e = db.get(Employee, employee_id)
    if e is None or e.company_id != company_id:
        raise NotFoundError("Không tìm thấy nhân viên")
    return e


def salary_expense_prefixes(company) -> tuple[str, ...]:
    """Accounts a salary may be charged to (company regime)."""
    return tuple(p for p in regime_of(company).expense_prefixes if p != "811")


def _validate_employee(
    db: Session, company_id: int, inp: EmployeeInput
) -> None:
    company = db.get(Company, company_id)
    if not inp.expense_account:
        inp.expense_account = regime_account(company, "expense_staff")
    if not inp.full_name.strip():
        raise BusinessError("Nhập họ tên nhân viên")
    if inp.contract_type not in CONTRACT_TYPES:
        raise BusinessError("Loại hợp đồng không hợp lệ")
    if inp.dependents < 0 or inp.dependents > 20:
        raise BusinessError("Số người phụ thuộc không hợp lệ")
    if inp.start_date and inp.end_date and inp.end_date < inp.start_date:
        raise BusinessError("Ngày nghỉ việc trước ngày vào làm")
    acc = db.scalar(
        select(Account).where(
            Account.company_id == company_id,
            Account.code == inp.expense_account,
        )
    )
    allowed = salary_expense_prefixes(company)
    if (
        acc is None
        or not acc.is_postable
        or not inp.expense_account.startswith(allowed)
    ):
        raise BusinessError(
            "TK chi phí lương phải là TK chi tiết thuộc " + ", ".join(allowed)
        )


def create_employee(db: Session, ctx, inp: EmployeeInput) -> Employee:
    _validate_employee(db, ctx.company_id, inp)
    p = partners.create(
        db,
        ctx,
        partners.PartnerInput(
            name=inp.full_name,
            code=inp.code,
            is_employee=True,
            tax_code=inp.tax_code,
            id_number=inp.id_number,
            phone=inp.phone,
            email=inp.email,
            address=inp.address,
            bank_account=inp.bank_account,
            bank_name=inp.bank_name,
        ),
        commit=False,
    )
    e = Employee(company_id=ctx.company_id, partner_id=p.id, created_at=_now())
    for f in EMP_FIELDS:
        setattr(e, f, getattr(inp, f))
    db.add(e)
    db.flush()
    write_audit(
        db,
        ctx,
        action="payroll.employee_create",
        entity_type="employee",
        entity_id=e.id,
        summary=f"Thêm nhân viên {p.code} – {p.name}",
    )
    db.commit()
    return e


def create_from_partner(db: Session, ctx, partner_id: int) -> Employee:
    p = partners.get_partner(db, ctx.company_id, partner_id)
    existing = db.scalar(
        select(Employee).where(
            Employee.company_id == ctx.company_id, Employee.partner_id == p.id
        )
    )
    if existing is not None:
        return existing
    p.is_employee = True
    e = Employee(
        company_id=ctx.company_id,
        partner_id=p.id,
        created_at=_now(),
        expense_account=regime_account(
            db.get(Company, ctx.company_id), "expense_staff"
        ),
    )
    db.add(e)
    db.flush()
    write_audit(
        db,
        ctx,
        action="payroll.employee_create",
        entity_type="employee",
        entity_id=e.id,
        summary=f"Thêm nhân viên từ đối tượng {p.code}",
    )
    db.commit()
    return e


def update_employee(
    db: Session, ctx, e: Employee, inp: EmployeeInput
) -> Employee:
    if e.company_id != ctx.company_id:
        raise NotFoundError()
    _validate_employee(db, ctx.company_id, inp)
    before = {f: getattr(e, f) for f in EMP_FIELDS}
    for f in EMP_FIELDS:
        setattr(e, f, getattr(inp, f))
    p = e.partner
    p.name = inp.full_name.strip()[:255]
    for f in (
        "tax_code",
        "id_number",
        "phone",
        "email",
        "address",
        "bank_account",
        "bank_name",
    ):
        setattr(p, f, (getattr(inp, f) or "").strip())
    if inp.code.strip():
        p.code = inp.code.strip()
    after = {f: getattr(e, f) for f in EMP_FIELDS}
    changed = sorted(k for k in after if after[k] != before[k])
    # Do not log values (salary, allowances, number of dependents, etc.
    # are personal data); log only the names of the changed fields.
    write_audit(
        db,
        ctx,
        action="payroll.employee_update",
        entity_type="employee",
        entity_id=e.id,
        summary=f"Sửa hồ sơ nhân viên {p.code}",
        after={"changed_fields": changed} if changed else None,
    )
    db.commit()
    return e


# --------------------------------------------------------- payroll runs


def period_end(year: int, month: int) -> date:
    return month_bounds(year, month)[1]


def get_run(db: Session, company_id: int, run_id: int) -> PayrollRun:
    r = db.get(PayrollRun, run_id)
    if r is None or r.company_id != company_id:
        raise NotFoundError("Không tìm thấy bảng lương")
    return r


def find_run(
    db: Session, company_id: int, year: int, month: int
) -> PayrollRun | None:
    return db.scalar(
        select(PayrollRun).where(
            PayrollRun.company_id == company_id,
            PayrollRun.year == year,
            PayrollRun.month == month,
        )
    )


def list_runs(db: Session, company_id: int) -> list[PayrollRun]:
    return list(
        db.scalars(
            select(PayrollRun)
            .where(PayrollRun.company_id == company_id)
            .order_by(PayrollRun.year.desc(), PayrollRun.month.desc())
        )
    )


def _eligible(e: Employee, year: int, month: int) -> bool:
    start, end = month_bounds(year, month)
    if e.start_date and e.start_date > end:
        return False
    if e.end_date and e.end_date < start:
        return False
    return True


def _new_line(run: PayrollRun, e: Employee) -> PayrollLine:
    line = PayrollLine(
        company_id=run.company_id,
        employee_id=e.id,
        contract_type=e.contract_type,
        is_resident=e.is_resident,
        dependents=e.dependents,
        expense_account=e.expense_account,
        base_salary=e.base_salary,
        days_worked=run.standard_days,
        taxable_allowance=e.taxable_allowance,
        nontaxable_allowance=e.nontaxable_allowance,
        other_income=0,
        insurance_salary=e.insurance_salary,
        advance_deduction=0,
    )
    line.employee = e
    return line


def recompute_run(db: Session, run: PayrollRun) -> None:
    prm = params.resolve(db, run.company_id, period_end(run.year, run.month))
    region = db.get(Company, run.company_id).wage_region or "I"
    for line in run.lines:
        _recompute_line(line, run, prm, region)


def _recompute_line(
    line: PayrollLine, run: PayrollRun, prm: params.Params, region: str
) -> None:
    res = calc.compute(
        calc.PayInput(
            base_salary=line.base_salary,
            standard_days=Decimal(run.standard_days or 0),
            days_worked=Decimal(line.days_worked or 0),
            taxable_allowance=line.taxable_allowance,
            nontaxable_allowance=line.nontaxable_allowance,
            other_income=line.other_income,
            insurance_salary=line.insurance_salary,
            dependents=line.dependents,
            contract_type=line.contract_type,
            is_resident=line.is_resident,
            advance_deduction=line.advance_deduction,
            region=region,
        ),
        prm,
    )
    for f in (
        "salary_by_days",
        "gross",
        "emp_bhxh",
        "emp_bhyt",
        "emp_bhtn",
        "er_bhxh",
        "er_bhyt",
        "er_bhtn",
        "er_kpcd",
        "taxable_income",
        "self_deduction",
        "dependent_deduction",
        "assessable_income",
        "pit",
        "advance_deduction",
        "net_pay",
    ):
        setattr(line, f, getattr(res, f))


def create_run(
    db: Session,
    ctx,
    year: int,
    month: int,
    standard_days: Decimal = Decimal(26),
) -> PayrollRun:
    if not (1 <= month <= 12) or year < 2000 or year > 2100:
        raise BusinessError("Kỳ lương không hợp lệ")
    company = db.get(Company, ctx.company_id)
    if period_end(year, month) < company.start_date:
        raise BusinessError("Kỳ lương trước ngày bắt đầu hạch toán")
    if find_run(db, ctx.company_id, year, month) is not None:
        raise BusinessError(f"Đã có bảng lương tháng {month:02d}/{year}")
    if standard_days <= 0:
        raise BusinessError("Số ngày công chuẩn phải lớn hơn 0")
    run = PayrollRun(
        company_id=ctx.company_id,
        year=year,
        month=month,
        standard_days=standard_days,
        created_at=_now(),
        created_by=ctx.user_id,
    )
    db.add(run)
    db.flush()
    for e in list_employees(db, ctx.company_id):
        if _eligible(e, year, month):
            run.lines.append(_new_line(run, e))
    recompute_run(db, run)
    db.flush()
    write_audit(
        db,
        ctx,
        action="payroll.run_create",
        entity_type="payroll_run",
        entity_id=run.id,
        summary=(
            f"Lập bảng lương tháng {month:02d}/{year} "
            f"({len(run.lines)} nhân viên)"
        ),
    )
    db.commit()
    return run


def _require_editable(run: PayrollRun) -> None:
    if not run.editable:
        raise BusinessError(
            "Bảng lương đã ghi sổ. "
            "Hủy chứng từ hạch toán lương (kỳ còn mở) để sửa."
        )


def update_run(db: Session, ctx, run: PayrollRun, form: dict) -> PayrollRun:
    """Update the standard working days and each line from the form.

    Per-line fields: days-<id>, other-<id>, advance-<id>, tallow-<id>,
    nallow-<id>, note-<id>.
    """
    _require_editable(run)
    run.standard_days = decimal_field(
        form, "standard_days", Decimal(run.standard_days)
    )
    if run.standard_days <= 0:
        raise BusinessError("Số ngày công chuẩn phải lớn hơn 0")
    run.note = (form.get("note") or run.note or "")[:1000]
    for line in run.lines:
        lid = line.id
        line.days_worked = decimal_field(
            form, f"days-{lid}", Decimal(line.days_worked)
        )
        line.other_income = money_field(
            form, f"other-{lid}", line.other_income
        )
        line.advance_deduction = money_field(
            form, f"advance-{lid}", line.advance_deduction
        )
        line.taxable_allowance = money_field(
            form, f"tallow-{lid}", line.taxable_allowance
        )
        line.nontaxable_allowance = money_field(
            form, f"nallow-{lid}", line.nontaxable_allowance
        )
        if f"note-{lid}" in form:
            line.note = (form.get(f"note-{lid}") or "")[:300]
    recompute_run(db, run)
    write_audit(
        db,
        ctx,
        action="payroll.run_update",
        entity_type="payroll_run",
        entity_id=run.id,
        summary=f"Sửa bảng lương tháng {run.period_label}",
    )
    db.commit()
    return run


def refresh_employees(db: Session, ctx, run: PayrollRun) -> PayrollRun:
    """Rebuild the lines from the current employee profiles.

    Keeps the days worked, other income, advance deductions and notes
    already entered.
    """
    _require_editable(run)
    keep = {
        ln.employee_id: (
            ln.days_worked,
            ln.other_income,
            ln.advance_deduction,
            ln.note,
        )
        for ln in run.lines
    }
    run.lines.clear()
    db.flush()
    for e in list_employees(db, ctx.company_id):
        if not _eligible(e, run.year, run.month):
            continue
        line = _new_line(run, e)
        if e.id in keep:
            (
                line.days_worked,
                line.other_income,
                line.advance_deduction,
                line.note,
            ) = keep[e.id]
        run.lines.append(line)
    recompute_run(db, run)
    write_audit(
        db,
        ctx,
        action="payroll.run_refresh",
        entity_type="payroll_run",
        entity_id=run.id,
        summary=(
            f"Cập nhật danh sách nhân viên bảng lương tháng {run.period_label}"
        ),
    )
    db.commit()
    return run


def delete_run(db: Session, ctx, run: PayrollRun) -> None:
    if run.entry is not None and run.entry.status == POSTED:
        raise BusinessError(
            "Bảng lương đã ghi sổ, không xóa được. "
            "Hủy chứng từ hạch toán lương trước."
        )
    if run.entry is not None and run.entry.status == DRAFT:
        js.delete_draft(db, ctx, run.entry)
    write_audit(
        db,
        ctx,
        action="payroll.run_delete",
        entity_type="payroll_run",
        entity_id=run.id,
        summary=f"Xóa bảng lương tháng {run.period_label}",
    )
    db.delete(run)
    db.commit()


def totals(run: PayrollRun) -> dict[str, int]:
    keys = (
        "salary_by_days",
        "taxable_allowance",
        "nontaxable_allowance",
        "other_income",
        "gross",
        "emp_bhxh",
        "emp_bhyt",
        "emp_bhtn",
        "er_bhxh",
        "er_bhyt",
        "er_bhtn",
        "er_kpcd",
        "taxable_income",
        "assessable_income",
        "pit",
        "advance_deduction",
        "net_pay",
    )
    out = {k: sum(getattr(ln, k) for ln in run.lines) for k in keys}
    out["emp_insurance"] = out["emp_bhxh"] + out["emp_bhyt"] + out["emp_bhtn"]
    out["er_insurance"] = (
        out["er_bhxh"] + out["er_bhyt"] + out["er_bhtn"] + out["er_kpcd"]
    )
    return out


def build_entry_lines(run: PayrollRun, bhtn: str = "3385") -> list[js.LineIn]:
    """Build the payroll journal lines.

    TK 334 lines are detailed per employee; the employer's insurance
    contributions are grouped by expense account. `bhtn` is the
    unemployment insurance account of the regime (3385 under TT133,
    3386 under TT99).
    """
    period = run.period_label
    out: list[js.LineIn] = []
    employer: dict[tuple[str, str], int] = defaultdict(int)
    for ln in run.lines:
        pid = ln.employee.partner_id
        name = ln.employee.full_name
        if ln.gross > 0:
            out.append(
                js.LineIn(
                    ln.expense_account,
                    "334",
                    ln.gross,
                    f"Lương tháng {period} – {name}",
                    credit_partner_id=pid,
                )
            )
        for amount, acc, label in (
            (ln.emp_bhxh, "3383", "BHXH"),
            (ln.emp_bhyt, "3384", "BHYT"),
            (ln.emp_bhtn, bhtn, "BHTN"),
        ):
            if amount > 0:
                out.append(
                    js.LineIn(
                        "334",
                        acc,
                        amount,
                        f"Khấu trừ {label} tháng {period} – {name}",
                        debit_partner_id=pid,
                    )
                )
        if ln.pit > 0:
            out.append(
                js.LineIn(
                    "334",
                    "3335",
                    ln.pit,
                    f"Khấu trừ thuế TNCN tháng {period} – {name}",
                    debit_partner_id=pid,
                )
            )
        if ln.advance_deduction > 0:
            out.append(
                js.LineIn(
                    "334",
                    "141",
                    ln.advance_deduction,
                    f"Trừ tạm ứng vào lương {period} – {name}",
                    debit_partner_id=pid,
                    credit_partner_id=pid,
                )
            )
        for amount, acc in (
            (ln.er_bhxh, "3383"),
            (ln.er_bhyt, "3384"),
            (ln.er_bhtn, bhtn),
            (ln.er_kpcd, "3382"),
        ):
            if amount > 0:
                employer[(ln.expense_account, acc)] += amount
    labels = {"3383": "BHXH", "3384": "BHYT", bhtn: "BHTN", "3382": "KPCĐ"}
    for (exp, acc), amount in sorted(employer.items()):
        out.append(
            js.LineIn(
                exp,
                acc,
                amount,
                f"Trích {labels[acc]} phần doanh nghiệp tháng {period}",
            )
        )
    return out


def post_run(db: Session, ctx, run: PayrollRun) -> JournalEntry:
    """Create and post the payroll journal entry (LG) for the run."""
    _require_editable(run)
    if not run.lines:
        raise BusinessError("Bảng lương chưa có nhân viên")
    recompute_run(db, run)
    db.flush()
    lines = build_entry_lines(
        run,
        regime_account(
            db.get(Company, run.company_id), "unemployment_insurance"
        ),
    )
    if not lines:
        raise BusinessError("Bảng lương không có số tiền để hạch toán")
    inp = js.EntryIn(
        doc_type="LG",
        date=period_end(run.year, run.month),
        description=f"Hạch toán lương tháng {run.period_label}",
        lines=lines,
        source="payroll",
        source_id=run.id,
        meta={"payroll_run": run.id, "year": run.year, "month": run.month},
    )
    if run.entry is not None and run.entry.status == DRAFT:
        e = js.update_draft(db, ctx, run.entry, inp, commit=False)
    else:
        e = js.create_entry(db, ctx, inp, commit=False)
    run.entry = e
    db.flush()
    js.post_entry(
        db, ctx, e, action_label=f"ghi sổ bảng lương tháng {run.period_label}"
    )
    return e


def create_payment(
    db: Session,
    ctx,
    run: PayrollRun,
    method: str,
    on: date,
    bank_account_id: int | None = None,
) -> JournalEntry:
    """Create a **draft** salary payment entry.

    A cash payment voucher (PC) or bank payment (BN) paying each
    employee's net pay.
    """
    if run.status != POSTED:
        raise BusinessError("Ghi sổ bảng lương trước khi trả lương")
    if run.payment_entry is not None and run.payment_entry.status != CANCELLED:
        raise BusinessError(
            f"Đã lập chứng từ trả lương {run.payment_entry.display_number}"
        )
    if method not in ("cash", "bank"):
        raise BusinessError("Chọn hình thức trả lương")
    if method == "bank" and not bank_account_id:
        raise BusinessError("Chọn tài khoản ngân hàng")
    lines = []
    for ln in run.lines:
        if ln.net_pay <= 0:
            continue
        lines.append(
            js.LineIn(
                "334",
                "1111" if method == "cash" else "1121",
                ln.net_pay,
                f"Trả lương tháng {run.period_label} – "
                f"{ln.employee.full_name}",
                debit_partner_id=ln.employee.partner_id,
                credit_bank_account_id=bank_account_id
                if method == "bank"
                else None,
            )
        )
    if not lines:
        raise BusinessError("Không có khoản thực lĩnh cần trả")
    company = db.get(Company, ctx.company_id)
    inp = js.EntryIn(
        doc_type="PC" if method == "cash" else "BN",
        date=on,
        doc_date=on,
        description=f"Trả lương tháng {run.period_label}",
        lines=lines,
        source="payroll",
        source_id=run.id,
        contact_name=f"Nhân viên {company.short_name or company.name}"[:200],
        meta={"payroll_run": run.id, "kind": "salary_payment"},
    )
    e = js.create_entry(db, ctx, inp, commit=False)
    run.payment_entry = e
    write_audit(
        db,
        ctx,
        action="payroll.payment",
        entity_type="payroll_run",
        entity_id=run.id,
        summary=(
            f"Lập chứng từ trả lương tháng {run.period_label}: "
            f"{format_vnd(e.total)}"
        ),
    )
    db.commit()
    return e


# ------------------------------------------------------------ summaries


def posted_lines(
    db: Session,
    company_id: int,
    year: int,
    month: int | None = None,
    *,
    months: tuple[int, ...] | None = None,
) -> list[PayrollLine]:
    """Lines of posted payroll runs of a year, one month or a set of
    months (a quarter).
    """
    stmt = (
        select(PayrollLine)
        .join(PayrollRun, PayrollRun.id == PayrollLine.run_id)
        .join(JournalEntry, JournalEntry.id == PayrollRun.entry_id)
        .where(
            PayrollRun.company_id == company_id,
            PayrollRun.year == year,
            JournalEntry.status == POSTED,
        )
        .options(
            selectinload(PayrollLine.employee).selectinload(Employee.partner),
            selectinload(PayrollLine.run),
        )
    )
    if month:
        stmt = stmt.where(PayrollRun.month == month)
    if months:
        stmt = stmt.where(PayrollRun.month.in_(months))
    return list(db.scalars(stmt))


@dataclass
class EmpSummary:
    employee: Employee
    months: dict[int, PayrollLine]
    gross: int = 0
    taxable_income: int = 0
    emp_insurance: int = 0
    er_insurance: int = 0
    self_deduction: int = 0
    dependent_deduction: int = 0
    assessable_income: int = 0
    pit: int = 0
    net_pay: int = 0
    emp_bhxh: int = 0
    emp_bhyt: int = 0
    emp_bhtn: int = 0
    er_bhxh: int = 0
    er_bhyt: int = 0
    er_bhtn: int = 0
    er_kpcd: int = 0


def year_summary(
    db: Session, company_id: int, year: int, month: int | None = None
) -> list[EmpSummary]:
    """Per-employee totals of posted payroll runs in a year or month."""
    by_emp: dict[int, EmpSummary] = {}
    for ln in posted_lines(db, company_id, year, month):
        s = by_emp.get(ln.employee_id)
        if s is None:
            s = by_emp[ln.employee_id] = EmpSummary(
                employee=ln.employee, months={}
            )
        s.months[ln.run.month] = ln
        for f in (
            "gross",
            "taxable_income",
            "self_deduction",
            "dependent_deduction",
            "assessable_income",
            "pit",
            "net_pay",
            "emp_bhxh",
            "emp_bhyt",
            "emp_bhtn",
            "er_bhxh",
            "er_bhyt",
            "er_bhtn",
            "er_kpcd",
        ):
            setattr(s, f, getattr(s, f) + getattr(ln, f))
        s.emp_insurance += ln.emp_insurance
        s.er_insurance += ln.er_insurance
    return sorted(by_emp.values(), key=lambda s: s.employee.code)


def months_unposted(
    db: Session, company_id: int, before: date
) -> list[PayrollRun]:
    """Payroll runs of months that have ended but are not posted."""
    out = []
    for run in list_runs(db, company_id):
        if period_end(run.year, run.month) < before and run.status != POSTED:
            out.append(run)
    return out


# ----------------------------------------------------------- dependants


def list_dependents(
    db: Session, company_id: int, employee_id: int | None = None
) -> list[EmployeeDependent]:
    stmt = select(EmployeeDependent).where(
        EmployeeDependent.company_id == company_id
    )
    if employee_id is not None:
        stmt = stmt.where(EmployeeDependent.employee_id == employee_id)
    return list(
        db.scalars(
            stmt.order_by(EmployeeDependent.from_month, EmployeeDependent.id)
        )
    )


def _month(value, label: str, required: bool = True) -> date | None:
    """First day of a month typed as YYYY-MM (or a full date)."""
    raw = (str(value or "")).strip()
    if not raw:
        if required:
            raise BusinessError(f"Chưa nhập {label}")
        return None
    try:
        y, m = (int(x) for x in raw.split("-")[:2])
        return date(y, m, 1)
    except ValueError as e:
        raise BusinessError(f"{label.capitalize()} không hợp lệ") from e


def add_dependent(
    db: Session, ctx, employee: Employee, form: dict
) -> EmployeeDependent:
    name = (form.get("full_name") or "").strip()
    if not name:
        raise BusinessError("Chưa nhập họ tên người phụ thuộc")
    tax_code = (form.get("tax_code") or "").strip()
    id_number = (form.get("id_number") or "").strip()
    if tax_code and not (tax_code.isdigit() and len(tax_code) in (10, 12)):
        raise BusinessError("Mã số thuế người phụ thuộc phải có 10 hoặc 12 số")
    if not tax_code and not id_number:
        raise BusinessError(
            "Nhập mã số thuế hoặc số giấy tờ của người phụ thuộc"
        )
    relationship = form.get("relationship") or "01"
    id_type = form.get("id_type") or "2080"
    if relationship not in RELATIONSHIPS or id_type not in ID_TYPES:
        raise BusinessError("Dữ liệu không hợp lệ")
    start = _month(form.get("from_month"), "tháng bắt đầu giảm trừ")
    end = _month(form.get("to_month"), "tháng kết thúc", required=False)
    if end is not None and end < start:
        raise BusinessError("Tháng kết thúc trước tháng bắt đầu giảm trừ")
    birth = (form.get("birth_date") or "").strip()
    try:
        birth_date = date.fromisoformat(birth) if birth else None
    except ValueError as e:
        raise BusinessError("Ngày sinh không hợp lệ") from e
    d = EmployeeDependent(
        company_id=ctx.company_id,
        employee_id=employee.id,
        full_name=name,
        birth_date=birth_date,
        tax_code=tax_code,
        id_type=id_type,
        id_number=id_number,
        relationship=relationship,
        from_month=start,
        to_month=end,
        created_at=_now(),
    )
    db.add(d)
    db.flush()
    write_audit(
        db,
        ctx,
        action="payroll.dependent_add",
        entity_type="employee",
        entity_id=employee.id,
        summary=f"Thêm người phụ thuộc {name} của {employee.full_name}",
    )
    db.commit()
    return d


def delete_dependent(
    db: Session, ctx, employee: Employee, dependent_id: int
) -> None:
    d = db.get(EmployeeDependent, dependent_id)
    if (
        d is None
        or d.company_id != ctx.company_id
        or d.employee_id != employee.id
    ):
        raise NotFoundError("Không tìm thấy người phụ thuộc")
    db.delete(d)
    write_audit(
        db,
        ctx,
        action="payroll.dependent_delete",
        entity_type="employee",
        entity_id=employee.id,
        summary=f"Xóa người phụ thuộc {d.full_name} của {employee.full_name}",
    )
    db.commit()


def active_dependents(
    dependents: list[EmployeeDependent], year: int, month: int
) -> int:
    return sum(1 for d in dependents if d.active_in(year, month))
