"""Actions shared by every document type.

Post, cancel, reverse, delete draft, edit description, attachments.

Each module's detail page calls these endpoints (form POST).
Permissions come from the module that owns the document type
(registry.can_entry). After the action the user is redirected back to
the module's document detail page (`entry.url`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.dates import parse_date, today
from app.core.errors import (
    BusinessError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.registry import can_entry
from app.core.security import CurrentUser, get_member
from app.core.web import form_data, get_ctx, get_tenant_db, redirect, safe_next
from app.features.journal import service as js

router = APIRouter()


def _entry(db: Session, request: Request, entry_id: int, action: str):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id)
    if not can_entry(ctx.user, e.doc_type, action):
        raise PermissionDeniedError()
    return ctx, e


@router.post("/entries/{entry_id}/post")
def entry_post(
    request: Request,
    entry_id: int,
    _u: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    ctx, e = _entry(db, request, entry_id, "post")
    js.post_entry(db, ctx, e)
    return redirect(e.url, f"Đã ghi sổ {e.number}", request=request)


@router.post("/entries/{entry_id}/cancel")
def entry_cancel(
    request: Request,
    entry_id: int,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    ctx, e = _entry(db, request, entry_id, "cancel")
    js.cancel_entry(db, ctx, e, form.get("reason") or "")
    return redirect(e.url, f"Đã hủy {e.number}", "warning", request=request)


@router.post("/entries/{entry_id}/reverse")
def entry_reverse(
    request: Request,
    entry_id: int,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    ctx, e = _entry(db, request, entry_id, "cancel")
    try:
        on = parse_date(form.get("date")) or today()
    except ValueError as err:
        raise BusinessError("Ngày điều chỉnh không hợp lệ") from err
    adj = js.reverse_entry(db, ctx, e, on, form.get("reason") or "")
    return redirect(
        adj.url,
        f"Đã lập chứng từ điều chỉnh nháp cho {e.number}. "
        "Kiểm tra rồi ghi sổ.",
        request=request,
    )


@router.post("/entries/{entry_id}/delete")
def entry_delete(
    request: Request,
    entry_id: int,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    ctx, e = _entry(db, request, entry_id, "create")
    back = safe_next(form.get("next"), e.url.rsplit("/", 1)[0] or "/")
    js.delete_draft(db, ctx, e)
    return redirect(back, "Đã xóa chứng từ nháp", request=request)


@router.post("/entries/{entry_id}/info")
def entry_info(
    request: Request,
    entry_id: int,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    # Editing the description of a posted document: accountants and
    # above (journal.edit_posted) or users with the module's cancel
    # permission. Cashiers (post only) may not edit.
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id)
    if not (
        ctx.can("journal.edit_posted")
        or can_entry(ctx.user, e.doc_type, "cancel")
    ):
        raise PermissionDeniedError()
    attached = form.get("attached_docs")
    js.update_posted_info(
        db,
        ctx,
        e,
        description=form.get("description"),
        contact_name=form.get("contact_name"),
        contact_address=form.get("contact_address"),
        ref_no=form.get("ref_no"),
        attached_docs=int(attached)
        if attached not in (None, "") and str(attached).isdigit()
        else None,
    )
    return redirect(e.url, "Đã lưu thông tin chứng từ", request=request)


@router.post("/entries/{entry_id}/attachments")
async def attachment_upload(
    request: Request,
    entry_id: int,
    file: UploadFile = File(...),
    _u: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    ctx, e = _entry(db, request, entry_id, "create")
    data = await file.read()
    js.add_attachment(db, ctx, e, file.filename or "file", data)
    return redirect(e.url, "Đã đính kèm file", request=request)


@router.get("/attachments/{attachment_id}")
def attachment_download(
    request: Request,
    attachment_id: int,
    _u: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    a = js.get_attachment(db, ctx, attachment_id)
    if not can_entry(ctx.user, a.entry.doc_type, "view"):
        raise PermissionDeniedError()
    path = js.attachment_path(a)
    if not path.exists():
        raise NotFoundError("File không còn trên máy chủ")
    inline = (
        a.content_type.startswith("image/")
        or a.content_type == "application/pdf"
    )
    return FileResponse(
        path,
        media_type=a.content_type,
        filename=a.filename,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.post("/attachments/{attachment_id}/delete")
def attachment_delete(
    request: Request,
    attachment_id: int,
    _u: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    a = js.get_attachment(db, ctx, attachment_id)
    if not can_entry(ctx.user, a.entry.doc_type, "create"):
        raise PermissionDeniedError()
    url = a.entry.url
    js.delete_attachment(db, ctx, a)
    return redirect(url, "Đã xóa file đính kèm", request=request)
