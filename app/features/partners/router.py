"""Customers, suppliers, employees and other partners."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.dates import today
from app.core.errors import PermissionDeniedError
from app.core.security import CurrentUser, require
from app.core.web import (
    form_data,
    get_ctx,
    get_tenant_db,
    is_htmx,
    redirect,
    render,
)
from app.features.journal import queries
from app.features.partners import service
from app.features.partners.service import PartnerInput

router = APIRouter()

PAGE = 50
ROLE_LABELS = {
    "customer": "Khách hàng",
    "supplier": "Nhà cung cấp",
    "employee": "Nhân viên",
}
# Receivable/payable accounts shown on the partner detail page.
# An employee's balance on these accounts reveals salary/advances,
# so only users allowed to view payroll may see it.
SALARY_ACCOUNTS = {"334", "141"}
DEBT_ACCOUNTS = [
    ("131", "Phải thu khách hàng"),
    ("331", "Phải trả người bán"),
    ("141", "Tạm ứng"),
    ("334", "Phải trả người lao động"),
    ("1388", "Phải thu khác"),
    ("3388", "Phải trả khác"),
]


def _input(form: dict) -> PartnerInput:
    try:
        terms = int(form.get("payment_terms_days") or 0)
    except ValueError:
        terms = 0
    return PartnerInput(
        name=form.get("name") or "",
        code=form.get("code") or "",
        is_customer=bool(form.get("is_customer")),
        is_supplier=bool(form.get("is_supplier")),
        is_employee=bool(form.get("is_employee")),
        tax_code=form.get("tax_code") or "",
        id_number=form.get("id_number") or "",
        address=form.get("address") or "",
        phone=form.get("phone") or "",
        email=form.get("email") or "",
        contact_person=form.get("contact_person") or "",
        bank_account=form.get("bank_account") or "",
        bank_name=form.get("bank_name") or "",
        payment_terms_days=max(0, min(terms, 3650)),
        note=form.get("note") or "",
        is_active=form.get("is_active", "1") == "1",
    )


@router.get("/partners")
def partners_page(
    request: Request,
    q: str = "",
    role: str = "",
    page: int = 1,
    show: str = "active",
    user: CurrentUser = Depends(require("catalog.view")),
    db: Session = Depends(get_tenant_db),
):
    page = max(1, page)
    rows = service.search(
        db,
        user.company_id,
        q,
        role=role or None,
        active=None if show == "all" else True,
        limit=PAGE + 1,
        offset=(page - 1) * PAGE,
    )
    ctx = {
        "partners": rows[:PAGE],
        "has_next": len(rows) > PAGE,
        "page": page,
        "q": q,
        "role": role,
        "show": show,
        "masked_ids": {p.id for p in rows if service.hide_personal(p, user)},
        "role_labels": ROLE_LABELS,
        "breadcrumbs": [("Danh mục", None), ("Đối tượng", None)],
    }
    if (
        is_htmx(request)
        and request.headers.get("hx-target") == "partner-results"
    ):
        return render(request, "partners/_rows.html", ctx)
    return render(request, "partners/index.html", ctx)


@router.get("/partners/new")
def partner_new(
    request: Request,
    role: str = "customer",
    _u: CurrentUser = Depends(require("catalog.create")),
):
    form = {
        "is_customer": role == "customer",
        "is_supplier": role == "supplier",
        "is_employee": role == "employee",
        "is_active": True,
    }
    return render(
        request,
        "partners/form.html",
        {
            "p": None,
            "form": form,
            "errors": {},
            "breadcrumbs": [("Đối tượng", "/partners"), ("Thêm mới", None)],
        },
    )


@router.post("/partners")
def partner_create(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("catalog.create")),
    db: Session = Depends(get_tenant_db),
):
    p = service.create(db, get_ctx(request), _input(form))
    if form.get("_return") == "json":
        return {
            "value": p.id,
            "label": p.label,
            "extra": {
                "name": p.name,
                "address": p.address,
                "tax_code": p.tax_code,
            },
        }
    return redirect(f"/partners/{p.id}", f"Đã thêm {p.name}", request=request)


@router.get("/partners/{partner_id}")
def partner_detail(
    request: Request,
    partner_id: int,
    user: CurrentUser = Depends(require("catalog.view")),
    db: Session = Depends(get_tenant_db),
):
    p = service.get_partner(db, user.company_id, partner_id)
    masked = service.hide_personal(p, user)
    balances = []
    if user.can("journal.view"):
        for code, label in DEBT_ACCOUNTS:
            if masked and code in SALARY_ACCOUNTS:
                continue
            s = queries.sums(
                db, user.company_id, code, partner_id=p.id, end=today()
            )
            if s.debit or s.credit:
                balances.append(
                    {
                        "account": code,
                        "label": label,
                        "debit": s.debit,
                        "credit": s.credit,
                        "net": s.net,
                    }
                )
    return render(
        request,
        "partners/detail.html",
        {
            "p": p,
            "balances": balances,
            "form": p,
            "errors": {},
            "role_labels": ROLE_LABELS,
            "masked": masked,
            "year_start": date(today().year, 1, 1),
            "breadcrumbs": [("Đối tượng", "/partners"), (p.name, None)],
        },
    )


@router.post("/partners/{partner_id}")
def partner_update(
    request: Request,
    partner_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("catalog.manage")),
    db: Session = Depends(get_tenant_db),
):
    p = service.get_partner(db, user.company_id, partner_id)
    if service.hide_personal(p, user):
        raise PermissionDeniedError(
            "Cần quyền xem lương để sửa hồ sơ nhân viên"
        )
    service.update(db, get_ctx(request), p, _input(form))
    return redirect(f"/partners/{p.id}", "Đã lưu", request=request)
