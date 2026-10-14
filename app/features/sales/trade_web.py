"""Shared web layer for sales and purchase documents.

Covers the list, form, detail page and Excel export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import AuditLog
from app.core.dates import fmt_date, parse_date, today
from app.core.errors import BusinessError, PermissionDeniedError
from app.core.security import CurrentUser, require
from app.core.web import (
    form_data,
    get_ctx,
    get_tenant_db,
    is_htmx,
    redirect,
    render,
)
from app.core.xlsx import Col, ReportBook, xlsx_response
from app.features.accounts.models import Account
from app.features.bank.service import list_bank_accounts
from app.features.journal.models import JournalEntry
from app.features.org.models import Company
from app.features.sales.common import (
    GRID_RATES,
    VAT_LABELS,
    VAT_ORDER,
    vat_groups,
)

PAGE = 50


@dataclass
class TradeConfig:
    module: str  # sales | purchases
    base: str  # /sales
    service: ModuleType
    main_kind: str  # sale | purchase
    titles: dict  # kind -> title
    list_titles: dict
    subtitles: dict
    partner_label: str
    partner_role: str
    price_field: str
    account_params: dict
    account_label: str
    show_discount: bool
    deductible: bool
    payment_labels: dict
    section: str
    icon: str
    # Account attribute on a line: revenue_account | debit_account.
    account_attr: str
    # JS expression that reads the account from the item details.
    item_account_expr: str
    extra: dict = field(default_factory=dict)

    @property
    def perm(self) -> str:
        return self.module

    def kind_path(self, kind: str) -> str:
        return self.base if kind == self.main_kind else f"{self.base}/returns"


def _filters(cfg: TradeConfig, kind: str, params) -> object:
    f = cfg.service.ListFilters(kind=kind)
    f.q = (params.get("q") or "").strip()
    f.status = params.get("status") or ""
    f.payment = params.get("payment") or ""
    try:
        f.start = parse_date(params.get("start"))
        f.end = parse_date(params.get("end"))
    except ValueError:
        f.start = f.end = None
    return f


def _account_names(db: Session, company_id: int) -> dict[str, str]:
    return {
        a.code: a.name
        for a in db.scalars(
            select(Account).where(Account.company_id == company_id)
        )
    }


def _history(db: Session, company_id: int, entry_id: int) -> list[dict]:
    logs = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.company_id == company_id,
            AuditLog.entity_type == "entry",
            AuditLog.entity_id == str(entry_id),
        )
        .order_by(AuditLog.id.desc())
    )
    icons = {
        "entry.create": "plus",
        "entry.update": "pencil",
        "entry.post": "check",
        "entry.cancel": "ban",
        "attachment.add": "paperclip",
        "attachment.delete": "trash",
        "entry.update_info": "pencil",
    }
    tones = {"entry.post": "green", "entry.cancel": "red"}
    return [
        {
            "at": log.at,
            "actor": log.username or "",
            "action": log.summary or log.action,
            "detail": log.reason,
            "icon": icons.get(log.action),
            "tone": tones.get(log.action, "gray"),
        }
        for log in logs
    ]


def _crumbs(cfg: TradeConfig, kind: str, last: str | None = None) -> list:
    """Breadcrumbs: section › (returns/allowances) › document no."""
    if not last and kind == cfg.main_kind:
        return []
    out = [(cfg.section, cfg.base)]
    if kind != cfg.main_kind:
        out.append(
            (cfg.list_titles[kind], cfg.kind_path(kind) if last else None)
        )
    if last:
        out.append((last, None))
    return out


def register(router: APIRouter, cfg: TradeConfig) -> None:
    svc = cfg.service

    def list_view(request: Request, kind: str, db: Session, user: CurrentUser):
        ctx = get_ctx(request)
        params = request.query_params
        f = _filters(cfg, kind, params)
        try:
            page = max(1, int(params.get("page") or 1))
        except ValueError:
            page = 1
        rows, totals = svc.list_docs(
            db, ctx, f, limit=PAGE, offset=(page - 1) * PAGE
        )
        context = {
            "cfg": cfg,
            "kind": kind,
            "rows": rows,
            "totals": totals,
            "f": f,
            "page": page,
            "pages": max(1, (totals["count"] + PAGE - 1) // PAGE),
            "title": cfg.list_titles[kind],
            "subtitle": cfg.subtitles[kind],
            "breadcrumbs": _crumbs(cfg, kind),
        }
        if (
            is_htmx(request)
            and request.headers.get("hx-target") == "trade-results"
        ):
            return render(request, "sales/_trade_results.html", context)
        return render(request, "sales/trade_list.html", context)

    @router.get(cfg.base)
    def trade_list(
        request: Request,
        user: CurrentUser = Depends(require(f"{cfg.perm}.view")),
        db: Session = Depends(get_tenant_db),
    ):
        return list_view(request, cfg.main_kind, db, user)

    @router.get(f"{cfg.base}/returns")
    def trade_returns(
        request: Request,
        user: CurrentUser = Depends(require(f"{cfg.perm}.view")),
        db: Session = Depends(get_tenant_db),
    ):
        return list_view(request, "return", db, user)

    @router.get(f"{cfg.base}/export.xlsx")
    def trade_export(
        request: Request,
        kind: str = "",
        user: CurrentUser = Depends(require(f"{cfg.perm}.export")),
        db: Session = Depends(get_tenant_db),
    ):
        ctx = get_ctx(request)
        kind = kind if kind in (cfg.main_kind, "return") else cfg.main_kind
        f = _filters(cfg, kind, request.query_params)
        rows, totals = svc.list_docs(db, ctx, f, limit=100_000)
        company = db.get(Company, ctx.company_id)
        book = ReportBook()
        sub = (
            "Tất cả"
            if not (f.start or f.end)
            else f"Từ ngày {fmt_date(f.start)} đến ngày {fmt_date(f.end)}"
        )
        ws = book.add_sheet(
            cfg.list_titles[kind][:31],
            company=company,
            title=cfg.list_titles[kind].upper(),
            subtitle=sub,
            columns=[
                Col("Ngày", 12, "date"),
                Col("Số chứng từ", 16),
                Col(cfg.partner_label, 34),
                Col("MST", 14),
                Col("Số hóa đơn", 18),
                Col("Tiền hàng", 16, "money"),
                Col("Thuế GTGT", 14, "money"),
                Col("Tổng thanh toán", 16, "money"),
                Col("Trạng thái", 12),
            ],
        )
        status = {
            "draft": "Nháp",
            "posted": "Đã ghi sổ",
            "cancelled": "Đã hủy",
        }
        for doc, e in rows:
            name = getattr(doc, "partner_name", None) or getattr(
                doc, "seller_name", ""
            )
            tax = getattr(doc, "buyer_tax_code", None) or getattr(
                doc, "seller_tax_code", ""
            )
            net = doc.goods_amount - getattr(doc, "discount_amount", 0)
            book.row(
                ws,
                [
                    e.date,
                    e.number or f"Nháp #{e.id}",
                    name,
                    tax,
                    f"{doc.invoice_series}-{doc.invoice_no}".strip("-"),
                    net,
                    doc.vat_amount,
                    doc.total_amount,
                    status.get(e.status, e.status),
                ],
            )
        book.total_row(ws, "Cộng", sum_cols=[5, 6, 7])
        return xlsx_response(
            book.to_bytes(), f"{cfg.module}-{today():%Y%m%d}.xlsx"
        )

    def form_context(
        request, db, ctx, *, kind, form, lines, errors, entry=None
    ):
        return {
            "cfg": cfg,
            "kind": kind,
            "form": form,
            "lines": lines,
            "errors": errors,
            "entry": entry,
            "title": cfg.titles[kind],
            "rates": GRID_RATES,
            "banks": [
                (b.id, b.label) for b in list_bank_accounts(db, ctx.company_id)
            ],
            "breadcrumbs": _crumbs(
                cfg, kind, entry.display_number if entry else "Tạo mới"
            ),
        }

    @router.get(f"{cfg.base}/new")
    def trade_new(
        request: Request,
        kind: str = "",
        source: int | None = None,
        user: CurrentUser = Depends(require(f"{cfg.perm}.create")),
        db: Session = Depends(get_tenant_db),
    ):
        ctx = get_ctx(request)
        kind = kind if kind in (cfg.main_kind, "return") else cfg.main_kind
        form, lines = {"date": today(), "payment_method": "credit"}, []
        if source:
            form, lines = svc.return_defaults(db, ctx, source)
            form["date"] = today()
            kind = "return"
        return render(
            request,
            "sales/trade_form.html",
            form_context(
                request, db, ctx, kind=kind, form=form, lines=lines, errors={}
            ),
        )

    def _save(request, db, form, *, kind, doc=None, entry=None):
        ctx = get_ctx(request)
        if (
            form.get("partner_id")
            and not str(form.get("partner_id")).isdigit()
            and not form.get("partner_name")
        ):
            # The combobox allows typing a new name.
            form["partner_name"] = form["partner_id"]
            form["partner_id"] = ""
        inp, errors, lines = svc.parse_form(db, ctx, form, kind)
        if inp is None:
            shown = dict(form)
            shown["partner_label"] = (
                form.get("partner_label") or form.get("partner_name") or ""
            )
            return None, render(
                request,
                "sales/trade_form.html",
                form_context(
                    request,
                    db,
                    ctx,
                    kind=kind,
                    form=shown,
                    lines=[ln.grid_dict() for ln in lines],
                    errors=errors,
                    entry=entry,
                ),
                status_code=422,
            )
        want_post = form.get("action") == "post"
        if want_post and not ctx.can(f"{cfg.perm}.post"):
            raise PermissionDeniedError()
        if doc is None:
            doc = svc.create(db, ctx, inp)
        else:
            svc.update(db, ctx, doc, inp)
        if want_post:
            from app.features.journal import service as js

            e = db.get(JournalEntry, doc.entry_id)
            try:
                js.post_entry(db, ctx, e)
            except BusinessError as err:
                return doc, redirect(
                    f"{cfg.base}/{doc.entry_id}/edit",
                    f"Đã lưu nháp nhưng chưa ghi sổ được: {err.message}",
                    "error",
                    request=request,
                )
            return doc, redirect(
                f"{cfg.base}/{doc.entry_id}",
                f"Đã ghi sổ {e.number}",
                request=request,
            )
        return doc, redirect(
            f"{cfg.base}/{doc.entry_id}",
            "Đã lưu chứng từ nháp",
            request=request,
        )

    @router.post(cfg.base)
    def trade_create(
        request: Request,
        form: dict = Depends(form_data),
        user: CurrentUser = Depends(require(f"{cfg.perm}.create")),
        db: Session = Depends(get_tenant_db),
    ):
        kind = (
            form.get("kind")
            if form.get("kind") in (cfg.main_kind, "return")
            else cfg.main_kind
        )
        _doc, resp = _save(request, db, dict(form), kind=kind)
        return resp

    @router.get(cfg.base + "/{entry_id:int}")
    def trade_detail(
        request: Request,
        entry_id: int,
        user: CurrentUser = Depends(require(f"{cfg.perm}.view")),
        db: Session = Depends(get_tenant_db),
    ):
        ctx = get_ctx(request)
        entry, doc = svc.get_doc(db, ctx, entry_id)
        kind = (
            doc.kind
            if doc
            else (
                "return" if entry.doc_type in ("TL", "TM") else cfg.main_kind
            )
        )
        names = _account_names(db, ctx.company_id)
        acc_lines = []
        for ln in entry.lines:
            acc_lines.append(
                {
                    "debit": ln.debit_account,
                    "credit": ln.credit_account,
                    "amount": ln.amount,
                    "description": ln.description,
                    "debit_name": names.get(ln.debit_account, ""),
                    "credit_name": names.get(ln.credit_account, ""),
                }
            )
        groups = []
        if doc:
            by_rate = vat_groups(
                [
                    type(
                        "L",
                        (),
                        {
                            "vat_rate": ln.vat_rate,
                            "net": ln.amount,
                            "vat_amount": ln.vat_amount,
                        },
                    )
                    for ln in doc.lines
                ]
            )
            groups = [(r, by_rate[r]) for r in VAT_ORDER if r in by_rate]
        original = (
            db.get(JournalEntry, doc.original_entry_id)
            if doc and doc.original_entry_id
            else None
        )
        bank = None
        if doc and doc.bank_account_id:
            from app.features.bank.models import BankAccount

            bank = db.get(BankAccount, doc.bank_account_id)
        return render(
            request,
            "sales/trade_detail.html",
            {
                "cfg": cfg,
                "entry": entry,
                "doc": doc,
                "kind": kind,
                "acc_lines": acc_lines,
                "vat_groups": groups,
                "vat_labels": VAT_LABELS,
                "history": _history(db, ctx.company_id, entry.id),
                "original": original,
                "bank": bank,
                "attachments": entry.attachments,
                "breadcrumbs": _crumbs(cfg, kind, entry.display_number),
            },
        )

    @router.get(cfg.base + "/{entry_id:int}/edit")
    def trade_edit(
        request: Request,
        entry_id: int,
        user: CurrentUser = Depends(require(f"{cfg.perm}.create")),
        db: Session = Depends(get_tenant_db),
    ):
        ctx = get_ctx(request)
        entry, doc = svc.get_doc(db, ctx, entry_id)
        if doc is None or not entry.is_draft:
            return redirect(
                f"{cfg.base}/{entry_id}",
                "Chỉ sửa được chứng từ nháp",
                "warning",
                request=request,
            )
        form, lines = svc.doc_to_form(doc)
        return render(
            request,
            "sales/trade_form.html",
            form_context(
                request,
                db,
                ctx,
                kind=doc.kind,
                form=form,
                lines=lines,
                errors={},
                entry=entry,
            ),
        )

    @router.post(cfg.base + "/{entry_id:int}")
    def trade_update(
        request: Request,
        entry_id: int,
        form: dict = Depends(form_data),
        user: CurrentUser = Depends(require(f"{cfg.perm}.create")),
        db: Session = Depends(get_tenant_db),
    ):
        ctx = get_ctx(request)
        entry, doc = svc.get_doc(db, ctx, entry_id)
        if doc is None:
            raise BusinessError("Chứng từ không có dữ liệu chi tiết để sửa")
        _doc, resp = _save(
            request, db, dict(form), kind=doc.kind, doc=doc, entry=entry
        )
        return resp
