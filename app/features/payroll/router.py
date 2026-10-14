"""Payroll routes.

Employees, monthly payroll runs, posting, salary payment, insurance
and PIT reports, the 05/KK-TNCN withholding return, and payroll
parameters.
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
from app.core.xlsx import (
    Col,
    ReportBook,
    pdf_response,
    xlsx_response,
    xml_response,
)
from app.features.accounts.service import list_accounts
from app.features.bank.service import list_bank_accounts
from app.features.journal import service as js
from app.features.org.models import Company
from app.features.payroll import htkk, htkk_kk, params, pit_return, service
from app.features.payroll.models import (
    CONTRACT_TYPES,
    ID_TYPES,
    RELATIONSHIPS,
    PayrollRun,
)
from app.features.payroll.service import (
    EmployeeInput,
    decimal_field,
    money_field,
)
from app.features.vat import khbs
from app.features.vat.models import FORM_PIT
from app.features.vat.service import TaxPeriod

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
    deps = service.list_dependents(db, user.company_id, e.id)
    ctx.update(
        {
            "summary": summary[0] if summary else None,
            "year": year,
            "dependents": deps,
            "active_dependents": service.active_dependents(
                deps, today().year, today().month
            ),
            "relationships": list(RELATIONSHIPS.items()),
            "id_types": list(ID_TYPES.items()),
        }
    )
    return render(request, "payroll/employee_form.html", ctx)


@router.post("/payroll/employees/{employee_id}/dependents")
def dependent_add(
    request: Request,
    employee_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    e = service.get_employee(db, user.company_id, employee_id)
    d = service.add_dependent(db, get_ctx(request), e, form)
    return redirect(
        f"/payroll/employees/{e.id}#dependents",
        f"Đã thêm người phụ thuộc {d.full_name}",
        request=request,
    )


@router.post(
    "/payroll/employees/{employee_id}/dependents/{dependent_id}/delete"
)
def dependent_delete(
    request: Request,
    employee_id: int,
    dependent_id: int,
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    e = service.get_employee(db, user.company_id, employee_id)
    service.delete_dependent(db, get_ctx(request), e, dependent_id)
    return redirect(
        f"/payroll/employees/{e.id}#dependents",
        "Đã xóa người phụ thuộc",
        "warning",
        request=request,
    )


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


@router.get("/payroll/reports/qtt.xml")
def reports_qtt_xml(
    year: int,
    user: CurrentUser = Depends(require("payroll.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    data = htkk.build_xml(db, company, year)
    return xml_response(
        data, f"05QTT-TNCN-{company.tax_code or 'mst'}-{year}.xml"
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


# ------------------------------------------------ 05/KK-TNCN return


def _pit_url(p) -> str:
    return f"/payroll/pit-returns/{p.period_type}/{p.year}/{p.period}"


@router.get("/payroll/pit-returns")
def pit_returns_page(
    request: Request,
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    saved, latest = {}, {}
    for d in pit_return.list_declarations(db, company.id):
        key = (d.period_type, d.year, d.period)
        if d.version == 0:
            saved[key] = d
        if key not in latest or d.version > latest[key].version:
            latest[key] = d
    rows = []
    p = pit_return.period_of(company, today())
    for _ in range(8):
        d = saved.pop((p.period_type, p.year, p.period), None)
        rows.append({"period": p, "decl": d, "ended": p.end < today()})
        p = pit_return.previous(company, p)
    for (pt, y, n), d in sorted(saved.items(), reverse=True):
        rows.append({"period": TaxPeriod(pt, y, n), "decl": d, "ended": True})
    for r in rows:
        p = r["period"]
        r["url"] = _pit_url(p)
        r["due"] = pit_return.due_date(p)
        r["latest"] = latest.get((p.period_type, p.year, p.period))
    return render(
        request,
        "payroll/pit_returns.html",
        {
            "rows": rows,
            "breadcrumbs": [
                ("Lương", "/payroll"),
                ("Tờ khai 05/KK-TNCN", None),
            ],
        },
    )


def _pit_period(period_type: str, year: int, period: int):
    return pit_return.parse_period(period_type, year, period)


@router.get("/payroll/pit-returns/{period_type}/{year:int}/{period:int}")
def pit_return_page(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    user: CurrentUser = Depends(require("payroll.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    if period_type == "month" and 1 <= period <= 12:
        start = date(year, period, 1)
        if start >= pit_return.QUARTERLY_FROM:
            # Monthly filing ended with 03/2026: open the quarter.
            return RedirectResponse(
                _pit_url(pit_return.period_of(company, start)), 303
            )
    p = _pit_period(period_type, year, period)
    decl = pit_return.get_declaration(db, company.id, p)
    final = bool(decl and decl.status != "draft")
    rows = pit_return.collect(db, company.id, p)
    live = pit_return.compute(rows)
    v = pit_return.latest_values(db, company.id, p) if final else live
    sups = pit_return.supplements(db, company.id, p) if final else []
    pending = (
        khbs.compare(FORM_PIT, v, live, pit_return.KEYS, pit_return.LABELS)
        if final
        else []
    )
    return render(
        request,
        "payroll/pit_return.html",
        {
            "period": p,
            "prev": pit_return.previous(company, p),
            "next": pit_return.following(company, p),
            "base": _pit_url(p),
            "decl": decl,
            "final": final,
            "v": v,
            "changed": final and v != live,
            "supplements": sups,
            "pending": pending,
            "today": today(),
            "rows": rows,
            "labels": pit_return.LABELS,
            "layout": pit_return.LAYOUT,
            "counts": pit_return.COUNTS,
            "unposted": pit_return.unposted_months(db, company.id, p),
            "due": pit_return.due_date(p),
            "company": company,
            "breadcrumbs": [
                ("Lương", "/payroll"),
                ("Tờ khai 05/KK-TNCN", "/payroll/pit-returns"),
                (p.label, None),
            ],
        },
    )


@router.post(
    "/payroll/pit-returns/{period_type}/{year:int}/{period:int}/final"
)
def pit_return_final(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    p = _pit_period(period_type, year, period)
    pit_return.finalize(db, get_ctx(request), p)
    return redirect(
        _pit_url(p), f"Đã chốt tờ khai 05/KK-TNCN {p.label}", request=request
    )


@router.post(
    "/payroll/pit-returns/{period_type}/{year:int}/{period:int}/reopen"
)
def pit_return_reopen(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    p = _pit_period(period_type, year, period)
    pit_return.reopen(db, get_ctx(request), p)
    return redirect(
        _pit_url(p),
        f"Đã mở lại tờ khai 05/KK-TNCN {p.label}",
        "warning",
        request=request,
    )


@router.post(
    "/payroll/pit-returns/{period_type}/{year:int}/{period:int}/supplements"
)
def pit_return_supplement(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    p = _pit_period(period_type, year, period)
    decl = pit_return.supplement(
        db,
        get_ctx(request),
        p,
        reason=form.get("reason") or "",
        until=_date(form, "until") or today(),
    )
    return redirect(
        _pit_url(p),
        f"Đã lập tờ khai bổ sung lần {decl.version}",
        request=request,
    )


@router.post(
    "/payroll/pit-returns/{period_type}/{year:int}/{period:int}"
    "/supplements/{version:int}/delete"
)
def pit_return_supplement_delete(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    version: int,
    user: CurrentUser = Depends(require("payroll.manage")),
    db: Session = Depends(get_tenant_db),
):
    p = _pit_period(period_type, year, period)
    khbs.delete_latest(db, get_ctx(request), FORM_PIT, p, version)
    return redirect(
        _pit_url(p),
        f"Đã xóa tờ khai bổ sung lần {version}",
        "warning",
        request=request,
    )


@router.get("/payroll/pit-returns/{period_type}/{year:int}/{period:int}.xml")
def pit_return_xml(
    period_type: str,
    year: int,
    period: int,
    version: int = 0,
    user: CurrentUser = Depends(require("payroll.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p = _pit_period(period_type, year, period)
    v, sup = pit_return.export(db, company.id, p, version)
    appendices = khbs.appendices_xml(company, htkk_kk.FORM, sup) if sup else ""
    return xml_response(
        htkk_kk.build_xml(
            company, p, v, version=version, appendices=appendices
        ),
        f"05KK-TNCN-{company.tax_code or 'mst'}-{p.year}-"
        f"{p.period_type[0].upper()}{p.period:02d}"
        + (f"-BS{version}" if version else "")
        + ".xml",
    )


@router.get("/payroll/pit-returns/{period_type}/{year:int}/{period:int}.pdf")
def pit_return_pdf(
    period_type: str,
    year: int,
    period: int,
    version: int = 0,
    user: CurrentUser = Depends(require("payroll.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p = _pit_period(period_type, year, period)
    v, sup = pit_return.export(db, company.id, p, version)
    pdf = render_pdf(
        "payroll/print/pit_kk.html",
        {
            "company": company,
            "period": p,
            "v": v,
            "labels": pit_return.LABELS,
            "layout": pit_return.LAYOUT,
            "counts": pit_return.COUNTS,
            "sup": sup,
            "form_name": "Tờ khai khấu trừ thuế TNCN (05/KK-TNCN)",
        },
    )
    return pdf_response(
        pdf,
        f"to-khai-05KK-TNCN-{p.year}-{p.period}"
        + (f"-BS{version}" if version else "")
        + ".pdf",
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
