"""Companies, memberships and invitations."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BusinessError, NotFoundError, PermissionDeniedError
from app.core.regimes import get_regime
from app.core.registry import OWNER, ROLE_LABELS, ROLES
from app.core.security import hash_token
from app.features.accounts.models import Account
from app.features.auth.models import User
from app.features.org.models import Company, Invitation, Membership

INVITE_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def normalize_tax_code(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit() or ch == "-")


def validate_tax_code(value: str) -> str | None:
    """Validate a Vietnamese tax code (MST).

    Valid forms are 10 digits, or 10 digits + '-' + 3 digits. Returns
    an error message, or None when valid.
    """
    v = normalize_tax_code(value)
    if not v:
        return None
    digits = v.replace("-", "")
    if not (
        len(digits) in (10, 13)
        and digits.isdigit()
        and (len(digits) == 10 or v[10:11] == "-")
    ):
        return "Mã số thuế gồm 10 số hoặc 13 số dạng 0123456789-001"
    return None


def seed_chart(db: Session, company: Company) -> None:
    """Seed the chart of accounts of the company's accounting regime."""
    existing = set(
        db.scalars(
            select(Account.code).where(Account.company_id == company.id)
        )
    )
    for a in get_regime(company.regime).chart():
        if a.code in existing:
            continue
        db.add(
            Account(
                company_id=company.id,
                code=a.code,
                name=a.name,
                parent_code=a.parent_code,
                level=a.level,
                kind=a.kind,
                normal=a.normal,
                is_postable=a.is_postable,
                partner_mode=a.partner_mode,
                needs_bank=a.needs_bank,
                tracks_item=a.tracks_item,
                tracks_asset=a.tracks_asset,
                no_negative=a.no_negative,
                is_system=True,
                is_active=True,
            )
        )


@dataclass
class CompanyInput:
    name: str
    tax_code: str = ""
    address: str = ""
    start_date: date | None = None
    short_name: str = ""
    legal_rep: str = ""
    vat_period: str = "quarter"
    business_type: str = "service"


def create_company(
    db: Session, owner: User | None, inp: CompanyInput, *, ctx_user=None
) -> Company:
    """Create a company and its chart of accounts.

    The creator becomes the owner.
    """
    name = (inp.name or "").strip()
    if not name:
        raise BusinessError("Nhập tên công ty")
    err = validate_tax_code(inp.tax_code)
    if err:
        raise BusinessError(err)
    start = inp.start_date or date(date.today().year, 1, 1)
    c = Company(
        name=name,
        short_name=(inp.short_name or "").strip(),
        tax_code=normalize_tax_code(inp.tax_code),
        address=(inp.address or "").strip(),
        start_date=start,
        legal_rep=(inp.legal_rep or "").strip(),
        vat_period=inp.vat_period
        if inp.vat_period in ("month", "quarter")
        else "quarter",
        business_type=inp.business_type or "service",
        settings={},
        created_at=_now(),
        created_by=getattr(owner, "id", None),
        lock_version=0,
    )
    db.add(c)
    db.flush()
    seed_chart(db, c)
    if owner is not None:
        db.add(
            Membership(
                user_id=owner.id,
                company_id=c.id,
                role=OWNER,
                is_active=True,
                created_at=_now(),
            )
        )
        owner.last_company_id = c.id
    write_audit(
        db,
        None,
        action="company.create",
        entity_type="company",
        entity_id=c.id,
        company_id=c.id,
        user=ctx_user or owner,
        summary=f"Tạo công ty {c.name}",
    )
    db.commit()
    return c


def user_companies(
    db: Session, user_id: int
) -> list[tuple[Company, Membership]]:
    rows = db.execute(
        select(Company, Membership)
        .join(Membership, Membership.company_id == Company.id)
        .where(
            Membership.user_id == user_id,
            Membership.is_active.is_(True),
            Company.is_active.is_(True),
        )
        .order_by(Company.name)
    ).all()
    return [(c, m) for c, m in rows]


def default_company_id(db: Session, user: User) -> int | None:
    companies = user_companies(db, user.id)
    ids = [c.id for c, _ in companies]
    if user.last_company_id in ids:
        return user.last_company_id
    return ids[0] if len(ids) == 1 else None


# -------------------------------------------------------------- members


def members(db: Session, company_id: int) -> list[tuple[Membership, User]]:
    return list(
        db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.company_id == company_id)
            .order_by(User.full_name, User.email)
        ).all()
    )


def _owners(db: Session, company_id: int) -> int:
    return (
        db.scalar(
            select(func.count(Membership.id)).where(
                Membership.company_id == company_id,
                Membership.role == OWNER,
                Membership.is_active.is_(True),
            )
        )
        or 0
    )


def change_role(db: Session, ctx, membership_id: int, role: str) -> Membership:
    if role not in ROLES:
        raise BusinessError("Vai trò không hợp lệ")
    m = db.get(Membership, membership_id)
    if m is None or m.company_id != ctx.company_id:
        raise NotFoundError()
    if (m.role == OWNER or role == OWNER) and ctx.user.role != OWNER:
        raise PermissionDeniedError(
            "Chỉ chủ doanh nghiệp được giao hoặc thu hồi vai trò "
            "chủ doanh nghiệp"
        )
    if m.role == OWNER and role != OWNER and _owners(db, ctx.company_id) <= 1:
        raise BusinessError("Công ty phải còn ít nhất một chủ doanh nghiệp")
    before = m.role
    m.role = role
    write_audit(
        db,
        ctx,
        action="member.role",
        entity_type="membership",
        entity_id=m.id,
        summary=(
            f"Đổi vai trò thành viên #{m.user_id}: "
            f"{ROLE_LABELS[before]} → {ROLE_LABELS[role]}"
        ),
        before={"role": before},
        after={"role": role},
    )
    db.commit()
    return m


def set_member_active(
    db: Session, ctx, membership_id: int, active: bool
) -> Membership:
    m = db.get(Membership, membership_id)
    if m is None or m.company_id != ctx.company_id:
        raise NotFoundError()
    if m.user_id == ctx.user_id and not active:
        raise BusinessError("Không thể tự khóa chính mình")
    if m.role == OWNER and not active:
        if ctx.user.role != OWNER:
            raise PermissionDeniedError()
        if _owners(db, ctx.company_id) <= 1:
            raise BusinessError(
                "Công ty phải còn ít nhất một chủ doanh nghiệp"
            )
    m.is_active = active
    write_audit(
        db,
        ctx,
        action="member.active" if active else "member.disable",
        entity_type="membership",
        entity_id=m.id,
        summary=("Mở lại" if active else "Khóa")
        + f" quyền truy cập của thành viên #{m.user_id}",
    )
    db.commit()
    return m


def invite(db: Session, ctx, email: str, role: str) -> tuple[Invitation, str]:
    """Create an invitation.

    Returns (invitation, raw token used to build the invite link).
    """
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 200:
        raise BusinessError("Email không hợp lệ")
    if role not in ROLES:
        raise BusinessError("Vai trò không hợp lệ")
    if role == OWNER and ctx.user.role != OWNER:
        raise PermissionDeniedError(
            "Chỉ chủ doanh nghiệp được mời thêm chủ doanh nghiệp"
        )
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        existing = db.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.company_id == ctx.company_id,
            )
        )
        if existing is not None:
            raise BusinessError("Người này đã là thành viên của công ty")
    token = secrets.token_urlsafe(32)
    inv = Invitation(
        company_id=ctx.company_id,
        email=email,
        role=role,
        token_hash=hash_token(token),
        invited_by=ctx.user_id,
        created_at=_now(),
        expires_at=_now() + timedelta(days=INVITE_DAYS),
    )
    db.add(inv)
    db.flush()
    write_audit(
        db,
        ctx,
        action="member.invite",
        entity_type="invitation",
        entity_id=inv.id,
        summary=f"Mời {email} làm {ROLE_LABELS[role]}",
    )
    db.commit()
    return inv, token


def pending_invitations(db: Session, company_id: int) -> list[Invitation]:
    return list(
        db.scalars(
            select(Invitation)
            .where(
                Invitation.company_id == company_id,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .order_by(Invitation.created_at.desc())
        )
    )


def revoke_invitation(db: Session, ctx, invitation_id: int) -> None:
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.company_id != ctx.company_id:
        raise NotFoundError()
    inv.revoked_at = _now()
    write_audit(
        db,
        ctx,
        action="member.invite_revoke",
        entity_type="invitation",
        entity_id=inv.id,
        summary=f"Thu hồi lời mời {inv.email}",
    )
    db.commit()


def find_invitation(db: Session, token: str) -> Invitation:
    inv = db.scalar(
        select(Invitation).where(
            Invitation.token_hash == hash_token(token or "")
        )
    )
    if (
        inv is None
        or inv.revoked_at is not None
        or inv.accepted_at is not None
        or _aware(inv.expires_at) < _now()
    ):
        raise NotFoundError("Lời mời không tồn tại hoặc đã hết hạn")
    return inv


def accept_invitation(db: Session, inv: Invitation, user: User) -> Membership:
    if user.email != inv.email:
        raise PermissionDeniedError("Lời mời này dành cho email khác")
    m = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.company_id == inv.company_id,
        )
    )
    if m is None:
        m = Membership(
            user_id=user.id,
            company_id=inv.company_id,
            role=inv.role,
            is_active=True,
            created_at=_now(),
        )
        db.add(m)
    else:
        m.role, m.is_active = inv.role, True
    inv.accepted_at = _now()
    user.last_company_id = inv.company_id
    write_audit(
        db,
        None,
        action="member.join",
        entity_type="membership",
        entity_id=user.id,
        company_id=inv.company_id,
        user=user,
        summary=f"{user.email} nhận lời mời ({ROLE_LABELS[inv.role]})",
    )
    db.commit()
    return m


def add_member_direct(
    db: Session, company_id: int, user: User, role: str
) -> Membership:
    """Add a member directly (CLI, tests)."""
    m = Membership(
        user_id=user.id,
        company_id=company_id,
        role=role,
        is_active=True,
        created_at=_now(),
    )
    db.add(m)
    db.commit()
    return m


# ------------------------------------------------------------- settings

COMPANY_FIELDS = (
    "name",
    "short_name",
    "tax_code",
    "address",
    "phone",
    "email",
    "legal_rep",
    "legal_rep_title",
    "chief_accountant",
    "preparer",
    "cashier",
    "storekeeper",
    "vat_method",
    "vat_period",
    "tax_office",
    "province",
    "wage_region",
    "business_type",
    "number_format",
)


def update_company(db: Session, ctx, data: dict) -> Company:
    c = db.get(Company, ctx.company_id)
    before = {f: getattr(c, f) for f in COMPANY_FIELDS}
    for f in COMPANY_FIELDS:
        if f in data and data[f] is not None:
            setattr(c, f, str(data[f]).strip())
    if not c.name:
        raise BusinessError("Nhập tên công ty")
    err = validate_tax_code(c.tax_code)
    if err:
        raise BusinessError(err)
    c.tax_code = normalize_tax_code(c.tax_code)
    if c.vat_method not in ("khau_tru", "truc_tiep"):
        raise BusinessError("Phương pháp tính thuế GTGT không hợp lệ")
    if c.vat_period not in ("month", "quarter"):
        raise BusinessError("Kỳ khai thuế GTGT không hợp lệ")
    if c.wage_region not in ("I", "II", "III", "IV"):
        raise BusinessError("Vùng lương tối thiểu không hợp lệ")
    from app.features.journal.service import validate_number_format

    err = validate_number_format(c.number_format)
    if err:
        raise BusinessError(err)
    after = {f: getattr(c, f) for f in COMPANY_FIELDS}
    changed = {k for k in after if after[k] != before[k]}
    if changed:
        write_audit(
            db,
            ctx,
            action="company.update",
            entity_type="company",
            entity_id=c.id,
            summary="Sửa thông tin công ty",
            before={k: before[k] for k in changed},
            after={k: after[k] for k in changed},
        )
    db.commit()
    return c


def update_company_settings(db: Session, ctx, key: str, value) -> Company:
    if key == "prefixes":
        import re as _re

        if not isinstance(value, dict) or any(
            not _re.fullmatch(r"[A-Z0-9]{1,8}", str(v or "").strip().upper())
            for v in value.values()
        ):
            raise BusinessError(
                "Tiền tố số chứng từ chỉ gồm chữ in hoa và số, tối đa 8 ký tự"
            )
        value = {str(k)[:8]: str(v).strip().upper() for k, v in value.items()}
    c = db.get(Company, ctx.company_id)
    settings = dict(c.settings or {})
    before = settings.get(key)
    settings[key] = value
    c.settings = settings
    write_audit(
        db,
        ctx,
        action="company.settings",
        entity_type="company",
        entity_id=c.id,
        summary=f"Sửa thiết lập {key}",
        before={key: before},
        after={key: value},
    )
    db.commit()
    return c


def set_start_date(db: Session, ctx, start: date) -> Company:
    """Change the bookkeeping start date.

    Only allowed while there are no posted entries and no locked
    periods.
    """
    from app.features.journal.models import JournalEntry, PeriodLock

    c = db.get(Company, ctx.company_id)
    has_entries = (
        db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == c.id, JournalEntry.status != "draft"
            )
        )
        or 0
    )
    has_locks = (
        db.scalar(
            select(func.count(PeriodLock.id)).where(
                PeriodLock.company_id == c.id, PeriodLock.is_locked.is_(True)
            )
        )
        or 0
    )
    if has_entries or has_locks:
        raise BusinessError(
            "Đã có chứng từ ghi sổ hoặc kỳ đã khóa, "
            "không đổi được ngày bắt đầu hạch toán"
        )
    before = c.start_date
    c.start_date = start
    write_audit(
        db,
        ctx,
        action="company.start_date",
        entity_type="company",
        entity_id=c.id,
        summary="Đổi ngày bắt đầu hạch toán",
        before={"start_date": before},
        after={"start_date": start},
    )
    db.commit()
    return c
