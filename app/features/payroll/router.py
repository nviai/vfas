"""Payroll routes.

Employees, monthly payroll runs, posting, salary payment, insurance
and PIT reports, and payroll parameters.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.dates import parse_date, today
from app.core.errors import BusinessError
from app.core.money import format_vnd
from app.core.pdf import render_pdf
from app.core.regimes import account as regime_account
from app.core.security import CurrentUser, require
from app.core.web import form_data, get_ctx, get_tenant_db, redirect, render
from app.core.xlsx import Col, ReportBook, pdf_response, xlsx_response
from app.features.accounts.service import list_accounts
from app.features.bank.service import list_bank_accounts
from app.features.journal import service as js
from app.features.org.models import Company
from app.features.payroll import params, service
from app.features.payroll.models import CONTRACT_TYPES, PayrollRun
from app.features.payroll.service import (
    EmployeeInput,
    decimal_field,
    money_field,
)

router = APIRouter()


def _company(db: Session, user: CurrentUser) -> Company:
    return db.get(Company, user.company_id)


def _int(form: dict, name: str, default: int = 0) -> int:
    raw = (str(form.get(name) or "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise BusinessError(f"Số không hợp lệ: {raw}") from e


def _date(form: dict, name: str) -> date | None:
    try:
        return parse_date(form.get(name))
    except ValueError as e:
        raise BusinessError("Ngày không hợp lệ") from e


# --------------------------------------------------------- payroll runs


@router.get("/payroll")
def payroll_page(
    request: Request,
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    runs = service.list_runs(db, user.company_id)
    d = today()
    taken = {(r.year, r.month) for r in runs}
    suggest = (d.year, d.month)
    if suggest in taken:
        suggest = (d.year + (d.month == 12), d.month % 12 + 1)
    return render(
        request,
        "payroll/index.html",
        {
            "runs": runs,
            "totals": {r.id: service.totals(r) for r in runs},
            "suggest": suggest,
            "employee_count": len(service.list_employees(db, user.company_id)),
            "breadcrumbs": [("Lương", None), ("Bảng lương", None)],
        },
    )


@router.post("/payroll/runs")
def run_create(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("payroll.create")),
    db: Session = Depends(get_tenant_db),
):
    year, month = _int(form, "year"), _int(form, "month")
    run = service.create_run(
        db,
        get_ctx(request),
        year,
        month,
        decimal_field(form, "standard_days", Decimal(26)),
    )
    return redirect(
        f"/payroll/runs/{run.id}",
        f"Đã lập bảng lương tháng {run.period_label}",
        request=request,
    )


def _run_ctx(db: Session, user: CurrentUser, run: PayrollRun) -> dict:
    t = service.totals(run)
    lines = sorted(run.lines, key=lambda ln: ln.employee.code)
    return {
        "run": run,
        "lines": lines,
        "t": t,
        "company": _company(db, user),
        "banks": list_bank_accounts(db, user.company_id),
        "prm": params.resolve(
            db, user.company_id, service.period_end(run.year, run.month)
        ),
        "contract_labels": CONTRACT_TYPES,
    }


@router.get("/payroll/runs/{run_id}")
def run_page(
    request: Request,
    run_id: int,
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    ctx = _run_ctx(db, user, run)
    ctx["breadcrumbs"] = [
        ("Bảng lương", "/payroll"),
        (f"Tháng {run.period_label}", None),
    ]
    ctx["today"] = today()
    return render(request, "payroll/run.html", ctx)


@router.post("/payroll/runs/{run_id}")
def run_save(
    request: Request,
    run_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("payroll.create")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    ctx = get_ctx(request)
    service.update_run(db, ctx, run, form)
    if form.get("action") == "post":
        if not user.can("payroll.post"):
            raise BusinessError("Bạn không có quyền ghi sổ bảng lương")
        e = service.post_run(db, ctx, run)
        return redirect(
            f"/payroll/runs/{run.id}",
            f"Đã ghi sổ bảng lương – chứng từ {e.number}",
            request=request,
        )
    return redirect(
        f"/payroll/runs/{run.id}", "Đã lưu bảng lương", request=request
    )


@router.post("/payroll/runs/{run_id}/refresh")
def run_refresh(
    request: Request,
    run_id: int,
    user: CurrentUser = Depends(require("payroll.create")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    service.refresh_employees(db, get_ctx(request), run)
    return redirect(
        f"/payroll/runs/{run.id}",
        "Đã cập nhật theo hồ sơ nhân viên",
        request=request,
    )


@router.post("/payroll/runs/{run_id}/post")
def run_post(
    request: Request,
    run_id: int,
    user: CurrentUser = Depends(require("payroll.post")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    e = service.post_run(db, get_ctx(request), run)
    return redirect(
        f"/payroll/runs/{run.id}",
        f"Đã ghi sổ bảng lương – chứng từ {e.number}",
        request=request,
    )


@router.post("/payroll/runs/{run_id}/delete")
def run_delete(
    request: Request,
    run_id: int,
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    service.delete_run(db, get_ctx(request), run)
    return redirect("/payroll", "Đã xóa bảng lương", request=request)


@router.post("/payroll/runs/{run_id}/pay")
def run_pay(
    request: Request,
    run_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("payroll.post")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    on = _date(form, "date") or today()
    e = service.create_payment(
        db,
        get_ctx(request),
        run,
        form.get("method") or "cash",
        on,
        _int(form, "bank_account_id") or None,
    )
    return redirect(
        e.url,
        f"Đã lập chứng từ trả lương nháp {format_vnd(e.total)} đồng. "
        "Kiểm tra rồi ghi sổ.",
        request=request,
    )


@router.get("/payroll/runs/{run_id}/sheet.pdf")
def run_sheet_pdf(
    run_id: int,
    user: CurrentUser = Depends(require("payroll.export")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    return pdf_response(
        render_pdf("payroll/print/sheet.html", _run_ctx(db, user, run)),
        f"bang-luong-{run.year}-{run.month:02d}.pdf",
    )


@router.get("/payroll/runs/{run_id}/payslips.pdf")
def run_payslips_pdf(
    run_id: int,
    user: CurrentUser = Depends(require("payroll.export")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    return pdf_response(
        render_pdf("payroll/print/payslips.html", _run_ctx(db, user, run)),
        f"phieu-luong-{run.year}-{run.month:02d}.pdf",
    )


SHEET_COLS = [
    ("Mã NV", 10, "text"),
    ("Họ và tên", 26, "text"),
    ("Ngày công", 9, "qty"),
    ("Lương theo công", 15, "money"),
    ("Phụ cấp chịu thuế", 14, "money"),
    ("Phụ cấp không chịu thuế", 14, "money"),
    ("Thu nhập khác", 14, "money"),
    ("Tổng thu nhập", 15, "money"),
    ("BHXH 8%", 12, "money"),
    ("BHYT 1,5%", 12, "money"),
    ("BHTN 1%", 12, "money"),
    ("Thuế TNCN", 13, "money"),
    ("Trừ tạm ứng", 13, "money"),
    ("Thực lĩnh", 15, "money"),
    ("BH, KPCĐ phần DN", 15, "money"),
]


@router.get("/payroll/runs/{run_id}/sheet.xlsx")
def run_sheet_xlsx(
    run_id: int,
    user: CurrentUser = Depends(require("payroll.export")),
    db: Session = Depends(get_tenant_db),
):
    run = service.get_run(db, user.company_id, run_id)
    c = _run_ctx(db, user, run)
    book = ReportBook()
    ws = book.add_sheet(
        "02-LDTL",
        company=c["company"],
        form_code="02-LĐTL",
        title="BẢNG THANH TOÁN TIỀN LƯƠNG",
        subtitle=f"Tháng {run.month:02d} năm {run.year}",
        columns=[Col(t, w, k) for t, w, k in SHEET_COLS],
    )
    first = None
    for ln in c["lines"]:
        idx = book.row(
            ws,
            [
                ln.employee.code,
                ln.employee.full_name,
                float(ln.days_worked),
                ln.salary_by_days,
                ln.taxable_allowance,
                ln.nontaxable_allowance,
                ln.other_income,
                None,
                ln.emp_bhxh,
                ln.emp_bhyt,
                ln.emp_bhtn,
                ln.pit,
                ln.advance_deduction,
                None,
                ln.er_insurance,
            ],
        )
        ws.cell(idx, 8).value = f"=SUM(D{idx}:G{idx})"
        ws.cell(idx, 14).value = f"=H{idx}-I{idx}-J{idx}-K{idx}-L{idx}-M{idx}"
        first = first or idx
    book.total_row(
        ws, "Cộng", sum_cols=list(range(3, 15)), label_col=2, from_row=first
    )
    book.signatures(
        ws,
        c["company"],
        left="Người lập biểu",
        middle="Kế toán trưởng",
        right="Giám đốc",
    )
    return xlsx_response(
        book.to_bytes(), f"bang-luong-{run.year}-{run.month:02d}.xlsx"
    )


@router.get("/payroll/entries/{entry_id}")
def payroll_entry(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id, doc_types=("LG",))
    run = db.scalar(
        select(PayrollRun).where(
            PayrollRun.company_id == ctx.company_id,
            or_(
                PayrollRun.entry_id == e.id,
                PayrollRun.payment_entry_id == e.id,
            ),
        )
    )
    if run is not None and run.entry_id == e.id:
        return RedirectResponse(f"/payroll/runs/{run.id}", status_code=303)
    back = f"/payroll/runs/{run.id}" if run else "/payroll"
    return render(
        request,
        "assets/entry.html",
        {
            "e": e,
            "back": back,
            "breadcrumbs": [
                ("Bảng lương", "/payroll"),
                (e.display_number, None),
            ],
        },
    )


# ------------------------------------------------------------ employees


def _employee_input(form: dict) -> EmployeeInput:
    return EmployeeInput(
        full_name=form.get("full_name") or "",
        code=form.get("code") or "",
        department=form.get("department") or "",
        position=form.get("position") or "",
        contract_type=form.get("contract_type") or "long",
        is_resident=form.get("is_resident", "1") == "1",
        dependents=_int(form, "dependents"),
        base_salary=money_field(form, "base_salary"),
        insurance_salary=money_field(form, "insurance_salary"),
        taxable_allowance=money_field(form, "taxable_allowance"),
        nontaxable_allowance=money_field(form, "nontaxable_allowance"),
        expense_account=form.get("expense_account") or "",
        start_date=_date(form, "start_date"),
        end_date=_date(form, "end_date"),
        tax_code=form.get("tax_code") or "",
        id_number=form.get("id_number") or "",
        phone=form.get("phone") or "",
        email=form.get("email") or "",
        address=form.get("address") or "",
        bank_account=form.get("bank_account") or "",
        bank_name=form.get("bank_name") or "",
        is_active=form.get("is_active", "1") == "1",
        note=form.get("note") or "",
    )


def _expense_accounts(db: Session, company_id: int) -> list[tuple[str, str]]:
    prefixes = service.salary_expense_prefixes(db.get(Company, company_id))
    return [
        (a.code, a.label)
        for a in list_accounts(
            db,
            company_id,
            postable=True,
            prefix=",".join(prefixes),
            active=True,
        )
    ]


def _employee_form_ctx(
    db: Session, user: CurrentUser, form: dict, emp, errors: dict
) -> dict:
    return {
        "form": form,
        "emp": emp,
        "errors": errors,
        "contract_types": list(CONTRACT_TYPES.items()),
        "expense_accounts": _expense_accounts(db, user.company_id),
        "breadcrumbs": [
            ("Nhân viên", "/payroll/employees"),
            (emp.full_name if emp else "Thêm mới", None),
        ],
    }


@router.get("/payroll/employees")
def employees_page(
    request: Request,
    q: str = "",
    show: str = "active",
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    rows = service.list_employees(
        db, user.company_id, active=None if show == "all" else True, q=q
    )
    return render(
        request,
        "payroll/employees.html",
        {
            "rows": rows,
            "q": q,
            "show": show,
            "contract_labels": CONTRACT_TYPES,
            "breadcrumbs": [("Lương", None), ("Nhân viên", None)],
        },
    )


@router.get("/payroll/employees/new")
def employee_new(
    request: Request,
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    form = {
        "contract_type": "long",
        "is_resident": True,
        "expense_account": regime_account(_company(db, user), "expense_staff"),
        "dependents": 0,
        "is_active": True,
    }
    return render(
        request,
        "payroll/employee_form.html",
        _employee_form_ctx(db, user, form, None, {}),
    )


@router.post("/payroll/employees")
def employee_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    try:
        e = service.create_employee(
            db, get_ctx(request), _employee_input(form)
        )
    except BusinessError as err:
        db.rollback()
        return render(
            request,
            "payroll/employee_form.html",
            _employee_form_ctx(db, user, form, None, {"_": err.message}),
            status_code=422,
        )
    return redirect(
        f"/payroll/employees/{e.id}",
        f"Đã thêm nhân viên {e.full_name}",
        request=request,
    )


def _employee_form(e) -> dict:
    p = e.partner
    form = {f: getattr(e, f) for f in service.EMP_FIELDS}
    form.update(
        {
            "full_name": p.name,
            "code": p.code,
            "tax_code": p.tax_code,
            "id_number": p.id_number,
            "phone": p.phone,
            "email": p.email,
            "address": p.address,
            "bank_account": p.bank_account,
            "bank_name": p.bank_name,
        }
    )
    for k in ("start_date", "end_date"):
        form[k] = form[k].isoformat() if form[k] else ""
    return form


@router.get("/payroll/employees/{employee_id}")
def employee_detail(
    request: Request,
    employee_id: int,
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    e = service.get_employee(db, user.company_id, employee_id)
    year = today().year
    summary = [
        s
        for s in service.year_summary(db, user.company_id, year)
        if s.employee.id == e.id
    ]
    ctx = _employee_form_ctx(db, user, _employee_form(e), e, {})
    ctx.update({"summary": summary[0] if summary else None, "year": year})
    return render(request, "payroll/employee_form.html", ctx)


@router.post("/payroll/employees/{employee_id}")
def employee_update(
    request: Request,
    employee_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    e = service.get_employee(db, user.company_id, employee_id)
    try:
        service.update_employee(db, get_ctx(request), e, _employee_input(form))
    except BusinessError as err:
        db.rollback()
        return render(
            request,
            "payroll/employee_form.html",
            _employee_form_ctx(db, user, form, e, {"_": err.message}),
            status_code=422,
        )
    return redirect(
        f"/payroll/employees/{e.id}", "Đã lưu hồ sơ nhân viên", request=request
    )


# -------------------------------------------------------------- reports


@router.get("/payroll/reports")
def reports_page(
    request: Request,
    year: int | None = None,
    month: int = 0,
    tab: str = "pit",
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    y = year or today().year
    rows = service.year_summary(db, user.company_id, y, month or None)
    tot = {
        k: sum(getattr(r, k) for r in rows)
        for k in (
            "gross",
            "taxable_income",
            "emp_insurance",
            "er_insurance",
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
        )
    }
    return render(
        request,
        "payroll/reports.html",
        {
            "rows": rows,
            "tot": tot,
            "year": y,
            "month": month,
            "tab": tab,
            "breadcrumbs": [
                ("Lương", "/payroll"),
                ("Báo cáo lương, BH, TNCN", None),
            ],
        },
    )


@router.get("/payroll/reports.xlsx")
def reports_xlsx(
    year: int,
    month: int = 0,
    tab: str = "pit",
    user: CurrentUser = Depends(require("payroll.export")),
    db: Session = Depends(get_tenant_db),
):
    rows = service.year_summary(db, user.company_id, year, month or None)
    company = _company(db, user)
    period = f"Tháng {month:02d} năm {year}" if month else f"Năm {year}"
    book = ReportBook()
    if tab == "insurance":
        cols = [
            Col("Mã NV", 10),
            Col("Họ và tên", 26),
            Col("BHXH NLĐ", 13, "money"),
            Col("BHYT NLĐ", 13, "money"),
            Col("BHTN NLĐ", 13, "money"),
            Col("BHXH DN", 13, "money"),
            Col("BHYT DN", 13, "money"),
            Col("BHTN DN", 13, "money"),
            Col("KPCĐ", 13, "money"),
            Col("Tổng", 15, "money"),
        ]
        ws = book.add_sheet(
            "Bao hiem",
            company=company,
            title="BẢNG TỔNG HỢP BẢO HIỂM, KINH PHÍ CÔNG ĐOÀN",
            subtitle=period,
            columns=cols,
        )
        first = None
        for r in rows:
            idx = book.row(
                ws,
                [
                    r.employee.code,
                    r.employee.full_name,
                    r.emp_bhxh,
                    r.emp_bhyt,
                    r.emp_bhtn,
                    r.er_bhxh,
                    r.er_bhyt,
                    r.er_bhtn,
                    r.er_kpcd,
                    None,
                ],
            )
            ws.cell(idx, 10).value = f"=SUM(C{idx}:I{idx})"
            first = first or idx
        book.total_row(
            ws,
            "Cộng",
            sum_cols=list(range(2, 10)),
            label_col=2,
            from_row=first,
        )
    else:
        cols = [
            Col("Mã NV", 10),
            Col("Họ và tên", 26),
            Col("MST", 14),
            Col("Thu nhập chịu thuế", 16, "money"),
            Col("BH bắt buộc", 14, "money"),
            Col("Giảm trừ bản thân", 15, "money"),
            Col("Giảm trừ người phụ thuộc", 15, "money"),
            Col("Thu nhập tính thuế", 16, "money"),
            Col("Thuế TNCN đã khấu trừ", 16, "money"),
        ]
        ws = book.add_sheet(
            "TNCN",
            company=company,
            title="BẢNG TỔNG HỢP THUẾ THU NHẬP CÁ NHÂN",
            subtitle=period,
            columns=cols,
        )
        first = None
        for r in rows:
            idx = book.row(
                ws,
                [
                    r.employee.code,
                    r.employee.full_name,
                    r.employee.partner.tax_code,
                    r.taxable_income,
                    r.emp_insurance,
                    r.self_deduction,
                    r.dependent_deduction,
                    r.assessable_income,
                    r.pit,
                ],
            )
            first = first or idx
        book.total_row(
            ws, "Cộng", sum_cols=list(range(3, 9)), label_col=2, from_row=first
        )
    book.signatures(ws, company)
    name = "bao-hiem" if tab == "insurance" else "tncn"
    return xlsx_response(
        book.to_bytes(),
        f"{name}-{year}{'-%02d' % month if month else ''}.xlsx",
    )


# ----------------------------------------------------------- parameters


@router.get("/payroll/settings")
def settings_page(
    request: Request,
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    groups = params.list_params(db, user.company_id)
    db.commit()
    current = params.resolve(db, user.company_id, today())
    return render(
        request,
        "payroll/settings.html",
        {
            "groups": groups,
            "labels": params.KEY_LABELS,
            "current": current,
            "today": today(),
            "breadcrumbs": [
                ("Lương", "/payroll"),
                ("Tham số lương & thuế", None),
            ],
        },
    )


@router.post("/payroll/settings")
def settings_save(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    key = form.get("key") or ""
    eff = _date(form, "effective_from")
    if eff is None:
        raise BusinessError("Nhập ngày hiệu lực")
    params.save_version(
        db,
        get_ctx(request),
        key,
        eff,
        params.parse_value(key, form),
        form.get("source") or "",
    )
    return redirect(
        f"/payroll/settings#{key}", "Đã lưu tham số", request=request
    )


@router.post("/payroll/settings/{param_id}/delete")
def settings_delete(
    request: Request,
    param_id: int,
    _u: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    params.delete_version(db, get_ctx(request), param_id)
    return redirect("/payroll/settings", "Đã xóa mức tham số", request=request)
