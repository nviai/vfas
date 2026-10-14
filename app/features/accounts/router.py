"""Chart of accounts pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.security import CurrentUser, require
from app.core.web import (
    form_data,
    get_ctx,
    get_tenant_db,
    is_htmx,
    redirect,
    render,
)
from app.features.accounts import service

router = APIRouter()

KIND_LABELS = {
    "asset": "Tài sản",
    "liability": "Nợ phải trả",
    "equity": "Vốn chủ sở hữu",
    "revenue": "Doanh thu",
    "expense": "Chi phí",
    "result": "Xác định kết quả",
}
NORMAL_LABELS = {
    "debit": "Dư Nợ",
    "credit": "Dư Có",
    "both": "Lưỡng tính",
    "none": "Không có số dư",
}


@router.get("/accounts")
def accounts_page(
    request: Request,
    q: str = "",
    show: str = "active",
    user: CurrentUser = Depends(require("catalog.view")),
    db: Session = Depends(get_tenant_db),
):
    rows = service.list_accounts(
        db, user.company_id, q=q, active=None if show == "all" else True
    )
    ctx = {
        "accounts": service.tree(rows),
        "q": q,
        "show": show,
        "kind_labels": KIND_LABELS,
        "normal_labels": NORMAL_LABELS,
        "breadcrumbs": [("Danh mục", None), ("Hệ thống tài khoản", None)],
    }
    if (
        is_htmx(request)
        and request.headers.get("hx-target") == "account-results"
    ):
        return render(request, "accounts/_rows.html", ctx)
    return render(request, "accounts/index.html", ctx)


@router.post("/accounts")
def account_create(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("catalog.manage")),
    db: Session = Depends(get_tenant_db),
):
    acc = service.create_child(
        db,
        get_ctx(request),
        form.get("parent_code") or "",
        form.get("code") or "",
        form.get("name") or "",
    )
    return redirect(
        f"/accounts?q={acc.code}",
        f"Đã thêm tài khoản {acc.code}",
        request=request,
    )


@router.post("/accounts/{code}")
def account_update(
    request: Request,
    code: str,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("catalog.manage")),
    db: Session = Depends(get_tenant_db),
):
    active = None
    if "active" in form:
        active = form.get("active") == "1"
    service.update_account(
        db,
        get_ctx(request),
        code,
        name=form.get("name"),
        active=active,
        partner_mode=form.get("partner_mode"),
    )
    return redirect(
        request.headers.get("hx-current-url")
        and f"/accounts?q={code}"
        or "/accounts",
        f"Đã cập nhật tài khoản {code}",
        request=request,
    )
