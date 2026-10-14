"""AI tools of the cash module (read figures or create DRAFTS only)."""

from __future__ import annotations

from sqlalchemy import select

from app.core.dates import parse_date, today
from app.core.errors import BusinessError
from app.core.registry import ai_tool
from app.features.bank.models import BankAccount
from app.features.cash import vouchers as mv
from app.features.journal import queries
from app.features.org.models import Company
from app.features.partners.models import Partner


def _date(value, default=None):
    try:
        return parse_date(value) if value else (default or today())
    except ValueError as e:
        raise BusinessError(f"Ngày không hợp lệ: {value}") from e


@ai_tool(
    name="get_cash_balance",
    label="Xem tồn quỹ tiền mặt",
    perm="cash.view",
    description=(
        "Cash on hand (account 1111) balance in VND at the end of a date "
        "(default: today)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "ISO date YYYY-MM-DD; default today",
            }
        },
    },
)
def get_cash_balance(db, ctx, *, date: str | None = None) -> dict:
    d = _date(date)
    return {
        "account": "1111",
        "date": d.isoformat(),
        "balance": queries.balance(
            db, ctx.company_id, "1111", upto=d, exact=True
        ),
    }


@ai_tool(
    name="get_bank_balances",
    label="Xem số dư tiền gửi",
    perm="bank.view",
    description=(
        "Balance of each company bank account (accounts 1121/1122) in VND "
        "at the end of a date."
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "ISO date YYYY-MM-DD; default today",
            }
        },
    },
)
def get_bank_balances(db, ctx, *, date: str | None = None) -> dict:
    d = _date(date)
    rows = []
    for b in db.scalars(
        select(BankAccount)
        .where(BankAccount.company_id == ctx.company_id)
        .order_by(BankAccount.id)
    ):
        rows.append(
            {
                "bank_account": b.label,
                "bank_name": b.bank_name,
                "account": b.account_code,
                "balance": queries.balance(
                    db,
                    ctx.company_id,
                    b.account_code,
                    upto=d,
                    exact=True,
                    bank_account_id=b.id,
                ),
                "active": b.is_active,
            }
        )
    return {
        "date": d.isoformat(),
        "accounts": rows,
        "total": queries.balance(db, ctx.company_id, "112", upto=d),
    }


@ai_tool(
    name="create_draft_cash_voucher",
    label="Lập nháp phiếu thu/chi",
    perm="cash.create",
    write=True,
    description=(
        "Create a DRAFT cash receipt (phiếu thu) or payment (phiếu chi). "
        "It is NOT posted: a person must review and post it. "
        "contra_account is the offsetting account "
        "(e.g. 131, 331, 6422, 5113)."
    ),
    parameters={
        "type": "object",
        "required": ["kind", "amount", "contra_account", "description"],
        "properties": {
            "kind": {"type": "string", "enum": ["receipt", "payment"]},
            "date": {
                "type": "string",
                "description": "ISO date YYYY-MM-DD; default today",
            },
            "amount": {
                "type": "integer",
                "description": "Amount in VND (integer)",
            },
            "contra_account": {"type": "string"},
            "contact_name": {
                "type": "string",
                "description": "Payer / receiver name",
            },
            "description": {"type": "string"},
            "partner_code": {
                "type": "string",
                "description": "Optional partner code (KH0001…)",
            },
        },
    },
)
def create_draft_cash_voucher(
    db,
    ctx,
    *,
    kind: str,
    amount: int,
    contra_account: str,
    description: str,
    date: str | None = None,
    contact_name: str = "",
    partner_code: str | None = None,
) -> dict:
    if kind not in ("receipt", "payment"):
        raise BusinessError("kind phải là receipt hoặc payment")
    if not isinstance(amount, int) or amount <= 0:
        raise BusinessError("Số tiền phải là số nguyên dương")
    doc_type = "PT" if kind == "receipt" else "PC"
    partner_id = None
    if partner_code:
        p = db.scalar(
            select(Partner).where(
                Partner.company_id == ctx.company_id,
                Partner.code == partner_code,
            )
        )
        if p is None:
            raise BusinessError(f"Không có đối tượng mã {partner_code}")
        partner_id = p.id
        contact_name = contact_name or p.name
    company = db.get(Company, ctx.company_id)
    form = {
        "date": (_date(date)).isoformat(),
        "description": description,
        "contact_name": contact_name or "(chưa ghi tên)",
        "account": contra_account,
        "amount": str(amount),
        "partner_id": str(partner_id or ""),
    }
    v, errors = mv.parse_form(form, doc_type, company=company)
    if v is None:
        raise BusinessError("; ".join(f"{k}: {m}" for k, m in errors.items()))
    v.source = "ai"
    e = mv.save(db, ctx, v)
    return {
        "draft_id": e.id,
        "url": e.url,
        "status": e.status,
        "summary": (
            f"{mv.KIND_LABELS[doc_type]} nháp {amount:,} đ – {description}"
        ).replace(",", "."),
    }
