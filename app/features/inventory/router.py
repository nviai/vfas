"""Inventory web routes.

Warehouses, opening stock, stock receipts/issues/transfers, issue
costing and stock ledgers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.dates import fmt_datetime, month_bounds, parse_date, today
from app.core.db import like_contains, ulower
from app.core.errors import BusinessError
from app.core.money import MoneyError, parse_vnd
from app.core.security import CurrentUser, require
from app.core.web import (
    form_multi,
    get_ctx,
    get_tenant_db,
    is_htmx,
    redirect,
    render,
)
from app.core.xlsx import pdf_response, xlsx_response
from app.features.inventory import costing, exports, reports, service
from app.features.inventory.models import (
    CANCELLED,
    DRAFT,
    ISSUE,
    KIND_LABELS,
    POSTED,
    RECEIPT,
    TRANSFER,
    CostingRun,
    StockDoc,
    Warehouse,
)
from app.features.items.models import Item
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.org.models import Company

router = APIRouter(prefix="/inventory")
PAGE = 50
KIND_PATH = {RECEIPT: "receipts", ISSUE: "issues", TRANSFER: "transfers"}
PATH_KIND = {v: k for k, v in KIND_PATH.items()}
KIND_TITLE = {RECEIPT: "Nhập kho", ISSUE: "Xuất kho", TRANSFER: "Chuyển kho"}


# ------------------------------------------------------------ helpers


@dataclass
class Period:
    mode: str
    start: date
    end: date
    label: str
    year: int | None = None
    quarter: int | None = None


def resolve_period(request: Request, company: Company) -> Period:
    qp = request.query_params
    mode = qp.get("mode") or "month"
    t = today()
    try:
        if mode == "quarter":
            y, q = (
                int(qp.get("year") or t.year),
                int(qp.get("quarter") or (t.month - 1) // 3 + 1),
            )
            s, e = date(y, 3 * q - 2, 1), month_bounds(y, 3 * q)[1]
            return Period("quarter", s, e, f"Quý {q} năm {y}", y, q)
        if mode == "year":
            y = int(qp.get("year") or t.year)
            return Period(
                "year", date(y, 1, 1), date(y, 12, 31), f"Năm {y}", y
            )
        if mode == "custom":
            s = parse_date(qp.get("start")) or company.start_date
            e = parse_date(qp.get("end")) or t
            s, e = min(s, e), max(s, e)
            return Period("custom", s, e, exports.period_label(s, e))
        y, m = (
            int(x)
            for x in (qp.get("month") or f"{t.year}-{t.month:02d}").split("-")[
                :2
            ]
        )
        s, e = month_bounds(y, m)
        return Period("month", s, e, f"Tháng {m:02d}/{y}", y)
    except (ValueError, TypeError):
        s, e = month_bounds(t.year, t.month)
        return Period("month", s, e, f"Tháng {t.month:02d}/{t.year}", t.year)


def _company(db: Session, user: CurrentUser) -> Company:
    return db.get(Company, user.company_id)


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_lines(form, kind: str) -> list[service.LineInput]:
    out = []
    try:
        n = int(form.get("lines-count") or 0)
    except ValueError:
        n = 0
    for i in range(min(n, 500)):
        g = lambda f, i=i: form.get(f"lines-{i}-{f}")  # noqa: E731
        item_id = _int(g("item_id"))
        qty_raw = (g("quantity") or "").strip()
        if not item_id and not qty_raw:
            continue
        qty = service.to_decimal(qty_raw, None)
        amount = 0
        price = service.to_decimal(g("unit_price"), Decimal(0)) or Decimal(0)
        if kind == RECEIPT:
            raw = (g("amount") or "").strip()
            try:
                amount = (
                    parse_vnd(raw)
                    if raw
                    else (service.money_round(price * qty) if qty else 0)
                )
            except MoneyError as e:
                raise BusinessError(f"Dòng {i + 1}: {e}") from e
        out.append(
            service.LineInput(
                item_id=item_id or 0,
                quantity=qty,
                unit_price=price,
                amount=amount,
                description=(g("description") or "").strip(),
                unit=(g("unit") or "").strip(),
            )
        )
    return out


def _parse_doc(form, kind: str) -> service.DocInput:
    try:
        d = parse_date(form.get("date"))
        doc_date = (
            parse_date(form.get("doc_date")) if form.get("doc_date") else None
        )
    except ValueError as e:
        raise BusinessError("Ngày không hợp lệ") from e
    return service.DocInput(
        kind=kind,
        date=d,
        doc_date=doc_date,
        warehouse_id=_int(form.get("warehouse_id")) or 0,
        to_warehouse_id=_int(form.get("to_warehouse_id")),
        partner_id=_int(form.get("partner_id")),
        contact_name=form.get("contact_name") or "",
        counter_account=(form.get("counter_account") or "").strip(),
        description=form.get("description") or "",
        ref_no=form.get("ref_no") or "",
        attached_docs=_int(form.get("attached_docs")) or 0,
        lines=_parse_lines(form, kind),
    )


def _lines_json(
    doc: StockDoc | None,
    inp: service.DocInput | None = None,
    db: Session | None = None,
) -> list[dict]:
    """Build the line rows for invoiceGrid (Alpine)."""
    if inp is not None:
        items = {
            it.id: it
            for it in db.scalars(
                select(Item).where(
                    Item.id.in_({ln.item_id for ln in inp.lines})
                )
            )
        }
        return [
            {
                "item_id": ln.item_id or "",
                "item_label": items[ln.item_id].label
                if ln.item_id in items
                else "",
                "description": ln.description,
                "unit": ln.unit,
                "quantity": float(ln.quantity)
                if ln.quantity is not None
                else None,
                "unit_price": float(ln.unit_price or 0) or None,
                "amount": ln.amount or None,
                "vat_rate": "KCT",
                "vat_amount": 0,
            }
            for ln in inp.lines
        ]
    if doc is None:
        return []
    return [
        {
            "item_id": ln.item_id,
            "item_label": ln.item.label,
            "description": ln.description,
            "unit": ln.unit,
            "quantity": float(ln.quantity),
            "unit_price": float(ln.unit_price or 0) or None,
            "amount": int(ln.amount) or None,
            "vat_rate": "KCT",
            "vat_amount": 0,
        }
        for ln in doc.lines
    ]


def _form_values(doc: StockDoc | None, inp: service.DocInput | None) -> dict:
    src = inp or doc
    if src is None:
        return {
            "date": today(),
            "doc_date": None,
            "warehouse_id": None,
            "to_warehouse_id": None,
            "partner_id": None,
            "contact_name": "",
            "counter_account": "",
            "description": "",
            "ref_no": "",
            "attached_docs": 0,
        }
    return {
        k: getattr(src, k)
        for k in (
            "date",
            "doc_date",
            "warehouse_id",
            "to_warehouse_id",
            "partner_id",
            "contact_name",
            "counter_account",
            "description",
            "ref_no",
            "attached_docs",
        )
    }


def _form_ctx(
    db: Session,
    user: CurrentUser,
    kind: str,
    doc: StockDoc | None = None,
    inp: service.DocInput | None = None,
    errors: list[str] | None = None,
) -> dict:
    whs = service.list_warehouses(db, user.company_id)
    if not whs:
        whs = [service.default_warehouse(db, user.company_id)]
        db.commit()
    values = _form_values(doc, inp)
    if not values["warehouse_id"]:
        values["warehouse_id"] = next(
            (w.id for w in whs if w.is_default), whs[0].id
        )
    from app.features.partners.models import Partner

    partner_label = None
    if values.get("partner_id"):
        p = db.get(Partner, values["partner_id"])
        partner_label = (
            p.label if p and p.company_id == user.company_id else None
        )
    path = KIND_PATH[kind]
    return {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "path": path,
        "doc": doc,
        "v": values,
        "lines": _lines_json(doc, inp, db),
        "warehouses": whs,
        "partner_label": partner_label,
        "errors_list": errors or [],
        "counter_hint": {
            RECEIPT: (
                "VD: 3381 (thừa chờ xử lý), 711 (nhập khác), "
                "154 (thành phẩm), 1388"
            ),
            ISSUE: (
                "VD: 6422 (quản lý), 6421 (bán hàng), "
                "154 (sản xuất), 1381 (thiếu)"
            ),
        }.get(kind),
        "breadcrumbs": [
            ("Kho", None),
            (KIND_TITLE[kind], f"/inventory/{path}"),
            ((doc.display_number if doc else "Tạo mới"), None),
        ],
    }


def _doc_or_404(db: Session, ctx, kind: str, ident: int) -> StockDoc:
    if kind == RECEIPT:
        return service.doc_for_entry(db, ctx, ident)
    return service.get_doc(db, ctx, ident, kind)


def _history(db: Session, doc: StockDoc) -> list[dict]:
    from app.core.audit import AuditLog

    conds = [
        (AuditLog.entity_type == "stock_doc")
        & (AuditLog.entity_id == str(doc.id))
    ]
    if doc.entry_id:
        conds.append(
            (AuditLog.entity_type == "entry")
            & (AuditLog.entity_id == str(doc.entry_id))
        )
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.company_id == doc.company_id, or_(*conds))
        .order_by(AuditLog.id.desc())
        .limit(30)
    )
    tone = {"post": "green", "cancel": "red", "create": "brand"}
    out = []
    for log in rows:
        act = log.action.split(".")[-1]
        out.append(
            {
                "at": fmt_datetime(log.at),
                "actor": log.username or "",
                "action": log.summary or log.action,
                "detail": log.reason,
                "tone": tone.get(act, "gray"),
            }
        )
    return out


# --------------------------------------------------------- warehouses


@router.get("/warehouses")
def warehouses_page(
    request: Request,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    whs = service.list_warehouses(db, user.company_id, active=None)
    return render(
        request,
        "inventory/warehouses.html",
        {
            "warehouses": whs,
            "allow_negative": service.allow_negative(db, user.company_id),
            "breadcrumbs": [("Danh mục", None), ("Kho", None)],
        },
    )


@router.post("/settings")
async def inventory_settings_save(
    request: Request,
    user: CurrentUser = Depends(require("inventory.manage")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    from app.features.org.service import update_company_settings

    cfg = service.inventory_settings(db, user.company_id)
    cfg["allow_negative_stock"] = form.get("allow_negative_stock") == "1"
    update_company_settings(db, get_ctx(request), "inventory", cfg)
    return redirect(
        "/inventory/warehouses", "Đã lưu thiết lập kho", request=request
    )


@router.post("/warehouses")
async def warehouse_save(
    request: Request,
    user: CurrentUser = Depends(require("inventory.manage")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    wid = _int(form.get("id"))
    wh = service.get_warehouse(db, user.company_id, wid) if wid else None
    w = service.save_warehouse(
        db,
        get_ctx(request),
        wh=wh,
        code=form.get("code") or "",
        name=form.get("name") or "",
        address=form.get("address") or "",
        keeper=form.get("keeper") or "",
        is_default=form.get("is_default") == "1",
        is_active=form.get("is_active", "1") == "1",
        note=form.get("note") or "",
    )
    return redirect(
        "/inventory/warehouses", f"Đã lưu kho {w.code}", request=request
    )


# ------------------------------------------------------ opening stock


@router.get("/opening")
def opening_page(
    request: Request,
    warehouse_id: int | None = None,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    whs = service.list_warehouses(db, user.company_id)
    if not whs:
        whs = [service.default_warehouse(db, user.company_id)]
        db.commit()
    wh = next((w for w in whs if w.id == warehouse_id), None) or next(
        (w for w in whs if w.is_default), whs[0]
    )
    rows = [
        m
        for m in service.list_opening(db, user.company_id)
        if m.warehouse_id == wh.id
    ]
    lines = [
        {
            "item_id": m.item_id,
            "item_label": m.item.label,
            "description": m.item.name,
            "unit": m.item.unit,
            "quantity": float(m.qty_in),
            "unit_price": float(m.unit_cost or 0) or None,
            "amount": int(m.amount),
            "vat_rate": "KCT",
            "vat_amount": 0,
        }
        for m in rows
    ]
    from sqlalchemy import func

    from app.features.inventory.models import StockMove
    from app.features.journal import locks
    from app.features.journal.models import OpeningBalance

    gl = dict(
        db.execute(
            select(
                OpeningBalance.account,
                func.sum(OpeningBalance.debit - OpeningBalance.credit),
            )
            .where(
                OpeningBalance.company_id == company.id,
                OpeningBalance.account.like("15%"),
            )
            .group_by(OpeningBalance.account)
        ).all()
    )
    stock = dict(
        db.execute(
            select(StockMove.stock_account, func.sum(StockMove.amount))
            .where(
                StockMove.company_id == company.id,
                StockMove.source == "opening",
            )
            .group_by(StockMove.stock_account)
        ).all()
    )
    compare = [
        {
            "account": a,
            "gl": int(gl.get(a) or 0),
            "stock": int(stock.get(a) or 0),
        }
        for a in sorted(set(gl) | set(stock))
    ]
    return render(
        request,
        "inventory/opening.html",
        {
            "company": company,
            "warehouses": whs,
            "wh": wh,
            "lines": lines,
            "rows": rows,
            "compare": compare,
            "locked": locks.any_locked(db, company.id),
            "opening_date": service.opening_date(company),
            "breadcrumbs": [("Danh mục", None), ("Tồn kho đầu kỳ", None)],
        },
    )


@router.post("/opening")
async def opening_save(
    request: Request,
    user: CurrentUser = Depends(require("inventory.manage")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    wh_id = _int(form.get("warehouse_id"))
    service.get_warehouse(db, user.company_id, wh_id or 0)
    rows = []
    for ln in _parse_lines(form, RECEIPT):
        rows.append(
            service.OpeningRow(
                item_id=ln.item_id,
                warehouse_id=wh_id,
                quantity=ln.quantity or Decimal(0),
                amount=ln.amount,
            )
        )
    service.replace_opening(db, get_ctx(request), rows, warehouse_id=wh_id)
    return redirect(
        f"/inventory/opening?warehouse_id={wh_id}",
        "Đã lưu tồn kho đầu kỳ",
        request=request,
    )


# ------------------------------------------------ stock document list


def _list(
    request: Request,
    kind: str,
    user: CurrentUser,
    db: Session,
    status: str,
    q: str,
    page: int,
):
    stmt = select(StockDoc).where(
        StockDoc.company_id == user.company_id, StockDoc.kind == kind
    )
    if status in (DRAFT, POSTED, CANCELLED):
        stmt = stmt.where(StockDoc.status == status)
    if q:
        term = q.strip().lower()
        stmt = stmt.where(
            or_(
                like_contains(ulower(StockDoc.number), term),
                like_contains(ulower(StockDoc.description), term),
                like_contains(ulower(StockDoc.contact_name), term),
            )
        )
    period = None
    if request.query_params.get("mode"):
        period = resolve_period(request, _company(db, user))
        stmt = stmt.where(
            StockDoc.date >= period.start, StockDoc.date <= period.end
        )
    page = max(1, page)
    docs = list(
        db.scalars(
            stmt.order_by(StockDoc.date.desc(), StockDoc.id.desc())
            .limit(PAGE + 1)
            .offset((page - 1) * PAGE)
        )
    )
    ctx = {
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "path": KIND_PATH[kind],
        "title": KIND_TITLE[kind],
        "docs": docs[:PAGE],
        "has_next": len(docs) > PAGE,
        "page": page,
        "status": status,
        "q": q,
        "breadcrumbs": [("Kho", None), (KIND_TITLE[kind], None)],
    }
    if is_htmx(request) and request.headers.get("hx-target") == "doc-results":
        return render(request, "inventory/_doc_rows.html", ctx)
    return render(request, "inventory/docs.html", ctx)


@router.get("/receipts")
def receipts(
    request: Request,
    status: str = "",
    q: str = "",
    page: int = 1,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    return _list(request, RECEIPT, user, db, status, q, page)


@router.get("/issues")
def issues(
    request: Request,
    status: str = "",
    q: str = "",
    page: int = 1,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    return _list(request, ISSUE, user, db, status, q, page)


@router.get("/transfers")
def transfers(
    request: Request,
    status: str = "",
    q: str = "",
    page: int = 1,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    return _list(request, TRANSFER, user, db, status, q, page)


# ------------------------------------------------------ create / edit


def _new(request: Request, kind: str, user: CurrentUser, db: Session):
    return render(
        request, "inventory/doc_form.html", _form_ctx(db, user, kind)
    )


@router.get("/receipts/new")
def receipt_new(
    request: Request,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
):
    return _new(request, RECEIPT, user, db)


@router.get("/issues/new")
def issue_new(
    request: Request,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
):
    return _new(request, ISSUE, user, db)


@router.get("/transfers/new")
def transfer_new(
    request: Request,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
):
    return _new(request, TRANSFER, user, db)


async def _create(
    request: Request, kind: str, user: CurrentUser, db: Session, form
):
    ctx = get_ctx(request)
    post = form.get("action") == "post"
    if post and not user.can("inventory.post"):
        post = False
    inp = None
    try:
        inp = _parse_doc(form, kind)
        doc = service.create_doc(db, ctx, inp)
    except BusinessError as e:
        db.rollback()
        errors = e.details.get("errors") or [e.message]
        return render(
            request,
            "inventory/doc_form.html",
            _form_ctx(db, user, kind, inp=inp, errors=errors),
            status_code=422 if not is_htmx(request) else 200,
        )
    if post:
        try:
            doc = service.post_doc(db, ctx, doc)
        except BusinessError as e:
            db.rollback()
            return redirect(
                doc.url
                if doc.kind != RECEIPT
                else f"/inventory/receipts/{doc.entry_id}",
                f"Đã lưu nháp nhưng chưa ghi sổ được: {e.message}",
                "error",
                request=request,
            )
        return redirect(doc.url, f"Đã ghi sổ {doc.number}", request=request)
    return redirect(doc.url, "Đã lưu nháp", request=request)


@router.post("/receipts")
async def receipt_create(
    request: Request,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    return await _create(request, RECEIPT, user, db, form)


@router.post("/issues")
async def issue_create(
    request: Request,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    return await _create(request, ISSUE, user, db, form)


@router.post("/transfers")
async def transfer_create(
    request: Request,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    return await _create(request, TRANSFER, user, db, form)


@router.get("/{path}/{ident}/edit")
def doc_edit(
    request: Request,
    path: str,
    ident: int,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
):
    kind = PATH_KIND.get(path)
    if kind is None:
        from app.core.errors import NotFoundError

        raise NotFoundError()
    doc = _doc_or_404(db, get_ctx(request), kind, ident)
    if service.effective_status(db, doc) != DRAFT:
        return redirect(
            doc.url, "Chứng từ đã ghi sổ không sửa được", "warning"
        )
    return render(
        request, "inventory/doc_form.html", _form_ctx(db, user, kind, doc=doc)
    )


@router.post("/{path}/{ident}/edit")
async def doc_update(
    request: Request,
    path: str,
    ident: int,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    kind = PATH_KIND.get(path)
    if kind is None:
        from app.core.errors import NotFoundError

        raise NotFoundError()
    ctx = get_ctx(request)
    doc = _doc_or_404(db, ctx, kind, ident)
    inp = None
    try:
        inp = _parse_doc(form, kind)
        service.update_doc(db, ctx, doc, inp)
    except BusinessError as e:
        db.rollback()
        errors = e.details.get("errors") or [e.message]
        return render(
            request,
            "inventory/doc_form.html",
            _form_ctx(db, user, kind, doc=doc, inp=inp, errors=errors),
            status_code=422,
        )
    if form.get("action") == "post" and user.can("inventory.post"):
        try:
            service.post_doc(db, ctx, doc)
        except BusinessError as e:
            db.rollback()
            return redirect(
                doc.url,
                f"Đã lưu nháp nhưng chưa ghi sổ được: {e.message}",
                "error",
                request=request,
            )
        return redirect(doc.url, f"Đã ghi sổ {doc.number}", request=request)
    return redirect(doc.url, "Đã lưu nháp", request=request)


# ---------------------------------------------------- detail, actions


@router.post("/{path}/{doc_id}/post")
async def doc_post(
    request: Request,
    path: str,
    doc_id: int,
    user: CurrentUser = Depends(require("inventory.post")),
    db: Session = Depends(get_tenant_db),
):
    doc = service.get_doc(db, get_ctx(request), doc_id, PATH_KIND.get(path))
    service.post_doc(db, get_ctx(request), doc)
    return redirect(doc.url, f"Đã ghi sổ {doc.number}", request=request)


@router.post("/{path}/{doc_id}/cancel")
async def doc_cancel(
    request: Request,
    path: str,
    doc_id: int,
    user: CurrentUser = Depends(require("inventory.cancel")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    doc = service.get_doc(db, get_ctx(request), doc_id, PATH_KIND.get(path))
    service.cancel_doc(db, get_ctx(request), doc, form.get("reason") or "")
    return redirect(
        doc.url, f"Đã hủy {doc.number}", "warning", request=request
    )


@router.post("/{path}/{doc_id}/delete")
async def doc_delete(
    request: Request,
    path: str,
    doc_id: int,
    user: CurrentUser = Depends(require("inventory.create")),
    db: Session = Depends(get_tenant_db),
):
    kind = PATH_KIND.get(path)
    doc = service.get_doc(db, get_ctx(request), doc_id, kind)
    service.delete_doc(db, get_ctx(request), doc)
    return redirect(
        f"/inventory/{path}", "Đã xóa chứng từ nháp", request=request
    )


@router.get("/{path}/{ident}/print.pdf")
def doc_print(
    request: Request,
    path: str,
    ident: int,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    kind = PATH_KIND.get(path)
    if kind is None:
        from app.core.errors import NotFoundError

        raise NotFoundError()
    doc = _doc_or_404(db, get_ctx(request), kind, ident)
    entry = db.get(JournalEntry, doc.entry_id) if doc.entry_id else None
    from app.features.inventory.models import StockMove

    moves = list(
        db.scalars(
            select(StockMove).where(
                StockMove.company_id == doc.company_id,
                StockMove.stock_doc_id == doc.id,
            )
        )
    )
    data = exports.doc_pdf(_company(db, user), doc, entry, moves)
    return pdf_response(data, f"{doc.number or 'nhap-' + str(doc.id)}.pdf")


# ------------------------------------------------------------ costing


@router.get("/costing")
def costing_page(
    request: Request,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    return render(
        request,
        "inventory/costing.html",
        {
            "months": costing.months_status(db, company),
            "today": today(),
            "breadcrumbs": [("Kho", None), ("Tính giá xuất kho", None)],
        },
    )


@router.post("/costing/run")
async def costing_run(
    request: Request,
    user: CurrentUser = Depends(require("inventory.costing")),
    db: Session = Depends(get_tenant_db),
    form=Depends(form_multi),
):
    y, m = _int(form.get("year")), _int(form.get("month"))
    if not y or not m or not 1 <= m <= 12:
        raise BusinessError("Chọn tháng cần tính giá")
    run = costing.run_costing(db, get_ctx(request), y, m)
    from app.core.money import format_vnd

    return redirect(
        "/inventory/costing",
        f"Đã tính giá xuất kho tháng {m:02d}/{y}: giá vốn "
        f"{format_vnd(run.total_cost)} đồng",
        request=request,
    )


@router.get("/costing/{entry_id}")
def costing_detail(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    entry = js.get_entry(db, ctx, entry_id, doc_types=("GV",))
    run = (
        db.scalar(
            select(CostingRun).where(
                CostingRun.company_id == ctx.company_id,
                CostingRun.id == entry.source_id,
            )
        )
        if entry.source_id
        else None
    )
    items = {
        it.id: it
        for it in db.scalars(
            select(Item).where(Item.company_id == ctx.company_id)
        )
    }
    from app.features.inventory.models import StockMove

    moves = []
    if run is not None:
        s, e = month_bounds(run.year, run.month)
        moves = list(
            db.scalars(
                select(StockMove)
                .where(
                    StockMove.company_id == ctx.company_id,
                    StockMove.date >= s,
                    StockMove.date <= e,
                    StockMove.valued.is_(False),
                )
                .order_by(StockMove.date, StockMove.id)
            )
        )
    return render(
        request,
        "inventory/costing_detail.html",
        {
            "entry": entry,
            "run": run,
            "items": items,
            "moves": moves,
            "breadcrumbs": [
                ("Kho", None),
                ("Tính giá xuất kho", "/inventory/costing"),
                (entry.display_number, None),
            ],
        },
    )


# ------------------------------------------------------ stock ledgers


def _report_ctx(request: Request, user: CurrentUser, db: Session):
    company = _company(db, user)
    period = resolve_period(request, company)
    qp = request.query_params
    item_id = _int(qp.get("item_id"))
    wh_id = _int(qp.get("warehouse_id"))
    item = db.get(Item, item_id) if item_id else None
    if item is not None and item.company_id != user.company_id:
        item = None
    if item is None:
        lst = reports.stock_items(db, user.company_id)
        item = lst[0] if lst else None
    wh = db.get(Warehouse, wh_id) if wh_id else None
    if wh is not None and wh.company_id != user.company_id:
        wh = None
    return company, period, item, wh


def _qs(request: Request) -> str:
    return str(request.url.query)


@router.get("/stock-card")
@router.get("/ledger")
def ledger_page(
    request: Request,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    card = request.url.path.endswith("stock-card")
    company, period, item, wh = _report_ctx(request, user, db)
    led = (
        reports.item_ledger(
            db,
            user.company_id,
            item.id,
            period.start,
            period.end,
            wh.id if wh else None,
        )
        if item
        else None
    )
    code, title = reports.FORMS["card" if card else "ledger"]
    base = "/inventory/stock-card" if card else "/inventory/ledger"
    return render(
        request,
        "inventory/ledger.html",
        {
            "card": card,
            "led": led,
            "period": period,
            "item": item,
            "wh": wh,
            "company": company,
            "warehouses": service.list_warehouses(
                db, user.company_id, active=None
            ),
            "form_code": code,
            "title": title,
            "base": base,
            "qs": _qs(request),
            "breadcrumbs": [
                ("Kho", None),
                ("Thẻ kho" if card else "Sổ chi tiết hàng hóa", None),
            ],
        },
    )


@router.get("/stock-card.pdf")
@router.get("/ledger.pdf")
@router.get("/stock-card.xlsx")
@router.get("/ledger.xlsx")
def ledger_export(
    request: Request,
    user: CurrentUser = Depends(require("inventory.export")),
    db: Session = Depends(get_tenant_db),
):
    path = request.url.path
    card = "stock-card" in path
    company, period, item, wh = _report_ctx(request, user, db)
    if item is None:
        raise BusinessError("Chưa có hàng hóa phát sinh kho")
    led = reports.item_ledger(
        db,
        user.company_id,
        item.id,
        period.start,
        period.end,
        wh.id if wh else None,
    )
    name = (
        f"{'the-kho' if card else 'so-chi-tiet'}-{item.code}-"
        f"{period.start:%Y%m%d}-{period.end:%Y%m%d}"
    )
    if path.endswith(".pdf"):
        return pdf_response(
            exports.ledger_pdf(company, led, card=card), name + ".pdf"
        )
    data = (
        exports.card_xlsx(company, led)
        if card
        else exports.ledger_xlsx(company, led)
    )
    return xlsx_response(data, name + ".xlsx")


@router.get("/summary")
def summary_page(
    request: Request,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    company, period, _item, wh = _report_ctx(request, user, db)
    by_wh = request.query_params.get("by_warehouse") == "1"
    s = reports.summary(
        db,
        user.company_id,
        period.start,
        period.end,
        warehouse_id=wh.id if wh else None,
        by_warehouse=by_wh,
    )
    from app.features.journal import queries

    gl = {
        acc: queries.balance(
            db, user.company_id, acc, upto=period.end, exact=True
        )
        for acc in s.by_account
    }
    code, title = reports.FORMS["summary"]
    return render(
        request,
        "inventory/summary.html",
        {
            "s": s,
            "period": period,
            "wh": wh,
            "by_wh": by_wh,
            "company": company,
            "gl": gl,
            "form_code": code,
            "title": title,
            "warehouses": service.list_warehouses(
                db, user.company_id, active=None
            ),
            "qs": _qs(request),
            "breadcrumbs": [
                ("Kho", None),
                ("Tổng hợp nhập – xuất – tồn", None),
            ],
        },
    )


@router.get("/summary.pdf")
@router.get("/summary.xlsx")
def summary_export(
    request: Request,
    user: CurrentUser = Depends(require("inventory.export")),
    db: Session = Depends(get_tenant_db),
):
    company, period, _item, wh = _report_ctx(request, user, db)
    s = reports.summary(
        db,
        user.company_id,
        period.start,
        period.end,
        warehouse_id=wh.id if wh else None,
        by_warehouse=request.query_params.get("by_warehouse") == "1",
    )
    name = f"tong-hop-nxt-{period.start:%Y%m%d}-{period.end:%Y%m%d}"
    if request.url.path.endswith(".pdf"):
        return pdf_response(exports.summary_pdf(company, s, wh), name + ".pdf")
    return xlsx_response(exports.summary_xlsx(company, s, wh), name + ".xlsx")


# Registered last so it does not shadow specific routes
# (/costing/{id}…).
@router.get("/{path}/{ident}")
def doc_detail(
    request: Request,
    path: str,
    ident: int,
    user: CurrentUser = Depends(require("inventory.view")),
    db: Session = Depends(get_tenant_db),
):
    kind = PATH_KIND.get(path)
    if kind is None:
        from app.core.errors import NotFoundError

        raise NotFoundError()
    ctx = get_ctx(request)
    doc = _doc_or_404(db, ctx, kind, ident)
    entry = db.get(JournalEntry, doc.entry_id) if doc.entry_id else None
    status = service.effective_status(db, doc)
    gv_pending = (
        kind != RECEIPT
        and status == POSTED
        and any(ln.amount == 0 for ln in doc.lines)
    )
    from app.features.inventory.models import StockMove

    moves = list(
        db.scalars(
            select(StockMove)
            .where(
                StockMove.company_id == doc.company_id,
                StockMove.stock_doc_id == doc.id,
            )
            .order_by(StockMove.id)
        )
    )
    return render(
        request,
        "inventory/doc_detail.html",
        {
            "doc": doc,
            "entry": entry,
            "status": status,
            "kind": kind,
            "path": path,
            "kind_label": KIND_LABELS[kind],
            "moves": moves,
            "history": _history(db, doc),
            "gv_pending": gv_pending,
            "today": today(),
            "breadcrumbs": [
                ("Kho", None),
                (KIND_TITLE[kind], f"/inventory/{path}"),
                (doc.display_number, None),
            ],
        },
    )
