"""Bank deposit routes.

Bank receipts/payments (BC/BN), internal transfers (CK), the S05-DNN
bank book, the bank account list, statement import and
reconciliation.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dates import parse_date, today
from app.core.errors import BusinessError, NotFoundError, PermissionDeniedError
from app.core.money import amount_in_words
from app.core.pdf import render_pdf
from app.core.registry import can_entry
from app.core.security import CurrentUser, require
from app.core.web import (
    form_data,
    get_ctx,
    get_tenant_db,
    is_htmx,
    redirect,
    render,
)
from app.core.xlsx import pdf_response, xlsx_response
from app.features.bank import service as bank_service
from app.features.bank import statements as st
from app.features.bank import transfers
from app.features.bank.models import BankAccount
from app.features.bank.statement_models import BankStatement
from app.features.cash import books, excel, printing
from app.features.cash import vouchers as mv
from app.features.cash.period import resolve
from app.features.cash.router import _submit, detail_context, form_context
from app.features.journal import queries
from app.features.journal import service as js
from app.features.journal.models import POSTED, JournalEntry
from app.features.org.models import Company
from app.features.partners.models import Partner

router = APIRouter()
PAGE = 50
KINDS = ("BC", "BN", "CK")


def _company(db: Session, user: CurrentUser) -> Company:
    return db.get(Company, user.company_id)


def _entry(db: Session, request: Request, entry_id: int):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id, doc_types=KINDS)
    if not can_entry(ctx.user, e.doc_type, "view"):
        raise PermissionDeniedError()
    return ctx, e


def _balances(db: Session, company_id: int) -> list[dict]:
    out = []
    for b in bank_service.list_bank_accounts(db, company_id, active=None):
        bal = queries.balance(
            db,
            company_id,
            b.account_code,
            upto=today(),
            exact=True,
            bank_account_id=b.id,
        )
        if b.is_active or bal:
            out.append({"bank": b, "balance": bal})
    return out


# ----------------------------------------------------------------- list


@router.get("/bank")
def bank_list(
    request: Request,
    page: int = 1,
    user: CurrentUser = Depends(require("bank.view")),
    db: Session = Depends(get_tenant_db),
):
    params = request.query_params
    kind = params.get("kind") or ""
    try:
        start = (
            parse_date(params.get("start")) if params.get("start") else None
        )
        end = parse_date(params.get("end")) if params.get("end") else None
    except ValueError:
        start = end = None
    bid = params.get("bank_account_id")
    f = mv.ListFilter(
        kinds=(kind,) if kind in KINDS else KINDS,
        status=params.get("status") or "",
        q=(params.get("q") or "").strip()[:100],
        start=start,
        end=end,
        bank_account_id=int(bid) if bid and bid.isdigit() else None,
    )
    page = max(1, page)
    total = mv.count_entries(db, user.company_id, f)
    base_q = {
        k: v for k, v in params.items() if k not in ("page", "status") and v
    }
    ctx = {
        "entries": mv.list_entries(
            db, user.company_id, f, limit=PAGE, offset=(page - 1) * PAGE
        ),
        "page": page,
        "pages": max(1, (total + PAGE - 1) // PAGE),
        "total": total,
        "list_url": "/bank",
        "filters": {
            "kind": kind,
            "status": f.status,
            "q": f.q,
            "start": params.get("start") or "",
            "end": params.get("end") or "",
            "bank_account_id": bid or "",
        },
        "base_q": urlencode(base_q),
        "counts": mv.status_counts(db, user.company_id, f),
        "balances": _balances(db, user.company_id),
        "bank_options": [
            (b.id, b.label)
            for b in bank_service.list_bank_accounts(db, user.company_id)
        ],
        "breadcrumbs": [("Tiền", None), ("Tiền gửi ngân hàng", None)],
    }
    if is_htmx(request) and request.headers.get("hx-target") == "bank-results":
        return render(request, "cash/_results.html", ctx)
    return render(request, "bank/list.html", ctx)


# ----------------------------------------------- bank receipts/payments


@router.get("/bank/new")
def bank_new(
    request: Request,
    kind: str = "BC",
    mode: str = "quick",
    user: CurrentUser = Depends(require("bank.create")),
    db: Session = Depends(get_tenant_db),
):
    kind = kind if kind in mv.BANK_KINDS else "BC"
    banks = bank_service.list_bank_accounts(db, user.company_id)
    values = {
        "date": today(),
        "doc_date": today(),
        "bank_account_id": request.query_params.get("bank_account_id")
        or (banks[0].id if banks else None),
    }
    for k in (
        "account",
        "description",
        "contact_name",
        "partner_id",
        "ref_no",
    ):
        if request.query_params.get(k):
            values[k] = request.query_params.get(k)
    ctx = form_context(
        request,
        db,
        user,
        kind,
        values,
        {},
        mode="detail" if mode == "detail" else "quick",
        action="/bank",
        cancel_url="/bank",
    )
    return render(request, "cash/form.html", ctx)


@router.post("/bank")
def bank_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.create")),
    db: Session = Depends(get_tenant_db),
):
    kind = form.get("kind") if form.get("kind") in mv.BANK_KINDS else "BC"
    return _submit(
        request, db, user, form, kind, action_url="/bank", cancel_url="/bank"
    )


# --------------------------------------------------- internal transfers


def _transfer_ctx(
    request, db, user, values: dict, errors: dict, entry=None
) -> dict:
    return {
        "v": values,
        "errors": errors,
        "entry": entry,
        "options": transfers.options(db, user.company_id),
        "action": f"/bank/{entry.id}" if entry else "/bank/transfer",
        "cancel_url": entry.url if entry else "/bank",
        "can_post": can_entry(user, "CK", "post"),
        "today": today(),
        "breadcrumbs": [
            ("Tiền", None),
            ("Tiền gửi ngân hàng", "/bank"),
            ("Chuyển tiền nội bộ", None),
        ],
    }


@router.get("/bank/transfer/new")
def transfer_new(
    request: Request,
    user: CurrentUser = Depends(require("bank.create")),
    db: Session = Depends(get_tenant_db),
):
    values = {
        "date": today(),
        "doc_date": today(),
        "source": request.query_params.get("source") or "",
        "target": request.query_params.get("target") or "",
        "description": "",
        "amount": None,
        "ref_no": "",
    }
    return render(
        request,
        "bank/transfer_form.html",
        _transfer_ctx(request, db, user, values, {}),
    )


def _transfer_submit(request, db, user, form, entry=None):
    company = _company(db, user)
    t, errors = transfers.parse_form(db, form, company)
    post = form.get("action") == "post"
    if t is not None:
        ctx = get_ctx(request)
        try:
            e = transfers.save(db, ctx, t, entry)
        except BusinessError as ex:
            db.rollback()
            errors = {"_": ex.message}
        else:
            if post:
                try:
                    js.post_entry(db, ctx, e)
                except BusinessError as ex:
                    db.rollback()
                    return redirect(
                        e.url,
                        f"Đã lưu nháp nhưng chưa ghi sổ được: {ex.message}",
                        "error",
                        request=request,
                    )
            return redirect(
                e.url,
                f"Đã ghi sổ {e.number}"
                if e.status == POSTED
                else "Đã lưu nháp",
                request=request,
            )
    values = {
        k: form.get(k)
        for k in (
            "date",
            "doc_date",
            "source",
            "target",
            "description",
            "ref_no",
        )
    }
    from app.core.money import MoneyError, parse_vnd

    try:
        values["amount"] = (
            parse_vnd(form.get("amount")) if form.get("amount") else None
        )
    except MoneyError:
        values["amount"] = None
    return render(
        request,
        "bank/transfer_form.html",
        _transfer_ctx(request, db, user, values, errors, entry),
        status_code=422,
    )


@router.post("/bank/transfer")
def transfer_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.create")),
    db: Session = Depends(get_tenant_db),
):
    return _transfer_submit(request, db, user, form)


# --------------------------------------------------------- detail, edit


@router.get("/bank/{entry_id:int}")
def bank_detail(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("bank.view")),
    db: Session = Depends(get_tenant_db),
):
    _ctx, e = _entry(db, request, entry_id)
    ctx = detail_context(db, user, e)
    ctx.update(
        {
            "breadcrumbs": [
                ("Tiền", None),
                ("Tiền gửi ngân hàng", "/bank"),
                (e.display_number, None),
            ],
            "back": "/bank",
            "print_url": f"/bank/{e.id}/print.pdf"
            if e.doc_type == "BN"
            else None,
            "edit_url": f"/bank/{e.id}/edit",
            "copy_url": None,
        }
    )
    if e.doc_type == "CK":
        ctx["incoming"] = False
    return render(request, "cash/detail.html", ctx)


@router.get("/bank/{entry_id:int}/edit")
def bank_edit(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("bank.create")),
    db: Session = Depends(get_tenant_db),
):
    _ctx, e = _entry(db, request, entry_id)
    if e.status != "draft":
        return redirect(
            e.url, "Chứng từ đã ghi sổ không sửa được số liệu", "warning"
        )
    if e.doc_type == "CK":
        return render(
            request,
            "bank/transfer_form.html",
            _transfer_ctx(request, db, user, transfers.values(e), {}, e),
        )
    values = mv.form_values(db, e)
    mode = (
        "detail"
        if len(values.get("lines") or []) > 1
        or values.get("vat_on")
        or request.query_params.get("mode") == "detail"
        else "quick"
    )
    ctx = form_context(
        request,
        db,
        user,
        e.doc_type,
        values,
        {},
        entry=e,
        mode=mode,
        action=f"/bank/{e.id}",
        cancel_url=e.url,
    )
    return render(request, "cash/form.html", ctx)


@router.post("/bank/{entry_id:int}")
def bank_update(
    request: Request,
    entry_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.create")),
    db: Session = Depends(get_tenant_db),
):
    _ctx, e = _entry(db, request, entry_id)
    if e.status != "draft":
        raise BusinessError(
            "Chứng từ đã ghi sổ không sửa được số liệu. "
            "Hãy hủy hoặc lập chứng từ điều chỉnh."
        )
    if e.doc_type == "CK":
        return _transfer_submit(request, db, user, form, e)
    return _submit(
        request,
        db,
        user,
        form,
        e.doc_type,
        entry=e,
        action_url=f"/bank/{e.id}",
        cancel_url=e.url,
    )


@router.get("/bank/{entry_id:int}/print.pdf")
def bank_print(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("bank.view")),
    db: Session = Depends(get_tenant_db),
):
    _ctx, e = _entry(db, request, entry_id)
    if e.doc_type != "BN":
        raise NotFoundError("Chỉ in ủy nhiệm chi cho chứng từ chi tiền gửi")
    company = _company(db, user)
    ev = mv.view(db, e)
    bank = (
        db.get(BankAccount, ev.bank_account_id) if ev.bank_account_id else None
    )
    partner = db.get(Partner, e.partner_id) if e.partner_id else None
    data = render_pdf(
        "bank/print/unc.html",
        {
            "company": company,
            "e": e,
            "bank": bank,
            "partner": partner,
            "words": amount_in_words(e.total) + ".",
            "watermark": "NHÁP"
            if e.status == "draft"
            else "ĐÃ HỦY"
            if e.status == "cancelled"
            else None,
        },
    )
    return pdf_response(data, f"UNC_{e.number or e.id}.pdf")


# ---------------------------------------------------- bank book S05-DNN


def _s05(db: Session, user: CurrentUser, params):
    company = _company(db, user)
    banks = bank_service.list_bank_accounts(db, user.company_id, active=None)
    bid = params.get("bank_account_id")
    bank = next(
        (b for b in banks if str(b.id) == str(bid)),
        banks[0] if banks else None,
    )
    period = resolve(params, company.start_date)
    book = books.money_book(
        db,
        company.id,
        period.start,
        period.end,
        form="s05",
        account=bank.account_code if bank else "1121",
        bank_account_id=bank.id if bank else -1,
    )
    return company, banks, bank, period, book


def _s05_lines(bank: BankAccount | None) -> list[str]:
    if bank is None:
        return []
    return [
        "Nơi mở tài khoản giao dịch: "
        f"{bank.bank_name}{(' – ' + bank.branch) if bank.branch else ''}",
        f"Số hiệu tài khoản tại nơi gửi: {bank.number}",
    ]


@router.get("/bank/book")
def bank_book(
    request: Request,
    user: CurrentUser = Depends(require("bank.view")),
    db: Session = Depends(get_tenant_db),
):
    company, banks, bank, period, book = _s05(db, user, request.query_params)
    q = period.query() + (f"&bank_account_id={bank.id}" if bank else "")
    return render(
        request,
        "bank/book.html",
        {
            "book": book,
            "period": period,
            "bank": bank,
            "banks": banks,
            "q": q,
            "info_lines": _s05_lines(bank),
            "years": list(
                range(
                    max(today().year, company.start_date.year),
                    company.start_date.year - 1,
                    -1,
                )
            ),
            "can_export": user.can("bank.export"),
            "breadcrumbs": [("Tiền", None), ("Sổ tiền gửi ngân hàng", None)],
        },
    )


@router.get("/bank/book.pdf")
def bank_book_pdf(
    request: Request,
    user: CurrentUser = Depends(require("bank.export")),
    db: Session = Depends(get_tenant_db),
):
    company, _banks, bank, period, book = _s05(db, user, request.query_params)
    if bank is None:
        raise NotFoundError("Chưa có tài khoản ngân hàng")
    data = printing.book_pdf(
        company, book, period.print_label, _s05_lines(bank)
    )
    return pdf_response(
        data,
        f"S05-DNN_{bank.number}_{period.start:%Y%m%d}_{period.end:%Y%m%d}.pdf",
    )


@router.get("/bank/book.xlsx")
def bank_book_xlsx(
    request: Request,
    user: CurrentUser = Depends(require("bank.export")),
    db: Session = Depends(get_tenant_db),
):
    company, _banks, bank, period, book = _s05(db, user, request.query_params)
    if bank is None:
        raise NotFoundError("Chưa có tài khoản ngân hàng")
    data = excel.book_xlsx(
        company,
        book,
        period.print_label,
        subtitle="",
        extra_lines=_s05_lines(bank),
    )
    return xlsx_response(
        data,
        f"S05-DNN_{bank.number}_{period.start:%Y%m%d}_{period.end:%Y%m%d}.xlsx",
    )


# ---------------------------------------------------- bank account list


@router.get("/bank-accounts")
def bank_accounts_page(
    request: Request,
    user: CurrentUser = Depends(require("bank.view")),
    db: Session = Depends(get_tenant_db),
):
    return render(
        request,
        "bank/accounts.html",
        {
            "rows": [
                {
                    "bank": b,
                    "balance": queries.balance(
                        db,
                        user.company_id,
                        b.account_code,
                        upto=today(),
                        exact=True,
                        bank_account_id=b.id,
                    ),
                }
                for b in bank_service.list_bank_accounts(
                    db, user.company_id, active=None
                )
            ],
            "breadcrumbs": [("Danh mục", None), ("Tài khoản ngân hàng", None)],
        },
    )


@router.post("/bank-accounts")
def bank_account_create(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("bank.manage")),
    db: Session = Depends(get_tenant_db),
):
    b = bank_service.create_bank_account(
        db,
        get_ctx(request),
        number=form.get("number") or "",
        bank_name=form.get("bank_name") or "",
        branch=form.get("branch") or "",
        short_name=form.get("short_name") or "",
        account_code=form.get("account_code") or "1121",
        note=form.get("note") or "",
    )
    return redirect(
        "/bank-accounts", f"Đã thêm tài khoản {b.label}", request=request
    )


@router.post("/bank-accounts/{bank_id:int}")
def bank_account_update(
    request: Request,
    bank_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.manage")),
    db: Session = Depends(get_tenant_db),
):
    b = bank_service.get_bank_account(db, user.company_id, bank_id)
    active = None
    if "active" in form:
        active = form.get("active") == "1"
    bank_service.update_bank_account(
        db,
        get_ctx(request),
        b,
        bank_name=form.get("bank_name"),
        branch=form.get("branch"),
        short_name=form.get("short_name"),
        note=form.get("note"),
        is_active=active,
    )
    return redirect(
        "/bank-accounts", "Đã cập nhật tài khoản ngân hàng", request=request
    )


# ------------------------------------------- statements, reconciliation


@router.get("/bank/statements")
def statements_page(
    request: Request,
    user: CurrentUser = Depends(require("bank.view")),
    db: Session = Depends(get_tenant_db),
):
    banks = bank_service.list_bank_accounts(db, user.company_id)
    bid = request.query_params.get("bank_account_id")
    bank = next(
        (b for b in banks if str(b.id) == str(bid)),
        banks[0] if banks else None,
    )
    status = request.query_params.get("status") or "unmatched"
    rec = st.reconciliation(db, user.company_id, bank) if bank else None
    lines = (
        st.list_lines(
            db,
            user.company_id,
            bank_account_id=bank.id,
            status=status if status != "all" else "",
        )
        if bank
        else []
    )
    suggestions = {
        sl.id: st.suggest(db, user.company_id, sl)
        for sl in lines
        if sl.status == "unmatched"
    }
    history = (
        list(
            db.scalars(
                select(BankStatement)
                .where(
                    BankStatement.company_id == user.company_id,
                    BankStatement.bank_account_id == bank.id,
                )
                .order_by(BankStatement.imported_at.desc())
                .limit(10)
            )
        )
        if bank
        else []
    )
    return render(
        request,
        "bank/statements.html",
        {
            "banks": banks,
            "bank": bank,
            "rec": rec,
            "lines": lines,
            "status": status,
            "suggestions": suggestions,
            "rules": st.rules(db, user.company_id),
            "history": history,
            "breadcrumbs": [("Tiền", None), ("Đối chiếu sao kê", None)],
        },
    )


@router.get("/bank/statements/import")
def statements_import_page(
    request: Request,
    user: CurrentUser = Depends(require("bank.reconcile")),
    db: Session = Depends(get_tenant_db),
):
    return render(
        request,
        "bank/statement_import.html",
        {
            "banks": bank_service.list_bank_accounts(db, user.company_id),
            "bank_id": request.query_params.get("bank_account_id"),
            "breadcrumbs": [
                ("Tiền", None),
                ("Đối chiếu sao kê", "/bank/statements"),
                ("Nhập sao kê", None),
            ],
        },
    )


@router.post("/bank/statements/import")
async def statements_upload(
    request: Request,
    file: UploadFile = File(...),
    bank_account_id: int = Form(...),
    user: CurrentUser = Depends(require("bank.reconcile")),
    db: Session = Depends(get_tenant_db),
):
    bank_service.get_bank_account(db, user.company_id, bank_account_id)
    data = await file.read()
    token = st.save_upload(
        user.company_id, bank_account_id, file.filename or "saoke.csv", data
    )
    return redirect(f"/bank/statements/import/{token}", request=request)


def _mapping_from(
    params, rows, profile: dict | None
) -> tuple[int, dict[str, int]]:
    if params.get("header_row") not in (None, ""):
        try:
            header = int(params.get("header_row")) - 1
        except ValueError:
            header = 0
        mapping = {}
        for f in st.FIELDS:
            v = params.get(f"col_{f}")
            if v not in (None, "") and str(v).isdigit():
                mapping[f] = int(v)
        return max(0, header), mapping
    if profile:
        header = int(profile.get("header_row", 0))
        mapping = {k: int(v) for k, v in profile.items() if k in st.FIELDS}
        return header, mapping
    return st.guess_mapping(rows)


@router.get("/bank/statements/import/{token}")
def statements_preview(
    request: Request,
    token: str,
    user: CurrentUser = Depends(require("bank.reconcile")),
    db: Session = Depends(get_tenant_db),
):
    data, info = st.load_upload(user.company_id, token)
    bank = bank_service.get_bank_account(
        db, user.company_id, int(info["bank_account_id"])
    )
    rows = st.read_rows(data, info["filename"])
    header, mapping = _mapping_from(
        request.query_params,
        rows,
        st.get_profile(db, user.company_id, bank.id),
    )
    if header < 0:
        header = 0
    result = st.apply_mapping(rows, header, mapping)
    width = max((len(r) for r in rows[:60]), default=0)
    return render(
        request,
        "bank/statement_preview.html",
        {
            "token": token,
            "bank": bank,
            "filename": info["filename"],
            "rows": rows[: 12 + header],
            "width": width,
            "header": header,
            "mapping": mapping,
            "fields": st.FIELDS,
            "result": result,
            "breadcrumbs": [
                ("Tiền", None),
                ("Đối chiếu sao kê", "/bank/statements"),
                (info["filename"], None),
            ],
        },
    )


@router.post("/bank/statements/import/{token}")
def statements_confirm(
    request: Request,
    token: str,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.reconcile")),
    db: Session = Depends(get_tenant_db),
):
    data, info = st.load_upload(user.company_id, token)
    bank = bank_service.get_bank_account(
        db, user.company_id, int(info["bank_account_id"])
    )
    rows = st.read_rows(data, info["filename"])
    header, mapping = _mapping_from(form, rows, None)
    result = st.apply_mapping(rows, header, mapping)
    _stmt, new, dup = st.import_statement(
        db, get_ctx(request), bank, info["filename"], result, header, mapping
    )
    st.discard_upload(token, user.company_id)
    msg = f"Đã nhập {new} giao dịch" + (
        f", bỏ qua {dup} giao dịch đã có" if dup else ""
    )
    return redirect(
        f"/bank/statements?bank_account_id={bank.id}", msg, request=request
    )


@router.post("/bank/statements/automatch")
def statements_automatch(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.reconcile")),
    db: Session = Depends(get_tenant_db),
):
    bank = bank_service.get_bank_account(
        db, user.company_id, int(form.get("bank_account_id") or 0)
    )
    n = st.auto_match(db, get_ctx(request), bank.id)
    return redirect(
        f"/bank/statements?bank_account_id={bank.id}",
        f"Đã tự khớp {n} giao dịch",
        request=request,
    )


@router.get("/bank/statements/lines/{line_id:int}")
def statement_line(
    request: Request,
    line_id: int,
    user: CurrentUser = Depends(require("bank.view")),
    db: Session = Depends(get_tenant_db),
):
    sl = st.get_line(db, user.company_id, line_id)
    return render(
        request,
        "bank/statement_line.html",
        {
            "sl": sl,
            "bank": db.get(BankAccount, sl.bank_account_id),
            "candidates": st.candidates(db, user.company_id, sl, days=60),
            "suggestion": st.suggest(db, user.company_id, sl),
            "matched": db.get(JournalEntry, sl.matched_entry_id)
            if sl.matched_entry_id
            else None,
            "breadcrumbs": [
                ("Tiền", None),
                (
                    "Đối chiếu sao kê",
                    f"/bank/statements?bank_account_id={sl.bank_account_id}",
                ),
                (f"{sl.date:%d/%m/%Y}", None),
            ],
        },
    )


@router.post("/bank/statements/lines/{line_id:int}/match")
def statement_match(
    request: Request,
    line_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.reconcile")),
    db: Session = Depends(get_tenant_db),
):
    sl = st.match(
        db, get_ctx(request), line_id, int(form.get("journal_line_id") or 0)
    )
    return redirect(
        f"/bank/statements?bank_account_id={sl.bank_account_id}",
        "Đã khớp giao dịch",
        request=request,
    )


@router.post("/bank/statements/lines/{line_id:int}/unmatch")
def statement_unmatch(
    request: Request,
    line_id: int,
    user: CurrentUser = Depends(require("bank.reconcile")),
    db: Session = Depends(get_tenant_db),
):
    sl = st.unmatch(db, get_ctx(request), line_id)
    return redirect(
        f"/bank/statements/lines/{sl.id}", "Đã bỏ khớp", request=request
    )


@router.post("/bank/statements/lines/{line_id:int}/ignore")
def statement_ignore(
    request: Request,
    line_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.reconcile")),
    db: Session = Depends(get_tenant_db),
):
    sl = st.ignore(db, get_ctx(request), line_id, form.get("note") or "")
    return redirect(
        f"/bank/statements?bank_account_id={sl.bank_account_id}",
        "Đã bỏ qua giao dịch"
        if sl.status == "ignored"
        else "Đã theo dõi lại giao dịch",
        request=request,
    )


@router.post("/bank/statements/lines/{line_id:int}/draft")
def statement_draft(
    request: Request,
    line_id: int,
    user: CurrentUser = Depends(require("bank.create")),
    db: Session = Depends(get_tenant_db),
):
    e = st.create_draft(db, get_ctx(request), line_id)
    return redirect(
        f"/bank/{e.id}/edit",
        "Đã lập chứng từ nháp từ sao kê – kiểm tra TK đối ứng rồi ghi sổ",
        request=request,
    )


@router.post("/bank/rules")
def rule_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.manage")),
    db: Session = Depends(get_tenant_db),
):
    pid = form.get("partner_id")
    st.save_rule(
        db,
        get_ctx(request),
        keyword=form.get("keyword") or "",
        account=form.get("account") or "",
        direction=form.get("direction") or "any",
        partner_id=int(pid) if pid and pid.isdigit() else None,
        description=form.get("description") or "",
    )
    bank_account_id = form.get("bank_account_id") or ""
    return redirect(
        f"/bank/statements?bank_account_id={bank_account_id}",
        "Đã thêm luật gợi ý",
        request=request,
    )


@router.post("/bank/rules/{rule_id:int}/delete")
def rule_delete(
    request: Request,
    rule_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("bank.manage")),
    db: Session = Depends(get_tenant_db),
):
    st.delete_rule(db, get_ctx(request), rule_id)
    bank_account_id = form.get("bank_account_id") or ""
    return redirect(
        f"/bank/statements?bank_account_id={bank_account_id}",
        "Đã xóa luật",
        request=request,
    )
