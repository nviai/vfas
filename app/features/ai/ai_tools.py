"""Shared AI tools on the general ledger.

Other modules register their own tools in their own ``ai_tools.py``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select

from app.core.dates import parse_date, today
from app.core.db import like_contains, ulower
from app.core.errors import BusinessError, NotFoundError
from app.core.money import format_vnd
from app.core.registry import ROLE_LABELS, ai_tool, can_entry, doc_label
from app.features.accounts.models import Account
from app.features.journal import queries
from app.features.journal.models import STATUS_LABELS, JournalEntry
from app.features.org.models import Company
from app.features.partners.models import Partner

DATE_HINT = "Date in YYYY-MM-DD"


def _date(value, default: date | None = None) -> date | None:
    if value in (None, ""):
        return default
    try:
        return parse_date(value)
    except ValueError as e:
        raise BusinessError(f"Ngày không hợp lệ: {value}") from e


def find_partner(db, company_id: int, ref: str | None) -> Partner | None:
    if not ref:
        return None
    ref = str(ref).strip()
    p = db.scalar(
        select(Partner).where(
            Partner.company_id == company_id, Partner.code == ref
        )
    )
    if p is None:
        p = db.scalar(
            select(Partner).where(
                Partner.company_id == company_id, Partner.tax_code == ref
            )
        )
    if p is None:
        p = db.scalar(
            select(Partner)
            .where(
                Partner.company_id == company_id,
                like_contains(ulower(Partner.name), ref.lower()),
            )
            .limit(1)
        )
    if p is None:
        raise NotFoundError(f"Không tìm thấy đối tượng '{ref}'")
    return p


def _entry_dict(e: JournalEntry, *, lines: bool = False) -> dict:
    d = {
        "id": e.id,
        "number": e.number,
        "doc_type": e.doc_type,
        "doc_type_label": doc_label(e.doc_type),
        "date": e.date.isoformat(),
        "status": e.status,
        "status_label": STATUS_LABELS.get(e.status, e.status),
        "description": e.description,
        "contact": e.contact_name or None,
        "total": e.total,
        "url": e.url,
    }
    if lines:
        d["lines"] = [
            {
                "debit_account": ln.debit_account,
                "credit_account": ln.credit_account,
                "amount": ln.amount,
                "description": ln.description or None,
            }
            for ln in e.lines
        ]
        if e.vat_records:
            d["vat_invoices"] = [
                {
                    "direction": v.direction,
                    "invoice_no": v.invoice_no,
                    "invoice_date": v.invoice_date,
                    "partner": v.partner_name,
                    "goods_amount": v.goods_amount,
                    "vat_rate": v.vat_rate,
                    "vat_amount": v.vat_amount,
                }
                for v in e.vat_records
            ]
        if e.cancel_reason:
            d["cancel_reason"] = e.cancel_reason
    return d


@ai_tool(
    name="get_company_info",
    label="Thông tin công ty",
    perm="company.view",
    description="Get the current company's profile (name, tax code, "
    "address, accounting regime, start date, VAT method/period), today's "
    "date and the asking user's role.",
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)
def get_company_info(db, ctx):
    c = db.get(Company, ctx.company_id)
    return {
        "name": c.name,
        "short_name": c.short_name or None,
        "tax_code": c.tax_code or None,
        "address": c.address or None,
        "regime": "TT133/2016/TT-BTC",
        "start_date": c.start_date.isoformat(),
        "vat_method": c.vat_method,
        "vat_period": c.vat_period,
        "legal_rep": c.legal_rep or None,
        "chief_accountant": c.chief_accountant or None,
        "today": today().isoformat(),
        "user_role": ROLE_LABELS.get(
            getattr(ctx.user, "role", "") or "", None
        ),
        "summary": c.name,
    }


@ai_tool(
    name="get_account_balance",
    label="Xem số dư tài khoản",
    perm="journal.view",
    description="Balance of a ledger account (TT133 code, prefix "
    "allowed: '111' covers 1111+1112) up to a date, optionally for one "
    "partner. Returns total debit, total credit and net balance "
    "(positive = debit balance).",
    parameters={
        "type": "object",
        "properties": {
            "account": {
                "type": "string",
                "description": "Account code, e.g. 1111, 112, 131, 331, 511",
            },
            "date": {
                "type": "string",
                "description": DATE_HINT + "; default today",
            },
            "partner_code": {
                "type": "string",
                "description": "Partner code, tax code or name (optional)",
            },
        },
        "required": ["account"],
        "additionalProperties": False,
    },
)
def get_account_balance(
    db,
    ctx,
    account: str,
    date: str | None = None,
    partner_code: str | None = None,
):
    code = str(account).strip()
    acc = db.scalar(
        select(Account).where(
            Account.company_id == ctx.company_id, Account.code == code
        )
    )
    if acc is None:
        raise NotFoundError(f"Không có tài khoản {code}")
    d = _date(date, today())
    partner = find_partner(db, ctx.company_id, partner_code)
    s = queries.sums(
        db,
        ctx.company_id,
        code,
        end=d,
        partner_id=partner.id if partner else None,
    )
    side = "Nợ" if s.net >= 0 else "Có"
    return {
        "account": code,
        "account_name": acc.name,
        "date": d.isoformat(),
        "partner": partner.name if partner else None,
        "total_debit": s.debit,
        "total_credit": s.credit,
        "balance": s.net,
        "balance_side": side,
        "summary": f"TK {code}: dư {side} {format_vnd(abs(s.net))} đ",
    }


@ai_tool(
    name="search_entries",
    label="Tìm chứng từ",
    perm="journal.view",
    description="Search accounting vouchers (all types) by text (number, "
    "description, counterparty), type code (PT, PC, BC, BN, PK, BH, MH, "
    "...), status and date range. Newest first.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "doc_type": {"type": "string", "description": "Voucher type code"},
            "status": {
                "type": "string",
                "enum": ["draft", "posted", "cancelled"],
            },
            "start": {"type": "string", "description": DATE_HINT},
            "end": {"type": "string", "description": DATE_HINT},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        "additionalProperties": False,
    },
)
def search_entries(
    db,
    ctx,
    query: str | None = None,
    doc_type: str | None = None,
    status: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
):
    stmt = select(JournalEntry).where(
        JournalEntry.company_id == ctx.company_id
    )
    if query:
        term = str(query).strip().lower()
        stmt = stmt.where(
            or_(
                like_contains(ulower(JournalEntry.number), term),
                like_contains(ulower(JournalEntry.description), term),
                like_contains(ulower(JournalEntry.contact_name), term),
                like_contains(ulower(JournalEntry.ref_no), term),
            )
        )
    if doc_type:
        stmt = stmt.where(JournalEntry.doc_type == str(doc_type).upper())
    if status:
        stmt = stmt.where(JournalEntry.status == status)
    if start:
        stmt = stmt.where(JournalEntry.date >= _date(start))
    if end:
        stmt = stmt.where(JournalEntry.date <= _date(end))
    limit = max(1, min(int(limit or 20), 50))
    rows = [
        e
        for e in db.scalars(
            stmt.order_by(
                JournalEntry.date.desc(), JournalEntry.id.desc()
            ).limit(limit * 2)
        )
        if can_entry(ctx.user, e.doc_type, "view")
    ][:limit]
    return {"items": [_entry_dict(e) for e in rows], "count": len(rows)}


@ai_tool(
    name="get_entry",
    label="Xem chứng từ",
    perm="journal.view",
    description="Get one voucher with its double-entry lines "
    "(debit/credit account, amount) by number (e.g. PT2026-0001) or "
    "numeric id.",
    parameters={
        "type": "object",
        "properties": {"number_or_id": {"type": "string"}},
        "required": ["number_or_id"],
        "additionalProperties": False,
    },
)
def get_entry(db, ctx, number_or_id):
    ref = str(number_or_id).strip()
    e = db.scalar(
        select(JournalEntry).where(
            JournalEntry.company_id == ctx.company_id,
            JournalEntry.number == ref,
        )
    )
    if e is None and ref.isdigit():
        e = db.get(JournalEntry, int(ref))
        if e is not None and e.company_id != ctx.company_id:
            e = None
    if e is None or not can_entry(ctx.user, e.doc_type, "view"):
        raise NotFoundError(f"Không tìm thấy chứng từ {ref}")
    d = _entry_dict(e, lines=True)
    d["summary"] = e.display_number
    return d


@ai_tool(
    name="list_accounts",
    label="Hệ thống tài khoản",
    perm="catalog.view",
    description="List chart-of-accounts entries (TT133) matching a code "
    "prefix or name text.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Code prefix or part of the name",
            },
            "postable_only": {
                "type": "boolean",
                "description": "Only detail accounts usable in entries",
            },
        },
        "additionalProperties": False,
    },
)
def list_accounts(
    db, ctx, query: str | None = None, postable_only: bool = False
):
    from app.features.accounts.service import list_accounts as _list

    rows = _list(
        db,
        ctx.company_id,
        q=query or "",
        postable=True if postable_only else None,
        active=True,
    )[:80]
    return {
        "items": [
            {
                "code": a.code,
                "name": a.name,
                "postable": a.is_postable,
                "normal_balance": a.normal,
                "requires_partner": a.partner_mode == "required",
            }
            for a in rows
        ]
    }


@ai_tool(
    name="search_partners",
    label="Tìm đối tượng",
    perm="catalog.view",
    description="Search customers, suppliers, employees by code, name, "
    "tax code or phone.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "role": {
                "type": "string",
                "enum": ["customer", "supplier", "employee"],
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
)
def search_partners(db, ctx, query: str, role: str | None = None):
    from app.features.partners.service import hide_personal, search

    rows = search(db, ctx.company_id, query, role=role, limit=20, active=None)
    items = []
    for p in rows:
        hide = hide_personal(p, ctx.user)
        items.append(
            {
                "code": p.code,
                "name": p.name,
                "tax_code": None if hide else (p.tax_code or None),
                "address": None if hide else (p.address or None),
                "roles": p.roles,
                "active": p.is_active,
            }
        )
    return {"items": items}


@ai_tool(
    name="run_data_checks",
    label="Kiểm tra dữ liệu",
    perm="checks.view",
    description="Run the data-quality checks (negative cash, unbalanced "
    "opening, number gaps, missing partners, old drafts, unlocked periods, "
    "module checks) and return the issues found.",
    parameters={
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
)
def run_data_checks(db, ctx):
    from app.features.checks.router import run_checks

    results = run_checks(db, ctx)
    out = []
    total = 0
    for r in results:
        issues = [
            {"severity": i.severity, "message": i.message, "link": i.link}
            for i in r["issues"][:20]
        ]
        total += len(r["issues"])
        out.append(
            {
                "check": r["title"],
                "issues": issues,
                "issue_count": len(r["issues"]),
                "error": r["error"],
            }
        )
    return {
        "checks": out,
        "summary": f"{total} vấn đề" if total else "Không có vấn đề",
    }
