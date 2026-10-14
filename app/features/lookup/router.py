"""Combobox lookups (/lookup/*) and Ctrl+K global search (/search).

All endpoints return JSON.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import ulower
from app.core.i18n import get_translator
from app.core.money import format_vnd
from app.core.registry import can_entry, load_features, registry
from app.core.security import CurrentUser, get_member
from app.core.web import build_nav, get_tenant_db, resolve_lang
from app.features.accounts.service import list_accounts
from app.features.bank.service import list_bank_accounts
from app.features.items import service as items
from app.features.journal.models import JournalEntry
from app.features.partners import service as partners

router = APIRouter()


def _lim(limit: int) -> int:
    return max(1, min(limit, 50))


@router.get("/lookup/accounts")
def lookup_accounts(
    q: str = "",
    postable: int = 1,
    prefix: str | None = None,
    limit: int = 20,
    user: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    rows = list_accounts(
        db,
        user.company_id,
        q=q,
        postable=bool(postable) if postable else None,
        prefix=prefix,
        active=True,
    )
    return [
        {
            "value": a.code,
            "label": f"{a.code} – {a.name}",
            "hint": a.name,
            "extra": {
                "partner_mode": a.partner_mode,
                "needs_bank": a.needs_bank,
                "tracks_item": a.tracks_item,
            },
        }
        for a in rows[: _lim(limit)]
    ]


@router.get("/lookup/partners")
def lookup_partners(
    q: str = "",
    role: str | None = None,
    limit: int = 20,
    user: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    rows = partners.search(
        db, user.company_id, q, role=role, limit=_lim(limit)
    )
    out = []
    for p in rows:
        hide = partners.hide_personal(p, user)
        address, tax = ("", "") if hide else (p.address, p.tax_code)
        out.append(
            {
                "value": p.id,
                "label": f"{p.code} – {p.name}",
                "hint": tax or address,
                "extra": {
                    "name": p.name,
                    "address": address,
                    "tax_code": tax,
                    "code": p.code,
                },
            }
        )
    return out


@router.get("/lookup/items")
def lookup_items(
    q: str = "",
    kind: str | None = None,
    limit: int = 20,
    user: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    rows = items.search(db, user.company_id, q, kind=kind, limit=_lim(limit))
    return [
        {
            "value": it.id,
            "label": f"{it.code} – {it.name}",
            "hint": it.unit,
            "extra": {
                "name": it.name,
                "unit": it.unit,
                "vat_rate": it.vat_rate,
                "kind": it.kind,
                "sale_price": str(it.sale_price or 0),
                "purchase_price": str(it.purchase_price or 0),
                "stock_account": it.stock_account,
                "revenue_account": it.revenue_account,
                "expense_account": it.expense_account,
            },
        }
        for it in rows
    ]


@router.get("/lookup/bank-accounts")
def lookup_bank_accounts(
    q: str = "",
    user: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    rows = list_bank_accounts(db, user.company_id)
    ql = q.strip().lower()
    if ql:
        rows = [
            b
            for b in rows
            if ql in b.label.lower() or ql in b.bank_name.lower()
        ]
    return [
        {
            "value": b.id,
            "label": b.label,
            "hint": b.bank_name,
            "extra": {"account_code": b.account_code},
        }
        for b in rows
    ]


@router.get("/search")
def search(
    request: Request,
    q: str = Query("", max_length=100),
    user: CurrentUser = Depends(get_member),
    db: Session = Depends(get_tenant_db),
):
    """Global search: features, vouchers, partners, accounts, items."""
    _ = get_translator(resolve_lang(request))
    term = q.strip()
    results: list[dict] = []
    tl = term.lower()
    # Features (menu)
    for section in build_nav(user, "", _):
        for it in section["items"]:
            if not tl or tl in it["label"].lower():
                results.append(
                    {
                        "label": it["label"],
                        "hint": section["label"],
                        "href": it["href"],
                        "icon": it["icon"],
                        "group": _("Chức năng"),
                    }
                )
    if not term:
        return {"results": results[:12]}
    load_features()
    # Only voucher types the user may view (cashier: cash receipts and
    # payments; payroll only for users allowed to view payroll).
    visible_types = [
        code for code in registry.doc_types if can_entry(user, code, "view")
    ]
    if visible_types:
        like = f"%{tl}%"
        stmt = (
            select(JournalEntry)
            .where(
                JournalEntry.company_id == user.company_id,
                JournalEntry.doc_type.in_(visible_types),
                or_(
                    ulower(JournalEntry.number).like(like),
                    ulower(JournalEntry.description).like(like),
                    ulower(JournalEntry.contact_name).like(like),
                    ulower(JournalEntry.ref_no).like(like),
                ),
            )
            .order_by(JournalEntry.date.desc(), JournalEntry.id.desc())
            .limit(8)
        )
        for e in db.scalars(stmt):
            results.append(
                {
                    "label": (
                        f"{e.display_number} · "
                        f"{e.description or e.contact_name}"
                    ),
                    "hint": (
                        f"{e.date:%d/%m/%Y} · {format_vnd(e.total)} · "
                        f"{_(e.status_label)}"
                    ),
                    "href": e.url,
                    "icon": "file-text",
                    "group": _("Chứng từ"),
                }
            )
    if user.can("catalog.view"):
        for p in partners.search(
            db, user.company_id, term, limit=5, active=None
        ):
            hint = (
                ""
                if partners.hide_personal(p, user)
                else (p.tax_code or p.address)
            )
            results.append(
                {
                    "label": f"{p.code} – {p.name}",
                    "hint": hint,
                    "href": f"/partners/{p.id}",
                    "icon": "users",
                    "group": _("Đối tượng"),
                }
            )
        for a in list_accounts(db, user.company_id, q=term)[:5]:
            results.append(
                {
                    "label": f"{a.code} – {a.name}",
                    "hint": _("Tài khoản"),
                    "href": f"/accounts?q={a.code}",
                    "icon": "book-open",
                    "group": _("Tài khoản"),
                }
            )
        for it in items.search(
            db, user.company_id, term, limit=5, active=None
        ):
            results.append(
                {
                    "label": f"{it.code} – {it.name}",
                    "hint": it.unit,
                    "href": f"/items/{it.id}",
                    "icon": "package",
                    "group": _("Hàng hóa, dịch vụ"),
                }
            )
    if user.can("assets.view"):
        try:
            from app.features.assets.models import FixedAsset

            like = f"%{tl}%"
            for a in db.scalars(
                select(FixedAsset)
                .where(
                    FixedAsset.company_id == user.company_id,
                    or_(
                        ulower(FixedAsset.code).like(like),
                        ulower(FixedAsset.name).like(like),
                    ),
                )
                .limit(5)
            ):
                results.append(
                    {
                        "label": f"{a.code} – {a.name}",
                        "hint": a.department or _("Tài sản cố định"),
                        "href": f"/assets/{a.id}",
                        "icon": "building",
                        "group": _("Tài sản cố định"),
                    }
                )
        except ImportError:
            pass
    return {"results": results[:30]}
