"""E-invoice pages: provider settings, invoice list, the panel on a
sales document, issuing, status refresh and downloads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core import crypto
from app.core.errors import BusinessError
from app.core.security import CurrentUser, require
from app.core.web import form_data, get_ctx, get_tenant_db, redirect, render
from app.features.einvoice import providers
from app.features.einvoice import service as svc
from app.features.einvoice.providers.base import FAILED, ISSUED, UNKNOWN
from app.features.journal.models import JournalEntry
from app.features.org.models import Company
from app.features.sales.models import RETURN

router = APIRouter()


def _doc_url(entry_id: int | None) -> str:
    return f"/sales/{entry_id}" if entry_id else "/einvoice"


# ------------------------------------------------------------- settings


@router.get("/einvoice/settings")
def settings_page(
    request: Request,
    user: CurrentUser = Depends(require("einvoice.manage")),
    db: Session = Depends(get_tenant_db),
):
    company = db.get(Company, user.company_id)
    cfg = svc.get_config(db, user.company_id)
    secrets_ = svc.config_secrets(cfg)
    return render(
        request,
        "einvoice/settings.html",
        {
            "cfg": cfg,
            "providers": providers.available(),
            "environments": svc.ENVIRONMENTS,
            "values": dict(cfg.settings or {}) if cfg else {},
            "masked": {k: crypto.mask(v) for k, v in secrets_.items()},
            "default_template": svc.default_template(company),
            "breadcrumbs": [
                ("Hóa đơn điện tử", "/einvoice"),
                ("Cài đặt", None),
            ],
        },
    )


@router.post("/einvoice/settings")
def settings_save(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("einvoice.manage")),
    db: Session = Depends(get_tenant_db),
):
    try:
        svc.save_config(db, get_ctx(request), form)
    except BusinessError as e:
        db.rollback()
        return redirect("/einvoice/settings", e.message, "error")
    if form.get("action") == "test":
        try:
            message = svc.test_connection(db, get_ctx(request))
        except BusinessError as e:
            return redirect("/einvoice/settings", e.message, "error")
        return redirect("/einvoice/settings", message)
    return redirect("/einvoice/settings", "Đã lưu cấu hình hóa đơn điện tử")


# ----------------------------------------------------------------- list


@router.get("/einvoice")
def list_page(
    request: Request,
    status: str = "",
    user: CurrentUser = Depends(require("einvoice.view")),
    db: Session = Depends(get_tenant_db),
):
    status = status if status in svc.STATUS_LABELS else ""
    return render(
        request,
        "einvoice/list.html",
        {
            "issues": svc.list_issues(db, user.company_id, status=status),
            "status": status,
            "status_labels": svc.STATUS_LABELS,
            "cfg": svc.get_config(db, user.company_id),
            "provider_names": {p.code: p.name for p in providers.available()},
            "breadcrumbs": [("Hóa đơn điện tử", None)],
        },
    )


# ------------------------------------------ panel on a sales document


@router.get("/einvoice/entries/{entry_id}/panel")
def doc_panel(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("einvoice.view")),
    db: Session = Depends(get_tenant_db),
):
    doc = svc.doc_for_entry(db, user.company_id, entry_id)
    entry = db.get(JournalEntry, entry_id)
    cfg = svc.get_config(db, user.company_id)
    issues = svc.issues_for_doc(db, user.company_id, doc.id)
    active = next(
        (i for i in issues if i.status in (UNKNOWN, "signed", ISSUED)), None
    )
    original_ok = doc.kind != RETURN or svc.original_issue(db, doc)
    can_issue = (
        user.can("einvoice.issue")
        and cfg is not None
        and cfg.is_active
        and entry is not None
        and entry.is_draft
        and active is None
        and not doc.einvoice_key
        and not doc.invoice_no
        and bool(original_ok)
    )
    return render(
        request,
        "einvoice/_panel.html",
        {
            "doc": doc,
            "entry": entry,
            "cfg": cfg,
            "active": active,
            "failed": [i for i in issues if i.status == FAILED][:3],
            "can_issue": can_issue,
            "is_return": doc.kind == RETURN,
            "original_ok": bool(original_ok),
            "status_labels": svc.STATUS_LABELS,
        },
    )


@router.post("/einvoice/entries/{entry_id}/issue")
def issue(
    request: Request,
    entry_id: int,
    _u: CurrentUser = Depends(require("einvoice.issue")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    doc = svc.doc_for_entry(db, ctx.company_id, entry_id)
    try:
        result = svc.issue_for_doc(db, ctx, doc)
    except BusinessError as e:
        db.rollback()
        return redirect(_doc_url(entry_id), e.message, "error")
    if result.status == FAILED:
        return redirect(
            _doc_url(entry_id),
            f"Nhà cung cấp từ chối hóa đơn: {result.error}",
            "error",
        )
    if result.status == UNKNOWN:
        return redirect(
            _doc_url(entry_id),
            "Chưa nhận được kết quả từ nhà cung cấp. Hệ thống sẽ tra cứu "
            "lại – KHÔNG phát hành lần nữa cho đến khi có kết quả.",
            "warning",
        )
    message = f"Đã phát hành hóa đơn {result.display_number}"
    if result.error:
        return redirect(
            _doc_url(entry_id), f"{message}. {result.error}", "warning"
        )
    return redirect(_doc_url(entry_id), message)


@router.post("/einvoice/{issue_id}/refresh")
def refresh(
    request: Request,
    issue_id: int,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("einvoice.issue")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    item = svc.get_issue(db, ctx.company_id, issue_id)
    svc.refresh(db, ctx, item)
    back = (
        "/einvoice" if form.get("next") == "list" else _doc_url(item.entry_id)
    )
    message = f"Trạng thái: {svc.STATUS_LABELS.get(item.status, item.status)}"
    if item.error:
        message += f" – {item.error}"
    return redirect(
        back, message, "error" if item.status == FAILED else "success"
    )


@router.get("/einvoice/{issue_id}/file.{kind}")
def download(
    request: Request,
    issue_id: int,
    kind: str,
    user: CurrentUser = Depends(require("einvoice.export")),
    db: Session = Depends(get_tenant_db),
):
    item = svc.get_issue(db, user.company_id, issue_id)
    data = svc.download(db, get_ctx(request), item, kind)
    media = "application/pdf" if kind == "pdf" else "application/xml"
    name = f"hoa-don-{item.series}-{item.number or item.id}.{kind}"
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
