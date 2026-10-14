"""Purchases (MH), purchase returns (TM) and XML e-invoice import."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import BusinessError
from app.core.security import CurrentUser, require
from app.core.web import get_ctx, get_tenant_db, redirect, render
from app.features.purchases import einvoice_import as imp
from app.features.purchases import service
from app.features.sales.trade_web import TradeConfig, register

router = APIRouter()

CONFIG = TradeConfig(
    module="purchases",
    base="/purchases",
    service=service,
    main_kind="purchase",
    titles={"purchase": "Chứng từ mua hàng", "return": "Trả lại hàng mua"},
    list_titles={"purchase": "Mua hàng", "return": "Trả lại hàng mua"},
    subtitles={
        "purchase": (
            "Hàng hóa, dịch vụ mua vào, thuế GTGT đầu vào và công nợ phải trả"
        ),
        "return": "Hàng mua trả lại người bán, điều chỉnh giảm thuế đầu vào",
    },
    partner_label="Nhà cung cấp",
    partner_role="supplier",
    price_field="purchase_price",
    account_params={"postable": 1, "prefix": "15,211,2411,242,6,811"},
    account_label="TK Nợ",
    show_discount=False,
    deductible=True,
    payment_labels={
        "credit": "Ghi có (TK 331)",
        "cash": "Tiền mặt (TK 1111)",
        "bank": "Chuyển khoản (TK 1121)",
    },
    section="Mua hàng",
    icon="truck",
    account_attr="debit_account",
    item_account_expr=(
        "(($event.detail.extra || {}).stock_account)"
        " || (($event.detail.extra || {}).expense_account)"
        " || line.account"
    ),
)
register(router, CONFIG)


# ------------------------------------------------------- XML e-invoices


def _ctx_accounts(request: Request):
    company = getattr(request.state, "company", None)
    return {
        "purchase_accounts": imp.purchase_accounts(company),
        "sales_accounts": imp.SALES_ACCOUNTS,
    }


@router.get("/einvoices")
def einvoices_page(
    request: Request, _u: CurrentUser = Depends(require("purchases.create"))
):
    return render(
        request,
        "purchases/einvoices.html",
        {
            "breadcrumbs": [
                ("Mua hàng", "/purchases"),
                ("Nhập hóa đơn điện tử", None),
            ],
            **_ctx_accounts(request),
        },
    )


@router.post("/einvoices")
async def einvoices_upload(
    request: Request,
    _u: CurrentUser = Depends(require("purchases.create")),
    db: Session = Depends(get_tenant_db),
):
    form = await request.form()
    files = []
    for f in form.getlist("files"):
        if hasattr(f, "read") and getattr(f, "filename", ""):
            files.append((f.filename, await f.read()))
    ctx = get_ctx(request)
    try:
        prev = imp.preview(db, ctx, files)
    except BusinessError as e:
        return redirect("/einvoices", e.message, "error", request=request)
    return render(
        request,
        "purchases/einvoice_preview.html",
        {
            "prev": prev,
            "breadcrumbs": [
                ("Mua hàng", "/purchases"),
                ("Nhập hóa đơn điện tử", "/einvoices"),
                ("Xem trước", None),
            ],
            **_ctx_accounts(request),
        },
    )


@router.post("/einvoices/{token}/confirm")
async def einvoices_confirm(
    request: Request,
    token: str,
    _u: CurrentUser = Depends(require("purchases.create")),
    db: Session = Depends(get_tenant_db),
):
    form = await request.form()
    keys = set(form.getlist("keys"))
    if not keys:
        return redirect(
            "/einvoices", "Chưa chọn hóa đơn nào", "warning", request=request
        )
    res = imp.confirm(
        db,
        get_ctx(request),
        token,
        keys,
        purchase_account=form.get("purchase_account")
        or service.expense_default(getattr(request.state, "company", None)),
        sales_account=form.get("sales_account") or "5113",
    )
    msg = (
        f"Đã tạo {res['in']} chứng từ mua hàng và {res['out']} "
        "chứng từ bán hàng nháp từ hóa đơn điện tử."
    )
    if res["errors"]:
        msg += " Lỗi: " + "; ".join(res["errors"][:3])
    target = (
        "/purchases?status=draft"
        if res["in"] or not res["out"]
        else "/sales?status=draft"
    )
    return redirect(
        target, msg, "warning" if res["errors"] else "success", request=request
    )


@router.post("/einvoices/{token}/discard")
def einvoices_discard(
    request: Request,
    token: str,
    _u: CurrentUser = Depends(require("purchases.create")),
):
    imp.discard(get_ctx(request), token)
    return redirect("/einvoices", "Đã bỏ phiên nhập", "info", request=request)
