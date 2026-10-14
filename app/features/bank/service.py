"""Bank account master data.

The bank deposit module builds on it with statements and
reconciliation.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BusinessError, NotFoundError
from app.features.bank.models import BankAccount


def list_bank_accounts(
    db: Session, company_id: int, *, active: bool | None = True
) -> list[BankAccount]:
    stmt = select(BankAccount).where(BankAccount.company_id == company_id)
    if active is not None:
        stmt = stmt.where(BankAccount.is_active.is_(active))
    return list(db.scalars(stmt.order_by(BankAccount.id)))


def get_bank_account(
    db: Session, company_id: int, bank_id: int
) -> BankAccount:
    b = db.get(BankAccount, bank_id)
    if b is None or b.company_id != company_id:
        raise NotFoundError("Không tìm thấy tài khoản ngân hàng")
    return b


def create_bank_account(
    db: Session,
    ctx,
    *,
    number: str,
    bank_name: str,
    branch: str = "",
    short_name: str = "",
    account_code: str = "1121",
    note: str = "",
) -> BankAccount:
    number = "".join((number or "").split())
    if not number:
        raise BusinessError("Nhập số tài khoản")
    if not (bank_name or "").strip():
        raise BusinessError("Nhập tên ngân hàng")
    if account_code not in ("1121", "1122"):
        raise BusinessError(
            "Tài khoản ngân hàng phải hạch toán vào 1121 hoặc 1122"
        )
    if db.scalar(
        select(BankAccount.id).where(
            BankAccount.company_id == ctx.company_id,
            BankAccount.number == number,
        )
    ):
        raise BusinessError(f"Số tài khoản {number} đã có")
    b = BankAccount(
        company_id=ctx.company_id,
        number=number,
        bank_name=bank_name.strip(),
        branch=branch.strip(),
        short_name=short_name.strip(),
        account_code=account_code,
        note=note.strip(),
        is_active=True,
    )
    db.add(b)
    db.flush()
    write_audit(
        db,
        ctx,
        action="bank_account.create",
        entity_type="bank_account",
        entity_id=b.id,
        summary=f"Thêm tài khoản ngân hàng {b.label}",
    )
    db.commit()
    return b


def update_bank_account(
    db: Session,
    ctx,
    b: BankAccount,
    *,
    bank_name: str | None = None,
    branch: str | None = None,
    short_name: str | None = None,
    note: str | None = None,
    is_active: bool | None = None,
) -> BankAccount:
    if b.company_id != ctx.company_id:
        raise NotFoundError()
    for f, v in (
        ("bank_name", bank_name),
        ("branch", branch),
        ("short_name", short_name),
        ("note", note),
    ):
        if v is not None:
            setattr(b, f, v.strip())
    if is_active is not None:
        b.is_active = is_active
    write_audit(
        db,
        ctx,
        action="bank_account.update",
        entity_type="bank_account",
        entity_id=b.id,
        summary=f"Sửa tài khoản ngân hàng {b.label}",
    )
    db.commit()
    return b
