"""Opening balances (at the date accounting starts in the software).

Editable only while no period is locked. Total debits must equal
total credits (a warning is shown otherwise). Editing a cash account's
opening balance must keep its balance non-negative at every point in
time (checked retroactively, as when posting).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BusinessError, NegativeBalanceError
from app.core.money import format_vnd
from app.features.accounts.models import Account
from app.features.bank.models import BankAccount
from app.features.journal import locks, queries
from app.features.journal.models import OpeningBalance
from app.features.journal.service import EntryValidationError, company_tx
from app.features.partners.models import Partner


@dataclass
class OpeningIn:
    account: str
    debit: int = 0
    credit: int = 0
    partner_id: int | None = None
    bank_account_id: int | None = None
    item_id: int | None = None
    note: str = ""


def list_opening(db: Session, company_id: int) -> list[OpeningBalance]:
    return list(
        db.scalars(
            select(OpeningBalance)
            .where(OpeningBalance.company_id == company_id)
            .order_by(OpeningBalance.account, OpeningBalance.id)
        )
    )


def totals(db: Session, company_id: int) -> tuple[int, int]:
    d, c = db.execute(
        select(
            func.coalesce(func.sum(OpeningBalance.debit), 0),
            func.coalesce(func.sum(OpeningBalance.credit), 0),
        ).where(OpeningBalance.company_id == company_id)
    ).one()
    return int(d), int(c)


def _validate(db: Session, company_id: int, rows: list[OpeningIn]) -> None:
    accounts = {
        a.code: a
        for a in db.scalars(
            select(Account).where(Account.company_id == company_id)
        )
    }
    partners = set(
        db.scalars(select(Partner.id).where(Partner.company_id == company_id))
    )
    banks = {
        b.id: b
        for b in db.scalars(
            select(BankAccount).where(BankAccount.company_id == company_id)
        )
    }
    errors = []
    for n, r in enumerate(rows, start=1):
        acc = accounts.get(r.account)
        if acc is None:
            errors.append(f"Dòng {n}: không có TK {r.account}")
            continue
        if not acc.is_postable:
            errors.append(f"Dòng {n}: TK {r.account} là tài khoản tổng hợp")
        if r.debit < 0 or r.credit < 0:
            errors.append(f"Dòng {n}: số dư không được âm")
        if r.debit and r.credit:
            errors.append(f"Dòng {n}: chỉ nhập dư Nợ hoặc dư Có")
        if acc.partner_mode == "required" and not r.partner_id:
            errors.append(f"Dòng {n}: TK {r.account} cần chọn đối tượng")
        if r.partner_id and r.partner_id not in partners:
            errors.append(f"Dòng {n}: đối tượng không hợp lệ")
        if acc.needs_bank:
            if not r.bank_account_id:
                errors.append(
                    f"Dòng {n}: TK {r.account} cần chọn tài khoản ngân hàng"
                )
            elif (
                r.bank_account_id not in banks
                or banks[r.bank_account_id].account_code != r.account
            ):
                errors.append(f"Dòng {n}: tài khoản ngân hàng không hợp lệ")
        if acc.no_negative and r.credit:
            errors.append(f"Dòng {n}: TK {r.account} không được có số dư Có")
    if errors:
        raise EntryValidationError(errors)


def replace_opening(
    db: Session,
    ctx,
    rows: list[OpeningIn],
    *,
    accounts: list[str] | None = None,
) -> None:
    """Replace all opening balances (or only those in `accounts`)."""
    rows = [r for r in rows if r.debit or r.credit]
    _validate(db, ctx.company_id, rows)
    with company_tx(db, ctx.company_id):
        if locks.any_locked(db, ctx.company_id):
            raise BusinessError(
                "Đã khóa sổ, không sửa được số dư đầu kỳ. "
                "Mở khóa toàn bộ trước."
            )
        before = totals(db, ctx.company_id)
        q = delete(OpeningBalance).where(
            OpeningBalance.company_id == ctx.company_id
        )
        if accounts:
            q = q.where(OpeningBalance.account.in_(accounts))
        db.execute(q)
        for r in rows:
            if accounts and r.account not in accounts:
                continue
            db.add(
                OpeningBalance(
                    company_id=ctx.company_id,
                    account=r.account,
                    debit=int(r.debit),
                    credit=int(r.credit),
                    partner_id=r.partner_id,
                    bank_account_id=r.bank_account_id,
                    item_id=r.item_id,
                    note=(r.note or "")[:300],
                )
            )
        db.flush()
        # Lowering a cash account's opening balance may make later
        # lines negative.
        no_neg = set(
            db.scalars(
                select(Account.code).where(
                    Account.company_id == ctx.company_id,
                    Account.no_negative.is_(True),
                )
            )
        )
        for code in sorted(no_neg):
            bank_ids = (
                [None]
                if not code.startswith("112")
                else list(
                    db.scalars(
                        select(BankAccount.id).where(
                            BankAccount.company_id == ctx.company_id,
                            BankAccount.account_code == code,
                        )
                    )
                )
            )
            for bid in bank_ids:
                p = queries.first_negative(
                    db, ctx.company_id, code, bank_account_id=bid
                )
                if p is not None:
                    raise NegativeBalanceError(
                        "Không thể sửa số dư đầu kỳ: "
                        f"số dư TK {code} sẽ bị âm "
                        f"tại ngày {p.date:%d/%m/%Y}, "
                        f"dòng {p.line_no}"
                        + (f" (chứng từ {p.number})" if p.number else "")
                        + f", còn {format_vnd(p.balance)} đồng.",
                        date=p.date,
                        line_no=p.line_no,
                        balance=p.balance,
                        voucher_number=p.number,
                    )
        after = totals(db, ctx.company_id)
        write_audit(
            db,
            ctx,
            action="opening.update",
            entity_type="opening",
            entity_id=",".join(accounts or []) or None,
            summary="Cập nhật số dư đầu kỳ",
            before={"debit": before[0], "credit": before[1]},
            after={"debit": after[0], "credit": after[1]},
        )
