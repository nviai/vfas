"""Cash module routes.

Cash receipts and payments, the cash book (S04a/S04b), cash counts
(08a-TT), cash book import from Excel and receipt/payment purposes.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import AuditLog
from app.core.dates import parse_date, today
from app.core.errors import BusinessError, PermissionDeniedError
from app.core.money import MoneyError, amount_in_words, parse_vnd
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
from app.features.accounts.models import Account
from app.features.auth.models import User
from app.features.bank.models import BankAccount
from app.features.bank.service import list_bank_accounts
from app.features.cash import (
    books,
    counts,
    excel,
    importer,
    printing,
    purposes,
)
from app.features.cash import vouchers as mv
from app.features.cash.models import DENOMINATIONS
from app.features.cash.period import resolve
from app.features.journal import locks, queries
from app.features.journal import service as js
from app.features.journal.models import POSTED, JournalEntry
from app.features.org.models import Company
from app.features.partners.models import Partner

router = APIRouter()
PAGE = 50
CASH = mv.CASH_ACCOUNT


def _company(db: Session, user: CurrentUser) -> Company:
    return db.get(Company, user.company_id)


def _entry(db: Session, request: Request, entry_id: int):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id, doc_types=mv.CASH_KINDS)
    if not can_entry(ctx.user, e.doc_type, "view"):
        raise PermissionDeniedError()
    return ctx, e


# ----------------------------------------------------------------- list


def _list_filter(params, company: Company) -> mv.ListFilter:
    kind = params.get("kind") or ""
    try:
        start = (
            parse_date(params.get("start")) if params.get("start") else None
        )
        end = parse_date(params.get("end")) if params.get("end") else None
    except ValueError:
        start = end = None
    return mv.ListFilter(
        kinds=(kind,) if kind in mv.CASH_KINDS else mv.CASH_KINDS,
        status=params.get("status") or "",
        q=(params.get("q") or "").strip()[:100],
        start=start,
        end=end,
        account=(params.get("account") or "").strip()[:20],
    )


@router.get("/cash")
def cash_list(
    request: Request,
    page: int = 1,
    user: CurrentUser = Depends(require("cash.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    params = request.query_params
    f = _list_filter(params, company)
    page = max(1, page)
    total = mv.count_entries(db, company.id, f)
    entries = mv.list_entries(
        db, company.id, f, limit=PAGE, offset=(page - 1) * PAGE
    )
    base_q = {
        k: v for k, v in params.items() if k not in ("page", "status") and v
    }
    ctx = {
        "entries": entries,
        "page": page,
        "pages": max(1, (total + PAGE - 1) // PAGE),
        "total": total,
        "filters": {
            "kind": params.get("kind") or "",
            "status": f.status,
            "q": f.q,
            "account": f.account,
            "start": params.get("start") or "",
            "end": params.get("end") or "",
        },
        "base_q": urlencode(base_q),
        "counts": mv.status_counts(db, company.id, f),
        "sums": mv.totals_by_kind(db, company.id, f),
        "balance": queries.balance(
            db, company.id, CASH, upto=today(), exact=True
        ),
        "breadcrumbs": [("Tiền", None), ("Phiếu thu, chi", None)],
    }
    if is_htmx(request) and request.headers.get("hx-target") == "cash-results":
        return render(request, "cash/_results.html", ctx)
    return render(request, "cash/list.html", ctx)


@router.get("/cash/words")
def cash_words(
    amount: str = "", _u: CurrentUser = Depends(require("cash.view"))
):
    try:
        v = parse_vnd(amount) if amount else 0
    except MoneyError:
        return PlainTextResponse("")
    return PlainTextResponse(amount_in_words(v) + "." if v > 0 else "")


# ----------------------------------------------------------------- form


def _label_partner(db: Session, company_id: int, partner_id) -> str:
    if not partner_id:
        return ""
    p = db.get(Partner, int(partner_id))
    return p.label if p is not None and p.company_id == company_id else ""


def _account_names(db: Session, company_id: int) -> dict[str, str]:
    return dict(
        db.execute(
            select(Account.code, Account.name).where(
                Account.company_id == company_id
            )
        ).all()
    )


def form_context(
    request: Request,
    db: Session,
    user: CurrentUser,
    kind: str,
    values: dict,
    errors: dict,
    *,
    entry: JournalEntry | None = None,
    mode: str = "quick",
    action: str = "",
    cancel_url: str = "/cash",
) -> dict:
    """Build the context of the cash/form.html template.

    Shared by PT/PC (cash module) and BC/BN (bank module).
    """
    company = _company(db, user)
    defaults = {
        k: None
        for k in (
            "date",
            "doc_date",
            "description",
            "contact_name",
            "contact_address",
            "partner_id",
            "attached_docs",
            "ref_no",
            "bank_account_id",
            "contra_bank_account_id",
            "account",
            "amount",
            "cash_count_id",
            "vat_on",
            "vat_rate",
            "vat_amount",
            "vat_goods_amount",
            "vat_invoice_no",
            "vat_invoice_series",
            "vat_invoice_template",
            "vat_invoice_date",
            "vat_partner_name",
            "vat_partner_tax_code",
        )
    }
    values = {
        **defaults,
        **{k: (val if val != "" else None) for k, val in values.items()},
    }
    names = _account_names(db, company.id)
    opts = purposes.options(db, company, kind)
    lines = []
    for ln in values.get("lines") or []:
        acc = ln.get("account") or ""
        lines.append(
            {
                "debit_account"
                if not mv.is_in(kind)
                else "credit_account": acc,
                (
                    "debit_account_label"
                    if not mv.is_in(kind)
                    else "credit_account_label"
                ): f"{acc} – {names.get(acc, '')}" if acc else "",
                "amount": ln.get("amount"),
                "partner_id": ln.get("partner_id") or "",
                "partner_label": _label_partner(
                    db, company.id, ln.get("partner_id")
                ),
                "description": ln.get("description") or "",
            }
        )
    account = values.get("account") or ""
    is_purpose = any(o["account"] == account for o in opts)
    banks = list_bank_accounts(db, company.id)
    is_bank = kind in mv.BANK_KINDS
    money_label = CASH
    if is_bank:
        sel = next(
            (
                b
                for b in banks
                if str(b.id) == str(values.get("bank_account_id") or "")
            ),
            None,
        )
        money_label = sel.account_code if sel else "1121"
    return {
        "kind": kind,
        "kind_label": mv.KIND_LABELS[kind],
        "incoming": mv.is_in(kind),
        "is_bank": is_bank,
        "entry": entry,
        "v": values,
        "errors": errors,
        "mode": mode,
        "action": action,
        "cancel_url": cancel_url,
        "purposes": opts,
        "purpose_sel": account
        if is_purpose
        else ("__other__" if account else ""),
        "other_account": "" if is_purpose else account,
        "other_account_label": f"{account} – {names.get(account, '')}"
        if account and not is_purpose
        else "",
        "partner_label": _label_partner(
            db, company.id, values.get("partner_id")
        ),
        "lines": lines,
        "money_account": money_label,
        "banks": banks,
        "can_post": can_entry(user, kind, "post"),
        "today": today(),
        "company": company,
        "vat_rates": [
            ("10", "10%"),
            ("8", "8%"),
            ("5", "5%"),
            ("0", "0%"),
            ("KCT", "KCT"),
            ("KKKNT", "KKKNT"),
        ],
        "breadcrumbs": [
            ("Tiền", None),
            (
                ("Tiền gửi ngân hàng", "/bank")
                if is_bank
                else ("Phiếu thu, chi", "/cash")
            ),
            ("Sửa nháp" if entry else "Tạo mới", None),
        ],
    }


def _new_values(params) -> dict:
    vals = {"date": today(), "doc_date": today()}
    for k in (
        "account",
        "description",
        "contact_name",
        "contact_address",
        "cash_count_id",
        "partner_id",
        "bank_account_id",
        "contra_bank_account_id",
        "ref_no",
    ):
        if params.get(k):
            vals[k] = params.get(k)
    if params.get("amount"):
        try:
            vals["amount"] = parse_vnd(params.get("amount"))
        except MoneyError:
            pass
    if params.get("date"):
        try:
            vals["date"] = vals["doc_date"] = (
                parse_date(params.get("date")) or today()
            )
        except ValueError:
            pass
    return vals


@router.get("/cash/new")
def cash_new(
    request: Request,
    kind: str = "PT",
    mode: str = "quick",
    user: CurrentUser = Depends(require("cash.create")),
    db: Session = Depends(get_tenant_db),
):
    kind = kind if kind in mv.CASH_KINDS else "PT"
    values = _new_values(request.query_params)
    copy_id = request.query_params.get("copy")
    if copy_id and copy_id.isdigit():
        src = js.get_entry(
            db, get_ctx(request), int(copy_id), doc_types=mv.CASH_KINDS
        )
        values = {
            **mv.form_values(db, src),
            "date": today(),
            "doc_date": today(),
        }
        kind = src.doc_type
        if len(values.get("lines") or []) > 1 or values.get("vat_on"):
            mode = "detail"
    ctx = form_context(
        request,
        db,
        user,
        kind,
        values,
        {},
        mode="detail" if mode == "detail" else "quick",
        action="/cash",
    )
    return render(request, "cash/form.html", ctx)


def _submit(
    request: Request,
    db: Session,
    user: CurrentUser,
    form: dict,
    kind: str,
    *,
    entry=None,
    action_url: str,
    cancel_url: str,
    template: str = "cash/form.html",
):
    company = _company(db, user)
    mode = "detail" if form.get("mode") == "detail" else "quick"
    v, errors = mv.parse_form(form, kind, company=company)
    post = form.get("action") == "post"
    if post and not can_entry(user, kind, "post"):
        raise PermissionDeniedError()
    if v is not None:
        ctx = get_ctx(request)
        try:
            e = mv.save(db, ctx, v, entry=entry)
        except js.EntryValidationError as ex:
            db.rollback()
            errors = {"lines": "; ".join(ex.errors)}
        except BusinessError as ex:
            db.rollback()
            errors = {"_": ex.message}
        else:
            if post:
                try:
                    js.post_entry(db, ctx, e)
                except BusinessError as ex:
                    db.rollback()
                    # Keep the form (already saved as a draft) and show
                    # the error on it instead of a transient flash.
                    db.refresh(e)
                    errs = {
                        "_": (
                            f"Đã lưu nháp nhưng chưa ghi sổ được: {ex.message}"
                        )
                    }
                    if "đối tượng" in ex.message:
                        errs["partner_id"] = (
                            "Chọn đối tượng "
                            "(khách hàng, nhà cung cấp, nhân viên)"
                        )
                    fctx = form_context(
                        request,
                        db,
                        user,
                        e.doc_type,
                        mv.form_values(db, e),
                        errs,
                        entry=e,
                        mode=mode,
                        action=e.url,
                        cancel_url=e.url,
                    )
                    return render(request, template, fctx, status_code=422)
            msg = (
                f"Đã ghi sổ {e.number}"
                if e.status == POSTED
                else "Đã lưu nháp"
            )
            return redirect(e.url, msg, request=request)
    values = {k: form.get(k) for k in form.keys()}
    try:
        count = int(form.get("lines-count") or 0)
    except ValueError:
        count = 0
    side = "credit_account" if mv.is_in(kind) else "debit_account"
    values["lines"] = [
        {
            "account": form.get(f"lines-{i}-{side}") or "",
            "description": form.get(f"lines-{i}-description"),
            "partner_id": form.get(f"lines-{i}-partner_id"),
            "amount": _int(form.get(f"lines-{i}-amount")),
        }
        for i in range(min(count, 200))
    ]
    values["amount"] = _int(form.get("amount"))
    values["vat_on"] = form.get("vat_on") in ("1", "on", "true")
    for k in ("vat_amount", "vat_goods_amount"):
        values[k] = _int(form.get(k))
    ctx = form_context(
        request,
        db,
        user,
        kind,
        values,
        errors,
        entry=entry,
        mode=mode,
        action=action_url,
        cancel_url=cancel_url,
    )
    return render(request, template, ctx, status_code=422)


def _int(raw):
    try:
        return parse_vnd(raw) if raw not in (None, "") else None
    except MoneyError:
        return None


@router.post("/cash")
def cash_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("cash.create")),
    db: Session = Depends(get_tenant_db),
):
    kind = form.get("kind") if form.get("kind") in mv.CASH_KINDS else "PT"
    return _submit(
        request, db, user, form, kind, action_url="/cash", cancel_url="/cash"
    )


@router.get("/cash/{entry_id:int}/edit")
def cash_edit(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("cash.create")),
    db: Session = Depends(get_tenant_db),
):
    _ctx, e = _entry(db, request, entry_id)
    if e.status != "draft":
        return redirect(
            e.url, "Chứng từ đã ghi sổ không sửa được số liệu", "warning"
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
        action=f"/cash/{e.id}",
        cancel_url=e.url,
    )
    return render(request, "cash/form.html", ctx)


@router.post("/cash/{entry_id:int}")
def cash_update(
    request: Request,
    entry_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("cash.create")),
    db: Session = Depends(get_tenant_db),
):
    _ctx, e = _entry(db, request, entry_id)
    if e.status != "draft":
        raise BusinessError(
            "Chứng từ đã ghi sổ không sửa được số liệu. "
            "Hãy hủy hoặc lập chứng từ điều chỉnh."
        )
    return _submit(
        request,
        db,
        user,
        form,
        e.doc_type,
        entry=e,
        action_url=f"/cash/{e.id}",
        cancel_url=e.url,
    )


# --------------------------------------------------------------- detail


def history(db: Session, e: JournalEntry) -> list[dict]:
    from app.core.dates import fmt_datetime

    icons = {
        "entry.create": ("file-plus", "gray"),
        "entry.update": ("pencil", "gray"),
        "entry.post": ("circle-check", "green"),
        "entry.cancel": ("ban", "red"),
        "entry.update_info": ("pencil", "amber"),
        "attachment.add": ("paperclip", "gray"),
        "attachment.delete": ("trash", "gray"),
        "entry.import": ("upload", "brand"),
    }
    out = []
    for log in db.scalars(
        select(AuditLog)
        .where(
            AuditLog.company_id == e.company_id,
            AuditLog.entity_type == "entry",
            AuditLog.entity_id == str(e.id),
        )
        .order_by(AuditLog.id.desc())
    ):
        icon, tone = icons.get(log.action, ("history", "gray"))
        via = {"ai": " · Trợ lý AI", "api": " · API"}.get(log.via, "")
        out.append(
            {
                "at": fmt_datetime(log.at) + via,
                "actor": log.username or "",
                "action": log.summary or log.action,
                "detail": log.reason,
                "icon": icon,
                "tone": tone,
            }
        )
    return out


def _user_name(db: Session, user_id: int | None) -> str:
    if not user_id:
        return ""
    u = db.get(User, user_id)
    return (u.full_name or u.email) if u else ""


def detail_context(db: Session, user: CurrentUser, e: JournalEntry) -> dict:
    company = db.get(Company, e.company_id)
    names = _account_names(db, company.id)
    partner_ids = {
        x
        for ln in e.lines
        for x in (ln.debit_partner_id, ln.credit_partner_id)
        if x
    }
    partners = (
        {
            p.id: p
            for p in db.scalars(
                select(Partner).where(Partner.id.in_(partner_ids))
            )
        }
        if partner_ids
        else {}
    )
    bank_ids = {
        x
        for ln in e.lines
        for x in (ln.debit_bank_account_id, ln.credit_bank_account_id)
        if x
    }
    banks = (
        {
            b.id: b
            for b in db.scalars(
                select(BankAccount).where(BankAccount.id.in_(bank_ids))
            )
        }
        if bank_ids
        else {}
    )
    lines = []
    for ln in e.lines:
        lines.append(
            {
                "debit": ln.debit_account,
                "credit": ln.credit_account,
                "amount": ln.amount,
                "debit_name": names.get(ln.debit_account, ""),
                "credit_name": names.get(ln.credit_account, ""),
                "partner": partners.get(
                    ln.debit_partner_id or ln.credit_partner_id
                ),
                "bank": banks.get(
                    ln.debit_bank_account_id or ln.credit_bank_account_id
                ),
                "description": ln.description,
            }
        )
    locked = locks.is_locked(db, company.id, e.date)
    files = [
        {
            "name": a.filename,
            "size": f"{max(1, a.size // 1024)} KB",
            "href": f"/attachments/{a.id}",
            "kind": "pdf"
            if a.content_type == "application/pdf"
            else "xml"
            if "xml" in a.content_type
            else "image",
            "delete_action": f"/attachments/{a.id}/delete",
        }
        for a in e.attachments
    ]
    reverses = db.get(JournalEntry, e.reverses_id) if e.reverses_id else None
    adjustments = list(
        db.scalars(
            select(JournalEntry).where(
                JournalEntry.company_id == e.company_id,
                JournalEntry.reverses_id == e.id,
            )
        )
    )
    return {
        "e": e,
        "lines": lines,
        "locked": locked,
        "files": files,
        "history": history(db, e),
        "creator": _user_name(db, e.created_by),
        "poster": _user_name(db, e.posted_by),
        "canceller": _user_name(db, e.cancelled_by),
        "words": amount_in_words(e.total),
        "can_post": can_entry(user, e.doc_type, "post"),
        "can_cancel": can_entry(user, e.doc_type, "cancel"),
        "can_edit": can_entry(user, e.doc_type, "create"),
        "reverses": reverses,
        "adjustments": adjustments,
        "today": today(),
        "incoming": mv.is_in(e.doc_type),
        "kind_label": mv.KIND_LABELS.get(e.doc_type, e.doc_type),
    }


@router.get("/cash/{entry_id:int}")
def cash_detail(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("cash.view")),
    db: Session = Depends(get_tenant_db),
):
    _ctx, e = _entry(db, request, entry_id)
    ctx = detail_context(db, user, e)
    ctx["breadcrumbs"] = [
        ("Tiền", None),
        ("Phiếu thu, chi", "/cash"),
        (e.display_number, None),
    ]
    ctx["back"] = "/cash"
    ctx["print_url"] = f"/cash/{e.id}/print.pdf"
    ctx["edit_url"] = f"/cash/{e.id}/edit"
    ctx["copy_url"] = f"/cash/new?copy={e.id}"
    return render(request, "cash/detail.html", ctx)


@router.get("/cash/{entry_id:int}/print.pdf")
def cash_print(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("cash.view")),
    db: Session = Depends(get_tenant_db),
):
    _ctx, e = _entry(db, request, entry_id)
    data = printing.voucher_pdf(
        db, _company(db, user), e, _user_name(db, e.created_by)
    )
    return pdf_response(data, f"{e.number or ('nhap-' + str(e.id))}.pdf")


# ------------------------------------------------------------ cash book


def _book(db: Session, company: Company, params, form: str):
    period = resolve(params, company.start_date)
    book = books.money_book(
        db, company.id, period.start, period.end, form=form, account=CASH
    )
    return period, book


@router.get("/cash/book")
def cash_book(
    request: Request,
    form: str = "s04a",
    user: CurrentUser = Depends(require("cash.view")),
    db: Session = Depends(get_tenant_db),
):
    form = form if form in ("s04a", "s04b") else "s04a"
    company = _company(db, user)
    period, book = _book(db, company, request.query_params, form)
    q = period.query() + f"&form={form}"
    return render(
        request,
        "cash/book.html",
        {
            "book": book,
            "period": period,
            "form": form,
            "q": q,
            "years": list(
                range(
                    max(today().year, company.start_date.year),
                    company.start_date.year - 1,
                    -1,
                )
            ),
            "subtitle": "Tài khoản: 1111 – Loại quỹ: Tiền Việt Nam"
            if form == "s04b"
            else "Loại quỹ: Tiền Việt Nam",
            "can_export": user.can("cash.export"),
            "breadcrumbs": [("Tiền", None), ("Sổ quỹ tiền mặt", None)],
        },
    )


@router.get("/cash/book.pdf")
def cash_book_pdf(
    request: Request,
    form: str = "s04a",
    user: CurrentUser = Depends(require("cash.export")),
    db: Session = Depends(get_tenant_db),
):
    form = form if form in ("s04a", "s04b") else "s04a"
    company = _company(db, user)
    period, book = _book(db, company, request.query_params, form)
    sub = (
        "Tài khoản: 1111 – Loại quỹ: Tiền Việt Nam"
        if form == "s04b"
        else "Loại quỹ: Tiền Việt Nam"
    )
    data = printing.book_pdf(company, book, period.print_label, [sub])
    return pdf_response(
        data, f"{book.code}_{period.start:%Y%m%d}_{period.end:%Y%m%d}.pdf"
    )


@router.get("/cash/book.xlsx")
def cash_book_xlsx(
    request: Request,
    form: str = "s04a",
    user: CurrentUser = Depends(require("cash.export")),
    db: Session = Depends(get_tenant_db),
):
    form = form if form in ("s04a", "s04b") else "s04a"
    company = _company(db, user)
    period, book = _book(db, company, request.query_params, form)
    sub = (
        "Tài khoản: 1111 – Loại quỹ: Tiền Việt Nam"
        if form == "s04b"
        else "Loại quỹ: Tiền Việt Nam"
    )
    data = excel.book_xlsx(company, book, period.print_label, subtitle=sub)
    return xlsx_response(
        data, f"{book.code}_{period.start:%Y%m%d}_{period.end:%Y%m%d}.xlsx"
    )


# ---------------------------------------------------------- cash counts


@router.get("/cash/counts")
def counts_list(
    request: Request,
    user: CurrentUser = Depends(require("cash.view")),
    db: Session = Depends(get_tenant_db),
):
    rows = []
    for cc in counts.list_cash_counts(db, user.company_id):
        remaining = counts.remaining_difference(db, cc)
        rows.append({"cc": cc, "remaining": remaining})
    return render(
        request,
        "cash/counts.html",
        {
            "rows": rows,
            "balance": queries.balance(
                db, user.company_id, CASH, upto=today(), exact=True
            ),
            "breadcrumbs": [("Tiền", None), ("Kiểm kê quỹ", None)],
        },
    )


@router.get("/cash/counts/balance")
def counts_balance(
    d: str = "",
    user: CurrentUser = Depends(require("cash.view")),
    db: Session = Depends(get_tenant_db),
):
    try:
        day = parse_date(d) or today()
    except ValueError:
        day = today()
    return {
        "date": day.isoformat(),
        "balance": counts.book_balance_at(db, user.company_id, day),
    }


def _count_defaults(db: Session, user: CurrentUser) -> dict:
    company = _company(db, user)
    return {
        "count_date": today(),
        "count_time": "",
        "accountant_name": company.chief_accountant or "",
        "cashier_name": company.cashier or "",
        "responsible_name": company.legal_rep or "",
        "other_member_name": "",
        "other_member_role": "",
        "difference_reason": "",
        "conclusion": "",
    }


@router.get("/cash/counts/new")
def counts_new(
    request: Request,
    user: CurrentUser = Depends(require("cashcount.manage")),
    db: Session = Depends(get_tenant_db),
):
    values = _count_defaults(db, user)
    return render(
        request,
        "cash/count_form.html",
        {
            "v": values,
            "errors": {},
            "denoms": DENOMINATIONS,
            "qty": {},
            "book": counts.book_balance_at(db, user.company_id, today()),
            "breadcrumbs": [
                ("Tiền", None),
                ("Kiểm kê quỹ", "/cash/counts"),
                ("Tạo mới", None),
            ],
        },
    )


@router.post("/cash/counts")
def counts_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("cashcount.manage")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    inp, errors = counts.parse_form(form, company)
    if inp is None:
        qty = {d: form.get(f"qty_{d}") for d in DENOMINATIONS}
        try:
            d = parse_date(form.get("count_date")) or today()
        except ValueError:
            d = today()
        return render(
            request,
            "cash/count_form.html",
            {
                "v": form,
                "errors": errors,
                "denoms": DENOMINATIONS,
                "qty": qty,
                "book": counts.book_balance_at(
                    db, user.company_id, min(d, today())
                ),
                "breadcrumbs": [
                    ("Tiền", None),
                    ("Kiểm kê quỹ", "/cash/counts"),
                    ("Tạo mới", None),
                ],
            },
            status_code=422,
        )
    cc = counts.create_cash_count(db, get_ctx(request), inp)
    return redirect(
        f"/cash/counts/{cc.id}",
        f"Đã lập bảng kiểm kê {cc.number}",
        request=request,
    )


@router.get("/cash/counts/{cc_id:int}")
def counts_detail(
    request: Request,
    cc_id: int,
    user: CurrentUser = Depends(require("cash.view")),
    db: Session = Depends(get_tenant_db),
):
    cc = counts.get_cash_count(db, user.company_id, cc_id)
    remaining = counts.remaining_difference(db, cc)
    return render(
        request,
        "cash/count_detail.html",
        {
            "cc": cc,
            "remaining": remaining,
            "suggestion": counts.suggestion(cc, remaining),
            "linked": counts.linked_entries(db, cc),
            "creator": _user_name(db, cc.created_by),
            "breadcrumbs": [
                ("Tiền", None),
                ("Kiểm kê quỹ", "/cash/counts"),
                (cc.number, None),
            ],
        },
    )


@router.get("/cash/counts/{cc_id:int}/print.pdf")
def counts_print(
    request: Request,
    cc_id: int,
    user: CurrentUser = Depends(require("cash.view")),
    db: Session = Depends(get_tenant_db),
):
    cc = counts.get_cash_count(db, user.company_id, cc_id)
    return pdf_response(
        printing.cashcount_pdf(_company(db, user), cc), f"{cc.number}.pdf"
    )


# --------------------------------------------- receipt/payment purposes


@router.get("/cash/purposes")
def purposes_page(
    request: Request,
    user: CurrentUser = Depends(require("cash.manage")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    data = purposes.purposes(company)
    return render(
        request,
        "cash/purposes.html",
        {
            "data": {k: [p.__dict__ for p in v] for k, v in data.items()},
            "book_no": company.setting("cash_book_no", ""),
            "breadcrumbs": [
                ("Tiền", None),
                ("Phiếu thu, chi", "/cash"),
                ("Mục đích thu, chi", None),
            ],
        },
    )


@router.post("/cash/purposes")
def purposes_save(
    request: Request,
    form=Depends(form_data),
    user: CurrentUser = Depends(require("cash.manage")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    if form.get("reset") == "1":
        from app.features.org.service import update_company_settings

        update_company_settings(db, ctx, "cash_purposes", {})
        return redirect(
            "/cash/purposes",
            "Đã khôi phục danh sách mặc định",
            request=request,
        )
    data: dict[str, list[dict]] = {"in": [], "out": []}
    for key in ("in", "out"):
        try:
            n = min(int(form.get(f"{key}-count") or 0), 100)
        except ValueError:
            n = 0
        for i in range(n):
            data[key].append(
                {
                    "label": form.get(f"{key}-{i}-label") or "",
                    "account": form.get(f"{key}-{i}-account") or "",
                    "role": form.get(f"{key}-{i}-role") or "",
                }
            )
    purposes.save(db, ctx, data)
    book_no = (form.get("cash_book_no") or "").strip()[:20]
    company = _company(db, user)
    if book_no != company.setting("cash_book_no", ""):
        from app.features.org.service import update_company_settings

        update_company_settings(db, ctx, "cash_book_no", book_no)
    return redirect(
        "/cash/purposes", "Đã lưu mục đích thu, chi", request=request
    )


# ------------------------------------------ cash book import from Excel


@router.get("/cash/import")
def import_page(
    request: Request, _u: CurrentUser = Depends(require("import.manage"))
):
    return render(
        request,
        "cash/import.html",
        {
            "breadcrumbs": [
                ("Tiền", None),
                ("Phiếu thu, chi", "/cash"),
                ("Nhập sổ quỹ từ Excel", None),
            ]
        },
    )


@router.get("/cash/import/template.xlsx")
def import_template(
    user: CurrentUser = Depends(require("import.manage")),
    db: Session = Depends(get_tenant_db),
):
    return xlsx_response(
        excel.template_xlsx(_company(db, user)), "mau_nhap_so_quy.xlsx"
    )


@router.post("/cash/import")
async def import_upload(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require("import.manage")),
):
    data = await file.read()
    token = importer.save_upload(
        user.company_id, file.filename or "import.xlsx", data, user.id
    )
    return redirect(f"/cash/import/{token}", request=request)


def _import_options(params) -> importer.ImportOptions:
    bid = params.get("bank_account_id")
    return importer.ImportOptions(
        default_receipt_contra=(params.get("receipt_contra") or "").strip(),
        default_payment_contra=(params.get("payment_contra") or "").strip(),
        bank_account_id=int(bid) if bid and str(bid).isdigit() else None,
    )


@router.get("/cash/import/{token}")
def import_preview(
    request: Request,
    token: str,
    user: CurrentUser = Depends(require("import.manage")),
    db: Session = Depends(get_tenant_db),
):
    path, filename = importer.load_upload(user.company_id, token)
    company = _company(db, user)
    options = _import_options(request.query_params)
    preview = importer.build_preview(db, company, path, filename, options)
    db.rollback()
    return render(
        request,
        "cash/import_preview.html",
        {
            "p": preview,
            "token": token,
            "options": options,
            "banks": list_bank_accounts(db, company.id),
            "breadcrumbs": [
                ("Tiền", None),
                ("Nhập sổ quỹ từ Excel", "/cash/import"),
                (filename, None),
            ],
        },
    )


@router.post("/cash/import/{token}/confirm")
def import_confirm(
    request: Request,
    token: str,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("import.manage")),
    db: Session = Depends(get_tenant_db),
):
    path, filename = importer.load_upload(user.company_id, token)
    options = _import_options(form)
    preview = importer.confirm_import(
        db,
        get_ctx(request),
        path,
        filename,
        options=options,
        skip_errors=form.get("skip_errors") == "1",
        token=token,
    )
    importer.discard_upload(token, user.company_id)
    return redirect(
        "/cash/book?mode=all",
        f"Đã nhập {len(preview.valid_rows)} phiếu thu, chi từ {filename}",
        request=request,
    )


@router.post("/cash/import/{token}/discard")
def import_discard(
    request: Request,
    token: str,
    user: CurrentUser = Depends(require("import.manage")),
):
    importer.load_upload(user.company_id, token)
    importer.discard_upload(token, user.company_id)
    return redirect(
        "/cash/import", "Đã hủy phiên nhập dữ liệu", "info", request=request
    )
