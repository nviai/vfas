"""Partners: customers, suppliers, employees."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import snapshot, write_audit
from app.core.db import like_contains, like_prefix, ulower
from app.core.errors import BusinessError, NotFoundError
from app.features.org.service import normalize_tax_code, validate_tax_code
from app.features.partners.models import Partner

FIELDS = (
    "code",
    "name",
    "is_customer",
    "is_supplier",
    "is_employee",
    "tax_code",
    "id_number",
    "address",
    "phone",
    "email",
    "contact_person",
    "bank_account",
    "bank_name",
    "payment_terms_days",
    "note",
    "is_active",
)
ROLE_PREFIX = {
    "customer": "KH",
    "supplier": "NCC",
    "employee": "NV",
    "other": "DT",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PartnerInput:
    name: str
    code: str = ""
    is_customer: bool = False
    is_supplier: bool = False
    is_employee: bool = False
    tax_code: str = ""
    id_number: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    contact_person: str = ""
    bank_account: str = ""
    bank_name: str = ""
    payment_terms_days: int = 0
    note: str = ""
    is_active: bool = True


def get_partner(db: Session, company_id: int, partner_id: int) -> Partner:
    p = db.get(Partner, partner_id)
    if p is None or p.company_id != company_id:
        raise NotFoundError("Không tìm thấy đối tượng")
    return p


def search(
    db: Session,
    company_id: int,
    q: str = "",
    *,
    role: str | None = None,
    active: bool | None = True,
    limit: int = 20,
    offset: int = 0,
) -> list[Partner]:
    stmt = select(Partner).where(Partner.company_id == company_id)
    if q:
        term = q.strip()
        stmt = stmt.where(
            or_(
                like_contains(ulower(Partner.name), term.lower()),
                like_contains(ulower(Partner.code), term.lower()),
                like_prefix(Partner.tax_code, term),
                like_contains(Partner.phone, term),
            )
        )
    if role == "customer":
        stmt = stmt.where(Partner.is_customer.is_(True))
    elif role == "supplier":
        stmt = stmt.where(Partner.is_supplier.is_(True))
    elif role == "employee":
        stmt = stmt.where(Partner.is_employee.is_(True))
    if active is not None:
        stmt = stmt.where(Partner.is_active.is_(active))
    stmt = (
        stmt.order_by(Partner.use_count.desc(), Partner.name)
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt))


def count(db: Session, company_id: int) -> int:
    return (
        db.scalar(
            select(func.count(Partner.id)).where(
                Partner.company_id == company_id
            )
        )
        or 0
    )


def next_code(db: Session, company_id: int, role: str = "other") -> str:
    prefix = ROLE_PREFIX.get(role, "DT")
    codes = db.scalars(
        select(Partner.code).where(
            Partner.company_id == company_id, Partner.code.like(f"{prefix}%")
        )
    )
    n = 0
    for c in codes:
        tail = c[len(prefix) :]
        if tail.isdigit():
            n = max(n, int(tail))
    return f"{prefix}{n + 1:04d}"


def _validate(
    db: Session, company_id: int, inp: PartnerInput, existing: Partner | None
) -> None:
    if not inp.name.strip():
        raise BusinessError("Nhập tên đối tượng")
    if not 0 <= int(inp.payment_terms_days or 0) <= 3650:
        raise BusinessError("Hạn thanh toán từ 0 đến 3650 ngày")
    err = validate_tax_code(inp.tax_code)
    if err:
        raise BusinessError(err)
    code = inp.code.strip()
    if code:
        dup = db.scalar(
            select(Partner.id).where(
                Partner.company_id == company_id, Partner.code == code
            )
        )
        if dup and (existing is None or dup != existing.id):
            raise BusinessError(f"Mã đối tượng {code} đã có")


def _role_of(inp: PartnerInput) -> str:
    return (
        "customer"
        if inp.is_customer
        else "supplier"
        if inp.is_supplier
        else "employee"
        if inp.is_employee
        else "other"
    )


def create(
    db: Session, ctx, inp: PartnerInput, *, commit: bool = True
) -> Partner:
    _validate(db, ctx.company_id, inp, None)
    p = Partner(company_id=ctx.company_id, created_at=_now())
    for f in FIELDS:
        setattr(p, f, getattr(inp, f))
    p.name = inp.name.strip()[:255]
    p.code = inp.code.strip() or next_code(db, ctx.company_id, _role_of(inp))
    p.tax_code = normalize_tax_code(inp.tax_code)
    db.add(p)
    db.flush()
    write_audit(
        db,
        ctx,
        action="partner.create",
        entity_type="partner",
        entity_id=p.id,
        summary=f"Thêm đối tượng {p.code} – {p.name}",
    )
    if commit:
        db.commit()
    return p


def update(db: Session, ctx, p: Partner, inp: PartnerInput) -> Partner:
    if p.company_id != ctx.company_id:
        raise NotFoundError()
    _validate(db, ctx.company_id, inp, p)
    before = snapshot(p, FIELDS)
    for f in FIELDS:
        setattr(p, f, getattr(inp, f))
    p.name = inp.name.strip()[:255]
    p.code = inp.code.strip() or p.code
    p.tax_code = normalize_tax_code(inp.tax_code)
    after = snapshot(p, FIELDS)
    changed = {k for k in after if after[k] != before[k]}
    if changed:
        write_audit(
            db,
            ctx,
            action="partner.update",
            entity_type="partner",
            entity_id=p.id,
            summary=f"Sửa đối tượng {p.code}",
            before={k: before[k] for k in changed},
            after={k: after[k] for k in changed},
        )
    db.commit()
    return p


def find_or_create(
    db: Session,
    ctx,
    name: str,
    *,
    address: str = "",
    tax_code: str = "",
    role: str = "other",
    commit: bool = False,
) -> Partner | None:
    """Find a partner by tax code, then by name; create it if missing.

    The name match is case-insensitive.
    """
    name = (name or "").strip()
    if not name:
        return None
    p = None
    tax = normalize_tax_code(tax_code)
    if tax:
        p = db.scalar(
            select(Partner).where(
                Partner.company_id == ctx.company_id, Partner.tax_code == tax
            )
        )
    if p is None:
        p = db.scalar(
            select(Partner).where(
                Partner.company_id == ctx.company_id,
                ulower(Partner.name) == name.lower(),
            )
        )
    if p is None:
        p = create(
            db,
            ctx,
            PartnerInput(
                name=name,
                address=address,
                tax_code=tax if not validate_tax_code(tax) else "",
                is_customer=role == "customer",
                is_supplier=role == "supplier",
                is_employee=role == "employee",
            ),
            commit=commit,
        )
    else:
        if role == "customer" and not p.is_customer:
            p.is_customer = True
        if role == "supplier" and not p.is_supplier:
            p.is_supplier = True
        if address and not p.address:
            p.address = address
    touch(p)
    return p


PERSONAL_FIELDS = (
    "id_number",
    "bank_account",
    "bank_name",
    "address",
    "tax_code",
    "phone",
    "email",
)


def hide_personal(p: Partner, user) -> bool:
    """Return True if the partner's personal data must be hidden.

    An employee's personal data (citizen ID, bank account, address…)
    is only visible to users allowed to view payroll.
    """
    return bool(p.is_employee) and not (
        user is None or user.can("payroll.view")
    )


def touch(p: Partner | None) -> None:
    if p is not None:
        p.use_count = (p.use_count or 0) + 1
        p.last_used_at = _now()
