"""Chart of accounts: tree view, add detail accounts, rename, and
deactivate/reactivate accounts.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.db import like_contains, like_prefix, ulower
from app.core.errors import BusinessError, NotFoundError
from app.features.accounts.models import Account
from app.features.journal.models import JournalLine, OpeningBalance


def list_accounts(
    db: Session,
    company_id: int,
    *,
    q: str = "",
    postable: bool | None = None,
    prefix: str | None = None,
    active: bool | None = None,
) -> list[Account]:
    stmt = select(Account).where(Account.company_id == company_id)
    if q:
        term = q.strip().lower()
        stmt = stmt.where(
            or_(
                like_prefix(Account.code, q.strip()),
                like_contains(ulower(Account.name), term),
            )
        )
    if postable is not None:
        stmt = stmt.where(Account.is_postable.is_(postable))
    if prefix:
        stmt = stmt.where(
            or_(*[Account.code.like(f"{p}%") for p in prefix.split(",") if p])
        )
    if active is not None:
        stmt = stmt.where(Account.is_active.is_(active))
    return list(db.scalars(stmt.order_by(Account.code)))


def get_account(db: Session, company_id: int, code: str) -> Account:
    a = db.scalar(
        select(Account).where(
            Account.company_id == company_id, Account.code == code
        )
    )
    if a is None:
        raise NotFoundError(f"Không có tài khoản {code}")
    return a


def account_used(db: Session, company_id: int, code: str) -> bool:
    used = db.scalar(
        select(JournalLine.id)
        .where(
            JournalLine.company_id == company_id,
            or_(
                JournalLine.debit_account == code,
                JournalLine.credit_account == code,
            ),
        )
        .limit(1)
    )
    if used:
        return True
    return (
        db.scalar(
            select(OpeningBalance.id)
            .where(
                OpeningBalance.company_id == company_id,
                OpeningBalance.account == code,
            )
            .limit(1)
        )
        is not None
    )


def create_child(
    db: Session, ctx, parent_code: str, code: str, name: str
) -> Account:
    """Add a detail account under ``parent_code``.

    Splitting is refused when the parent is postable and already has
    data (journal lines or opening balances).
    """
    code, name = (code or "").strip(), (name or "").strip()
    parent = get_account(db, ctx.company_id, parent_code)
    if (
        not code.isdigit()
        or not code.startswith(parent.code)
        or len(code) <= len(parent.code)
    ):
        raise BusinessError(
            f"Mã tài khoản con phải bắt đầu bằng {parent.code} và dài hơn, "
            "chỉ gồm chữ số"
        )
    if len(code) > 20:
        raise BusinessError("Mã tài khoản quá dài")
    if not name:
        raise BusinessError("Nhập tên tài khoản")
    if db.scalar(
        select(Account.id).where(
            Account.company_id == ctx.company_id, Account.code == code
        )
    ):
        raise BusinessError(f"Tài khoản {code} đã có")
    if parent.is_postable and account_used(db, ctx.company_id, parent.code):
        raise BusinessError(
            f"TK {parent.code} đã có số liệu, "
            "không tách thêm tài khoản con được"
        )
    acc = Account(
        company_id=ctx.company_id,
        code=code,
        name=name,
        parent_code=parent.code,
        level=parent.level + 1,
        kind=parent.kind,
        normal=parent.normal,
        is_postable=True,
        partner_mode=parent.partner_mode,
        needs_bank=parent.needs_bank,
        tracks_item=parent.tracks_item,
        tracks_asset=parent.tracks_asset,
        no_negative=parent.no_negative,
        is_system=False,
        is_active=True,
    )
    parent.is_postable = False
    db.add(acc)
    write_audit(
        db,
        ctx,
        action="account.create",
        entity_type="account",
        entity_id=code,
        summary=f"Thêm tài khoản {code} – {name}",
    )
    db.commit()
    return acc


def update_account(
    db: Session,
    ctx,
    code: str,
    *,
    name: str | None = None,
    active: bool | None = None,
    partner_mode: str | None = None,
) -> Account:
    acc = get_account(db, ctx.company_id, code)
    before = {
        "name": acc.name,
        "is_active": acc.is_active,
        "partner_mode": acc.partner_mode,
    }
    if name is not None:
        if not name.strip():
            raise BusinessError("Nhập tên tài khoản")
        acc.name = name.strip()[:255]
    if active is not None:
        acc.is_active = active
    if partner_mode in ("none", "optional", "required"):
        acc.partner_mode = partner_mode
    after = {
        "name": acc.name,
        "is_active": acc.is_active,
        "partner_mode": acc.partner_mode,
    }
    if after != before:
        write_audit(
            db,
            ctx,
            action="account.update",
            entity_type="account",
            entity_id=code,
            summary=f"Sửa tài khoản {code}",
            before=before,
            after=after,
        )
    db.commit()
    return acc


def tree(accounts: list[Account]) -> list[Account]:
    """Sort by code.

    Code order is already tree order because codes are hierarchical
    by prefix.
    """
    return sorted(accounts, key=lambda a: a.code)
