"""gl module routes: vouchers, opening balances, closing and reports.

Covers shared vouchers, general journal vouchers (PK), opening
balances, period locks, period-end closing, books and financial
statements.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dates import parse_date, today
from app.core.errors import BusinessError, NotFoundError, PermissionDeniedError
from app.core.money import MoneyError, parse_vnd
from app.core.regimes import form_for, regime_of
from app.core.registry import can_entry, doc_url
from app.core.security import CurrentUser, require
from app.core.web import (
    form_data,
    form_multi,
    get_ctx,
    get_tenant_db,
    is_htmx,
    redirect,
    render,
)
from app.core.xlsx import pdf_response, xlsx_response, xml_response
from app.features.bank.models import BankAccount
from app.features.gl import (
    books,
    closing,
    entries,
    exports,
    htkk,
    notes,
    opening_io,
    periods,
    statements,
    statements_tt99,
)
from app.features.journal import locks, opening
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.org.models import Company
from app.features.partners.models import Partner

router = APIRouter()

BOOKS = ("Sổ sách & báo cáo", None)


def _company(db: Session, user: CurrentUser) -> Company:
    return db.get(Company, user.company_id)


def _years(company: Company) -> list[int]:
    return list(range(today().year + 1, company.start_date.year - 1, -1))


# ============================================================ vouchers


@router.get("/entries")
def entries_page(
    request: Request,
    user: CurrentUser = Depends(require("journal.view")),
    db: Session = Depends(get_tenant_db),
):
    f = entries.EntryFilters.from_params(request.query_params)
    rows, total, amount = entries.list_entries(db, user.company_id, f)
    pages = max(1, (total + entries.PAGE - 1) // entries.PAGE)
    partner = db.get(Partner, f.partner_id) if f.partner_id else None
    ctx = {
        "rows": rows,
        "total": total,
        "amount": amount,
        "f": f,
        "pages": pages,
        "counts": entries.status_counts(db, user.company_id, f),
        "doc_types": entries.doc_type_options(),
        "partner_label": partner.label
        if partner and partner.company_id == user.company_id
        else "",
        "breadcrumbs": [BOOKS, ("Chứng từ", None)],
    }
    if (
        is_htmx(request)
        and request.headers.get("hx-target") == "entry-results"
    ):
        return render(request, "gl/_entry_rows.html", ctx)
    return render(request, "gl/entries.html", ctx)


@router.get("/entries/export.xlsx")
def entries_export(
    request: Request,
    user: CurrentUser = Depends(require("journal.export")),
    db: Session = Depends(get_tenant_db),
):
    f = entries.EntryFilters.from_params(request.query_params)
    from sqlalchemy import select as sel

    rows = list(
        db.scalars(
            sel(JournalEntry)
            .where(entries._filtered(user.company_id, f))
            .order_by(JournalEntry.date, JournalEntry.id)
            .limit(20000)
        )
    )
    names = entries.names_for(db, user.company_id, rows)
    data = exports.xlsx_entries(_company(db, user), rows, names)
    return xlsx_response(data, "chung-tu.xlsx")


def _pk_page(
    request: Request,
    db: Session,
    *,
    values: dict,
    lines: list[dict],
    errors: dict,
    entry=None,
    vat=None,
    status_code: int = 200,
):
    banks = [
        {"value": b.id, "label": b.label}
        for b in db.scalars(
            select(BankAccount).where(
                BankAccount.company_id == get_ctx(request).company_id,
                BankAccount.is_active,
            )
        )
    ]
    return render(
        request,
        "gl/pk_form.html",
        {
            "values": values,
            "lines": lines,
            "errors": errors,
            "entry": entry,
            "vat": vat or {},
            "bank_items": banks,
            "breadcrumbs": [
                BOOKS,
                ("Chứng từ", "/entries"),
                ("Phiếu kế toán", None),
            ],
        },
        status_code=status_code,
    )


@router.get("/entries/new")
def pk_new(
    request: Request,
    copy: int | None = None,
    _u: CurrentUser = Depends(require("journal.create")),
    db: Session = Depends(get_tenant_db),
):
    values = {
        "date": today().isoformat(),
        "doc_date": today().isoformat(),
        "description": "",
        "ref_no": "",
        "attached_docs": "",
        "contact_name": "",
    }
    lines: list[dict] = []
    if copy:
        src = js.get_entry(db, get_ctx(request), copy)
        values.update({"description": src.description, "ref_no": src.ref_no})
        lines = entries.lines_from_entry(db, src)
    return _pk_page(request, db, values=values, lines=lines, errors={})


def _vat_values(form) -> dict:
    keys = (
        "has_vat",
        "vat_direction",
        "vat_invoice_series",
        "vat_invoice_no",
        "vat_invoice_date",
        "vat_partner_name",
        "vat_partner_tax_code",
        "vat_goods_amount",
        "vat_rate",
        "vat_amount",
    )
    return {k: (form.get(k) or "") for k in keys}


def _vat_from_entry(e: JournalEntry) -> dict:
    if not e.vat_records:
        return {}
    v = e.vat_records[0]
    return {
        "has_vat": "1",
        "vat_direction": v.direction,
        "vat_invoice_series": v.invoice_series,
        "vat_invoice_no": v.invoice_no,
        "vat_invoice_date": v.invoice_date.isoformat()
        if v.invoice_date
        else "",
        "vat_partner_name": v.partner_name,
        "vat_partner_tax_code": v.partner_tax_code,
        "vat_goods_amount": v.goods_amount,
        "vat_rate": v.vat_rate,
        "vat_amount": v.vat_amount,
    }


@router.post("/entries")
def pk_create(
    request: Request,
    form=Depends(form_multi),
    _u: CurrentUser = Depends(require("journal.create")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    parsed = entries.parse_pk_form(db, ctx.company_id, form)
    action = form.get("action") or "save"
    if parsed.inp is None:
        return _pk_page(
            request,
            db,
            values=parsed.values,
            lines=parsed.lines,
            errors=parsed.errors,
            vat=_vat_values(form),
            status_code=422,
        )
    if action == "post" and not ctx.can("journal.post"):
        raise PermissionDeniedError()
    try:
        e = js.create_entry(
            db, ctx, parsed.inp, allow_incomplete=action != "post"
        )
    except BusinessError as err:
        db.rollback()
        return _pk_page(
            request,
            db,
            values=parsed.values,
            lines=parsed.lines,
            errors={
                "lines": "; ".join(err.details.get("errors", [err.message]))
            },
            vat=_vat_values(form),
            status_code=422,
        )
    if action == "post":
        try:
            js.post_entry(db, ctx, e)
        except BusinessError as err:
            return redirect(
                f"/entries/{e.id}",
                f"Đã lưu nháp nhưng chưa ghi sổ được: {err.message}",
                "error",
            )
        return redirect(f"/entries/{e.id}", f"Đã ghi sổ {e.number}")
    return redirect(f"/entries/{e.id}", "Đã lưu phiếu kế toán nháp")


@router.get("/entries/{entry_id}")
def entry_detail(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("company.view")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id)
    url = doc_url(e.doc_type, e.id)
    if url != f"/entries/{e.id}":
        return RedirectResponse(url, status_code=303)
    if not can_entry(user, e.doc_type, "view"):
        raise PermissionDeniedError()
    data = entries.entry_context(db, ctx, e)
    data["breadcrumbs"] = [
        BOOKS,
        ("Chứng từ", "/entries"),
        (e.display_number, None),
    ]
    data["editable"] = (
        e.is_draft and e.doc_type == "PK" and data["perm"]["create"]
    )
    return render(request, "gl/entry_detail.html", data)


@router.get("/entries/{entry_id}/edit")
def pk_edit(
    request: Request,
    entry_id: int,
    _u: CurrentUser = Depends(require("journal.create")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id)
    if doc_url(e.doc_type, e.id) != f"/entries/{e.id}" or e.doc_type != "PK":
        return RedirectResponse(doc_url(e.doc_type, e.id), status_code=303)
    if not e.is_draft:
        return redirect(
            f"/entries/{e.id}",
            "Chứng từ đã ghi sổ không sửa được số liệu",
            "warning",
        )
    return _pk_page(
        request,
        db,
        values=entries.entry_values(e),
        lines=entries.lines_from_entry(db, e),
        errors={},
        entry=e,
        vat=_vat_from_entry(e),
    )


@router.post("/entries/{entry_id}/edit")
def pk_update(
    request: Request,
    entry_id: int,
    form=Depends(form_multi),
    _u: CurrentUser = Depends(require("journal.create")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id, doc_types=("PK",))
    parsed = entries.parse_pk_form(db, ctx.company_id, form)
    action = form.get("action") or "save"
    if parsed.inp is None:
        return _pk_page(
            request,
            db,
            values=parsed.values,
            lines=parsed.lines,
            errors=parsed.errors,
            entry=e,
            vat=_vat_values(form),
            status_code=422,
        )
    if action == "post" and not ctx.can("journal.post"):
        raise PermissionDeniedError()
    try:
        js.update_draft(
            db, ctx, e, parsed.inp, allow_incomplete=action != "post"
        )
    except BusinessError as err:
        db.rollback()
        return _pk_page(
            request,
            db,
            values=parsed.values,
            lines=parsed.lines,
            entry=e,
            vat=_vat_values(form),
            errors={
                "lines": "; ".join(err.details.get("errors", [err.message]))
            },
            status_code=422,
        )
    if action == "post":
        try:
            js.post_entry(db, ctx, e)
        except BusinessError as err:
            return redirect(
                f"/entries/{e.id}",
                f"Đã lưu nháp nhưng chưa ghi sổ được: {err.message}",
                "error",
            )
        return redirect(f"/entries/{e.id}", f"Đã ghi sổ {e.number}")
    return redirect(f"/entries/{e.id}", "Đã lưu thay đổi")


@router.get("/entries/{entry_id}/print.pdf")
def entry_print(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("company.view")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id)
    if not can_entry(user, e.doc_type, "view"):
        raise PermissionDeniedError()
    names = entries.names_for(db, ctx.company_id, [e])
    from app.features.auth.models import User

    creator = db.get(User, e.created_by) if e.created_by else None
    data = exports.pdf(
        "pk",
        company=_company(db, user),
        entry=e,
        names=names,
        creator=creator.full_name if creator else "",
    )
    return pdf_response(data, f"{e.number or 'nhap-' + str(e.id)}.pdf")


# ==================================================== opening balances


def _opening_ctx(db: Session, user: CurrentUser, **extra) -> dict:
    lines, bank_values = opening_io.grid_lines(db, user.company_id)
    banks = list(
        db.scalars(
            select(BankAccount)
            .where(BankAccount.company_id == user.company_id)
            .order_by(BankAccount.id)
        )
    )
    d, c = opening.totals(db, user.company_id)
    company = _company(db, user)
    ctx = {
        "lines": lines,
        "banks": banks,
        "bank_values": bank_values,
        "total_debit": d,
        "total_credit": c,
        "locked": locks.any_locked(db, user.company_id),
        "opening_date": periods.day_before(company.start_date),
        "can_edit": user.can("opening.manage"),
        "errors": [],
        "breadcrumbs": [("Danh mục", None), ("Số dư đầu kỳ", None)],
    }
    ctx.update(extra)
    return ctx


@router.get("/opening")
def opening_page(
    request: Request,
    user: CurrentUser = Depends(require("journal.view")),
    db: Session = Depends(get_tenant_db),
):
    return render(request, "gl/opening.html", _opening_ctx(db, user))


@router.post("/opening")
def opening_save(
    request: Request,
    form=Depends(form_multi),
    user: CurrentUser = Depends(require("opening.manage")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    try:
        rows = opening_io.parse_form(db, ctx.company_id, form)
        opening.replace_opening(db, ctx, rows)
    except BusinessError as err:
        db.rollback()
        errs = err.details.get("errors") or [err.message]
        return render(
            request,
            "gl/opening.html",
            _opening_ctx(db, user, errors=errs),
            status_code=422,
        )
    d, c = opening.totals(db, ctx.company_id)
    if d != c:
        return redirect(
            "/opening",
            (
                f"Đã lưu. Tổng dư Nợ và dư Có đang lệch {abs(d - c):,} đồng"
            ).replace(",", "."),
            "warning",
        )
    return redirect("/opening", "Đã lưu số dư đầu kỳ")


@router.get("/opening/template.xlsx")
def opening_template(
    user: CurrentUser = Depends(require("journal.view")),
    db: Session = Depends(get_tenant_db),
):
    return xlsx_response(
        opening_io.template_xlsx(db, user.company_id), "so-du-dau-ky.xlsx"
    )


@router.post("/opening/import")
async def opening_import(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require("opening.manage")),
    db: Session = Depends(get_tenant_db),
):
    data = await file.read()
    if not data:
        raise BusinessError("Chọn file Excel để nhập")
    rows = opening_io.parse_xlsx(db, user.company_id, data)
    token = opening_io.save_preview(user.company_id, rows)
    ok = [r for r in rows if not r.error]
    return render(
        request,
        "gl/opening_preview.html",
        {
            "rows": rows,
            "token": token,
            "errors": len(rows) - len(ok),
            "filename": file.filename,
            "total_debit": sum(r.debit for r in ok),
            "total_credit": sum(r.credit for r in ok),
            "breadcrumbs": [
                ("Danh mục", None),
                ("Số dư đầu kỳ", "/opening"),
                ("Nhập từ Excel", None),
            ],
        },
    )


@router.post("/opening/import/confirm")
def opening_import_confirm(
    request: Request,
    form=Depends(form_multi),
    _u: CurrentUser = Depends(require("opening.manage")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    token = form.get("token") or ""
    if form.get("action") == "discard":
        opening_io.discard_preview(ctx.company_id, token)
        return redirect("/opening", "Đã hủy nhập file", "info")
    rows = opening_io.load_preview(ctx.company_id, token)
    if any(r.error for r in rows):
        raise BusinessError("File còn dòng lỗi – sửa file rồi nhập lại")
    opening.replace_opening(db, ctx, opening_io.to_opening(rows))
    opening_io.discard_preview(ctx.company_id, token)
    return redirect("/opening", f"Đã nhập {len(rows)} dòng số dư đầu kỳ")


# ======================================================== period locks


@router.get("/locks")
def locks_page(
    request: Request,
    year: int | None = None,
    user: CurrentUser = Depends(require("journal.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    y = year or today().year
    years = [yy for yy in _years(company) if yy <= today().year]
    if y not in years:
        y = years[0] if years else today().year
    cells = locks.year_grid(db, company, y)
    locked = locks.locked_set(db, company.id)
    last = locks.last_locked_month(db, company)
    if last is None:
        nxt = (company.start_date.year, company.start_date.month)
    else:
        nxt = (last[0], last[1] + 1) if last[1] < 12 else (last[0] + 1, 1)
    next_lock = (
        nxt
        if date(nxt[0], nxt[1], 1) < date(today().year, today().month, 1)
        else None
    )
    return render(
        request,
        "gl/locks.html",
        {
            "next_lock": next_lock,
            "year": y,
            "years": years,
            "cells": cells,
            "year_locked": (y, 0) in locked,
            "can_lock_year": (y, 0) not in locked
            and date(y, 12, 31) < today()
            and all(c.locked or c.before_start or c.can_lock for c in cells)
            and user.can("locks.manage"),
            "last": last,
            "can_manage": user.can("locks.manage"),
            "breadcrumbs": [BOOKS, ("Khóa sổ", None)],
        },
    )


def _int_field(form, key: str) -> int:
    try:
        return int(form.get(key) or "")
    except ValueError as e:
        raise BusinessError("Kỳ không hợp lệ") from e


@router.post("/locks/lock")
def locks_lock(
    request: Request,
    form=Depends(form_multi),
    _u: CurrentUser = Depends(require("locks.manage")),
    db: Session = Depends(get_tenant_db),
):
    y, m = _int_field(form, "year"), _int_field(form, "month")
    locks.lock_period(db, get_ctx(request), y, m)
    return redirect(
        f"/locks?year={y}",
        f"Đã khóa sổ {'năm ' + str(y) if m == 0 else f'tháng {m:02d}/{y}'}",
        request=request,
    )


@router.post("/locks/unlock")
def locks_unlock(
    request: Request,
    form=Depends(form_multi),
    _u: CurrentUser = Depends(require("locks.manage")),
    db: Session = Depends(get_tenant_db),
):
    y, m = _int_field(form, "year"), _int_field(form, "month")
    locks.unlock_period(db, get_ctx(request), y, m, form.get("reason") or "")
    return redirect(
        f"/locks?year={y}",
        f"Đã mở khóa {'năm ' + str(y) if m == 0 else f'tháng {m:02d}/{y}'}",
        "warning",
        request=request,
    )


# ============================================================= closing


@router.get("/closing")
def closing_page(
    request: Request,
    user: CurrentUser = Depends(require("closing.run")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    if "mode" in request.query_params:
        period = periods.resolve(request.query_params, default_mode="month")
    else:  # default: previous month
        t = today()
        period = periods.make(
            "month",
            t.year - (1 if t.month == 1 else 0),
            month=12 if t.month == 1 else t.month - 1,
        )
    plan = closing.plan(db, company.id, period)
    ry = period.end.year - 1 if period.end.month < 12 else period.end.year
    return render(
        request,
        "gl/closing.html",
        {
            "period": period,
            "plan": plan,
            "years": _years(company),
            "history": closing.history(db, company.id),
            "retained": closing.retained_plan(db, company.id, ry)
            if ry >= company.start_date.year
            else None,
            "period_locked": locks.is_locked(db, company.id, period.end),
            "breadcrumbs": [BOOKS, ("Kết chuyển cuối kỳ", None)],
        },
    )


@router.post("/closing/run")
def closing_run(
    request: Request,
    form=Depends(form_multi),
    _u: CurrentUser = Depends(require("closing.run")),
    db: Session = Depends(get_tenant_db),
):
    period = periods.resolve(form, default_mode="month")
    e = closing.run(db, get_ctx(request), period)
    return redirect(
        f"/closing?{period.query()}",
        f"Đã kết chuyển {period.label.lower()} ({e.number})",
    )


@router.post("/closing/retained")
def closing_retained(
    request: Request,
    form=Depends(form_multi),
    _u: CurrentUser = Depends(require("closing.run")),
    db: Session = Depends(get_tenant_db),
):
    y = _int_field(form, "year")
    e = closing.run_retained(db, get_ctx(request), y)
    return redirect(
        "/closing", f"Đã kết chuyển lợi nhuận năm {y} ({e.number})"
    )


@router.post("/closing/cit")
def closing_cit(
    request: Request,
    form=Depends(form_multi),
    _u: CurrentUser = Depends(require("closing.run")),
    db: Session = Depends(get_tenant_db),
):
    try:
        on = parse_date(form.get("date")) or today()
        amount = parse_vnd(form.get("amount") or "")
    except (ValueError, MoneyError) as e:
        raise BusinessError("Ngày hoặc số tiền không hợp lệ") from e
    e = closing.record_cit(db, get_ctx(request), on, amount)
    return redirect(
        request.headers.get("referer", "/closing").split(
            request.url.netloc, 1
        )[-1]
        or "/closing",
        f"Đã ghi nhận chi phí thuế TNDN ({e.number})",
    )


# =================================================== books and reports


def _report_ctx(
    company: Company,
    period: periods.ReportPeriod,
    title: str,
    crumb: str,
    **extra,
) -> dict:
    ctx = {
        "period": period,
        "years": _years(company),
        "report_title": title,
        "date_label": exports.date_label(),
        "breadcrumbs": [BOOKS, (crumb, None)],
    }
    ctx.update(extra)
    return ctx


def _fmt(request: Request) -> str:
    path = request.url.path
    return (
        "pdf"
        if path.endswith(".pdf")
        else "xlsx"
        if path.endswith(".xlsx")
        else "html"
    )


def _file(company: Company, code: str, ext: str, suffix: str = "") -> str:
    """Download name: the form code of the company's regime."""
    name = form_for(company, code) or code.split("-")[0].lower()
    return f"{name}{suffix}.{ext}"


def _check_export(user: CurrentUser, fmt: str) -> None:
    if fmt != "html" and not user.can("reports.export"):
        raise PermissionDeniedError()


@router.get("/reports")
def reports_hub(
    request: Request,
    user: CurrentUser = Depends(require("reports.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    period = periods.resolve(request.query_params, default_mode="year")
    ss = statements.statement_set(company)
    st = ss.builders["b01a"](db, company, period)
    pl = ss.builders["b02"](db, company, period)
    tb = books.trial_balance(db, company.id, period.start, period.end, level=1)
    unclosed = bool(books.pl_balances(db, company.id, period.end))
    descriptions = {
        "b01a": (
            "Tài sản, nợ phải trả và vốn chủ sở hữu tại ngày cuối kỳ",
            "scale",
        ),
        "b02": ("Doanh thu, chi phí và lợi nhuận trong kỳ", "trending-up"),
        "b03": ("Dòng tiền theo phương pháp trực tiếp", "arrow-left-right"),
    }
    cards = [
        {
            "key": kind,
            "title": title,
            "code": code,
            "desc": descriptions[kind][0],
            "icon": descriptions[kind][1],
        }
        for kind, (title, code) in ss.titles.items()
    ]
    if ss.notes_code:
        cards.append(
            {
                "key": "b09",
                "title": "Bản thuyết minh báo cáo tài chính",
                "code": ss.notes_code,
                "desc": "Chính sách kế toán và chi tiết các khoản mục",
                "icon": "file-text",
            }
        )
    tb_title, tb_code = ss.trial_balance
    cards.append(
        {
            "key": "f01",
            "title": tb_title,
            "code": tb_code,
            "desc": "Số dư đầu kỳ, phát sinh và số dư cuối kỳ "
            "của các tài khoản",
            "icon": "table",
        }
    )
    return render(
        request,
        "gl/reports.html",
        _report_ctx(
            company,
            period,
            "Báo cáo tài chính",
            "Báo cáo tài chính",
            b01a=st,
            b02=pl,
            lines=ss.lines,
            cards=cards,
            regime=regime_of(company),
            tb=tb,
            unclosed=unclosed,
        ),
    )


@router.get("/reports/s03a")
@router.get("/reports/s03a.pdf")
@router.get("/reports/s03a.xlsx")
def report_s03a(
    request: Request,
    user: CurrentUser = Depends(require("reports.view")),
    db: Session = Depends(get_tenant_db),
):
    fmt = _fmt(request)
    _check_export(user, fmt)
    company = _company(db, user)
    period = periods.resolve(request.query_params, default_mode="month")
    book = books.general_journal(
        db,
        company.id,
        period.start,
        period.end,
        mask_payroll=not user.can("payroll.view"),
    )
    if fmt == "pdf":
        return pdf_response(
            exports.pdf(
                "s03a", company=company, book=book, subtitle=period.label
            ),
            _file(company, "S03a-DNN", "pdf"),
        )
    if fmt == "xlsx":
        return xlsx_response(
            exports.xlsx_journal(company, book, period.label),
            _file(company, "S03a-DNN", "xlsx"),
        )
    limit = 3000
    return render(
        request,
        "gl/report_s03a.html",
        _report_ctx(
            company,
            period,
            "Sổ Nhật ký chung",
            "Sổ Nhật ký chung",
            book=book,
            rows=book.rows[:limit],
            truncated=len(book.rows) > limit,
        ),
    )


# Accounts whose per-employee detail ledger reveals salary, net pay or
# advances -> requires payroll.view.
SALARY_ACCOUNTS = ("334", "3335", "141")


def _ledger_params(request: Request, db: Session, company_id: int):
    q = request.query_params
    account = (q.get("account") or "").strip()
    partner_id = (
        int(q["partner_id"]) if (q.get("partner_id") or "").isdigit() else None
    )
    bank_id = (
        int(q["bank_account_id"])
        if (q.get("bank_account_id") or "").isdigit()
        else None
    )
    partner = db.get(Partner, partner_id) if partner_id else None
    if partner is not None and partner.company_id != company_id:
        raise NotFoundError()
    bank = db.get(BankAccount, bank_id) if bank_id else None
    if bank is not None and bank.company_id != company_id:
        raise NotFoundError()
    return account, partner, bank


@router.get("/reports/s03b")
@router.get("/reports/s03b.pdf")
@router.get("/reports/s03b.xlsx")
def report_s03b(
    request: Request,
    user: CurrentUser = Depends(require("reports.view")),
    db: Session = Depends(get_tenant_db),
):
    fmt = _fmt(request)
    _check_export(user, fmt)
    company = _company(db, user)
    period = periods.resolve(request.query_params, default_mode="month")
    account, _p, _b = _ledger_params(request, db, company.id)
    account = account or "111"
    accs = books.accounts_map(db, company.id)
    if account not in accs:
        raise BusinessError(f"Không có tài khoản {account}")
    book = books.ledger_book(
        db,
        company.id,
        account,
        period.start,
        period.end,
        with_nkc=True,
        mask_payroll=not user.can("payroll.view"),
    )
    if fmt == "pdf":
        return pdf_response(
            exports.pdf(
                "s03b",
                company=company,
                book=book,
                subtitle=period.label,
                detailed=False,
                title="SỔ CÁI",
                form_code="S03b-DNN",
            ),
            _file(company, "S03b-DNN", "pdf", f"-{account}"),
        )
    if fmt == "xlsx":
        return xlsx_response(
            exports.xlsx_ledger(
                company,
                book,
                period.label,
                form_code="S03b-DNN",
                title="SỔ CÁI",
            ),
            _file(company, "S03b-DNN", "xlsx", f"-{account}"),
        )
    level1 = [a for a in accs.values() if a.level == 1 and a.is_active]
    return render(
        request,
        "gl/report_ledger.html",
        _report_ctx(
            company,
            period,
            "Sổ Cái",
            "Sổ Cái",
            book=book,
            account=account,
            kind="s03b",
            account_items=[
                {"value": a.code, "label": f"{a.code} – {a.name}"}
                for a in sorted(level1, key=lambda a: a.code)
            ],
            account_label=f"{account} – {accs[account].name}",
        ),
    )


@router.get("/reports/ledger")
@router.get("/reports/ledger.pdf")
@router.get("/reports/ledger.xlsx")
def report_ledger(
    request: Request,
    user: CurrentUser = Depends(require("reports.view")),
    db: Session = Depends(get_tenant_db),
):
    fmt = _fmt(request)
    _check_export(user, fmt)
    company = _company(db, user)
    period = periods.resolve(request.query_params, default_mode="month")
    account, partner, bank = _ledger_params(request, db, company.id)
    account = account or "1111"
    accs = books.accounts_map(db, company.id)
    if account not in accs:
        raise BusinessError(f"Không có tài khoản {account}")
    can_payroll = user.can("payroll.view")
    if (
        partner is not None
        and partner.is_employee
        and not can_payroll
        and account.startswith(SALARY_ACCOUNTS)
    ):
        raise PermissionDeniedError(
            "Cần quyền xem lương để xem sổ chi tiết của nhân viên"
        )
    book = books.ledger_book(
        db,
        company.id,
        account,
        period.start,
        period.end,
        partner_id=partner.id if partner else None,
        bank_account_id=bank.id if bank else None,
        mask_payroll=not can_payroll,
    )
    book.partner_name = partner.name if partner else None
    book.bank_label = bank.label if bank else None
    title = "SỔ CHI TIẾT TÀI KHOẢN"
    if fmt == "pdf":
        return pdf_response(
            exports.pdf(
                "s03b",
                company=company,
                book=book,
                subtitle=period.label,
                detailed=True,
                title=title,
                form_code=None,
            ),
            f"so-chi-tiet-{account}.pdf",
        )
    if fmt == "xlsx":
        return xlsx_response(
            exports.xlsx_ledger(
                company, book, period.label, form_code=None, title=title
            ),
            f"so-chi-tiet-{account}.xlsx",
        )
    return render(
        request,
        "gl/report_ledger.html",
        _report_ctx(
            company,
            period,
            "Sổ chi tiết tài khoản",
            "Sổ chi tiết tài khoản",
            book=book,
            account=account,
            kind="ledger",
            partner=partner,
            bank=bank,
            account_label=f"{account} – {accs[account].name}",
            banks=list(
                db.scalars(
                    select(BankAccount).where(
                        BankAccount.company_id == company.id
                    )
                )
            ),
        ),
    )


@router.get("/reports/f01")
@router.get("/reports/f01.pdf")
@router.get("/reports/f01.xlsx")
def report_f01(
    request: Request,
    level: int = 2,
    show_zero: int = 0,
    user: CurrentUser = Depends(require("reports.view")),
    db: Session = Depends(get_tenant_db),
):
    fmt = _fmt(request)
    _check_export(user, fmt)
    company = _company(db, user)
    period = periods.resolve(request.query_params, default_mode="year")
    level = 1 if level == 1 else 3 if level >= 3 else 2
    tb = books.trial_balance(
        db,
        company.id,
        period.start,
        period.end,
        level=level,
        show_zero=bool(show_zero),
    )
    if fmt == "pdf":
        return pdf_response(
            exports.pdf(
                "f01",
                company=company,
                tb=tb,
                subtitle=period.label,
                tb_title=statements.statement_set(company).trial_balance[0],
            ),
            _file(company, "F01-DNN", "pdf"),
        )
    if fmt == "xlsx":
        return xlsx_response(
            exports.xlsx_trial_balance(company, tb, period.label),
            _file(company, "F01-DNN", "xlsx"),
        )
    tb_title = statements.statement_set(company).trial_balance[0]
    return render(
        request,
        "gl/report_f01.html",
        _report_ctx(
            company,
            period,
            tb_title,
            tb_title,
            tb=tb,
            level=level,
            show_zero=bool(show_zero),
        ),
    )


def _statement_route(kind: str):
    def view(
        request: Request,
        user: CurrentUser = Depends(require("reports.view")),
        db: Session = Depends(get_tenant_db),
    ):
        fmt = _fmt(request)
        _check_export(user, fmt)
        company = _company(db, user)
        period = periods.resolve(request.query_params, default_mode="year")
        ss = statements.statement_set(company)
        if kind not in ss.builders:
            raise NotFoundError(
                f"Chế độ kế toán {regime_of(company).short} "
                "không có báo cáo này"
            )
        st = ss.builders[kind](db, company, period)
        subtitle = (
            f"Tại ngày {period.end:%d/%m/%Y}"
            if kind == "b01a"
            else period.label
        )
        if fmt == "pdf":
            return pdf_response(
                exports.pdf(
                    "statement", company=company, st=st, subtitle=subtitle
                ),
                f"{st.code}.pdf",
            )
        if fmt == "xlsx":
            return xlsx_response(
                exports.xlsx_statement(company, st, subtitle),
                f"{st.code}.xlsx",
            )
        title, _code = ss.titles[kind]
        return render(
            request,
            "gl/report_statement.html",
            _report_ctx(
                company,
                period,
                title,
                title,
                st=st,
                kind=kind,
                subtitle=subtitle,
                regime=regime_of(company),
                lines=ss.lines,
                tabs=[(k, code) for k, (_t, code) in ss.titles.items()]
                + ([("b09", ss.notes_code)] if ss.notes_code else []),
                terms=statements_tt99.term_settings(company),
            ),
        )

    return view


for _kind in ("b01a", "b02", "b03"):
    _v = _statement_route(_kind)
    router.add_api_route(
        f"/reports/{_kind}", _v, methods=["GET"], name=f"report_{_kind}"
    )
    router.add_api_route(
        f"/reports/{_kind}.pdf",
        _v,
        methods=["GET"],
        name=f"report_{_kind}_pdf",
    )
    router.add_api_route(
        f"/reports/{_kind}.xlsx",
        _v,
        methods=["GET"],
        name=f"report_{_kind}_xlsx",
    )


@router.post("/reports/b01-terms")
def report_b01_terms(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("settings.manage")),
    db: Session = Depends(get_tenant_db),
):
    """Accounts presented as long-term / short-term on B01-DN (TT99)."""
    from app.features.org import service as org

    def codes(name: str) -> list[str]:
        out = []
        for part in (form.get(name) or "").replace(";", ",").split(","):
            code = part.strip()
            if code:
                if not code.isdigit() or len(code) > 10:
                    raise BusinessError(
                        f"Số hiệu tài khoản không hợp lệ: {code}"
                    )
                out.append(code)
        return out

    org.update_company_settings(
        db,
        get_ctx(request),
        "b01",
        {"long_term": codes("long_term"), "short_term": codes("short_term")},
    )
    return redirect(
        "/reports/b01a?" + (form.get("query") or "mode=year"),
        "Đã lưu cách trình bày ngắn hạn/dài hạn",
    )


@router.get("/reports/bctc.xml")
def report_bctc_xml(
    request: Request,
    year: int,
    user: CurrentUser = Depends(require("reports.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    if (company.regime or "tt133") != "tt133":
        raise BusinessError(
            "XML bộ báo cáo tài chính hiện chỉ có cho chế độ Thông tư 133"
        )
    data = htkk.build_xml(db, company, year)
    return xml_response(data, f"BCTC-{company.tax_code or 'mst'}-{year}.xml")


@router.get("/reports/b09")
@router.get("/reports/b09.pdf")
@router.get("/reports/b09.xlsx")
def report_b09(
    request: Request,
    user: CurrentUser = Depends(require("reports.view")),
    db: Session = Depends(get_tenant_db),
):
    fmt = _fmt(request)
    _check_export(user, fmt)
    company = _company(db, user)
    if not statements.statement_set(company).notes_code:
        raise NotFoundError(
            "Doanh nghiệp siêu nhỏ (TT58) không phải lập thuyết minh BCTC"
        )
    period = periods.resolve(request.query_params, default_mode="year")
    data = notes.build(db, company, period)
    if fmt == "pdf":
        return pdf_response(
            exports.pdf(
                "b09", company=company, notes=data, subtitle=period.label
            ),
            _file(company, "B09-DNN", "pdf"),
        )
    if fmt == "xlsx":
        return xlsx_response(
            exports.xlsx_notes(company, data, period.label),
            _file(company, "B09-DNN", "xlsx"),
        )
    return render(
        request,
        "gl/report_b09.html",
        _report_ctx(
            company,
            period,
            "Bản thuyết minh báo cáo tài chính",
            "Thuyết minh BCTC",
            notes=data,
        ),
    )
