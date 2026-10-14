"""Import XML e-invoices.

Flow: preview -> create draft purchase (input) / sales (output)
documents, skipping duplicates.
"""

from __future__ import annotations

import json
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import ulower
from app.core.errors import BusinessError, NotFoundError
from app.features.items.models import Item
from app.features.journal import service as js
from app.features.org.models import Company
from app.features.org.service import normalize_tax_code
from app.features.partners.models import Partner
from app.features.purchases import service as purchases
from app.features.purchases.einvoice import EInvoice, read_files
from app.features.purchases.models import PurchaseDoc
from app.features.sales import service as sales
from app.features.sales.common import TradeHeader, TradeLine
from app.features.sales.models import SalesDoc

PURCHASE_ACCOUNTS = [
    ("156", "156 – Hàng hóa"),
    ("152", "152 – Nguyên liệu, vật liệu"),
    ("153", "153 – Công cụ, dụng cụ"),
    ("6422", "6422 – Chi phí quản lý doanh nghiệp"),
    ("6421", "6421 – Chi phí bán hàng"),
    ("242", "242 – Chi phí trả trước"),
    ("2111", "2111 – TSCĐ hữu hình"),
]
SALES_ACCOUNTS = [
    ("5111", "5111 – Doanh thu bán hàng hóa"),
    ("5113", "5113 – Doanh thu cung cấp dịch vụ"),
    ("5112", "5112 – Doanh thu bán thành phẩm"),
]


@dataclass
class Preview:
    token: str
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def importable(self) -> int:
        return sum(1 for r in self.rows if r["status"] == "new")


def _dir(company_id: int, token: str) -> Path:
    if not token.isalnum():
        raise NotFoundError("Phiên nhập không hợp lệ")
    return get_settings().imports_dir / "einvoices" / str(company_id) / token


def _existing_keys(db: Session, company_id: int) -> set[str]:
    keys = set(
        db.scalars(
            select(PurchaseDoc.einvoice_key).where(
                PurchaseDoc.company_id == company_id,
                PurchaseDoc.einvoice_key.is_not(None),
            )
        )
    )
    keys |= set(
        db.scalars(
            select(SalesDoc.einvoice_key).where(
                SalesDoc.company_id == company_id,
                SalesDoc.einvoice_key.is_not(None),
            )
        )
    )
    return keys


def direction_of(company: Company, inv: EInvoice) -> str | None:
    mst = normalize_tax_code(company.tax_code)
    if mst and normalize_tax_code(inv.buyer.tax_code) == mst:
        return "in"
    if mst and normalize_tax_code(inv.seller.tax_code) == mst:
        return "out"
    return None


def _classify(
    db: Session, company: Company, invoices: list[EInvoice]
) -> list[dict]:
    existing = _existing_keys(db, company.id)
    seen: set[str] = set()
    rows = []
    for inv in invoices:
        direction = direction_of(company, inv)
        status = "new"
        note = ""
        if direction is None:
            status, note = (
                "foreign",
                "MST người mua/người bán không phải của công ty",
            )
        elif inv.key in existing:
            status, note = "duplicate", "Đã nhập trước đó"
        elif inv.key in seen:
            status, note = "duplicate", "Trùng trong lần tải lên này"
        elif inv.issue_date is None:
            status, note = "error", "Thiếu ngày lập hóa đơn"
        elif inv.issue_date < company.start_date:
            status, note = "error", "Ngày hóa đơn trước ngày bắt đầu hạch toán"
        elif not inv.lines:
            status, note = "error", "Hóa đơn không có dòng hàng hóa, dịch vụ"
        seen.add(inv.key)
        other = inv.seller if direction == "in" else inv.buyer
        rows.append(
            {
                "key": inv.key,
                "filename": inv.filename,
                "direction": direction,
                "status": status,
                "note": note,
                "ref": inv.ref,
                "date": inv.issue_date,
                "partner": other.name,
                "tax_code": other.tax_code,
                "lines": len(inv.lines),
                "goods": sum(ln.amount for ln in inv.lines),
                "vat": sum(ln.vat_amount for ln in inv.lines),
                "total": inv.total_payment,
                "warnings": inv.warnings,
                "has_code": bool(inv.tax_authority_code),
            }
        )
    return rows


def preview(db: Session, ctx, files: list[tuple[str, bytes]]) -> Preview:
    company = db.get(Company, ctx.company_id)
    if not files:
        raise BusinessError("Chọn ít nhất một file XML hoặc ZIP")
    invoices, errors = read_files(files)
    from app.core.tempfiles import cleanup_imports_throttled

    cleanup_imports_throttled()
    token = secrets.token_hex(12)
    d = _dir(ctx.company_id, token)
    d.mkdir(parents=True, exist_ok=True)
    for i, inv in enumerate(invoices):
        (d / f"{i:04d}.xml").write_bytes(inv.raw)
    (d / "meta.json").write_text(
        json.dumps(
            {"names": [inv.filename for inv in invoices], "user": ctx.user_id}
        )
    )
    return Preview(
        token=token, rows=_classify(db, company, invoices), errors=errors
    )


def load(db: Session, ctx, token: str) -> tuple[list[EInvoice], Preview]:
    d = _dir(ctx.company_id, token)
    if not (d / "meta.json").exists():
        raise NotFoundError(
            "Phiên nhập đã hết hạn hoặc đã hoàn tất. Tải file lên lại."
        )
    meta = json.loads((d / "meta.json").read_text())
    files = [
        (name, (d / f"{i:04d}.xml").read_bytes())
        for i, name in enumerate(meta["names"])
    ]
    invoices, errors = read_files(files)
    company = db.get(Company, ctx.company_id)
    return invoices, Preview(
        token=token, rows=_classify(db, company, invoices), errors=errors
    )


def discard(ctx, token: str) -> None:
    shutil.rmtree(_dir(ctx.company_id, token), ignore_errors=True)


def _match_item(
    db: Session, company_id: int, code: str, name: str
) -> Item | None:
    if code:
        it = db.scalar(
            select(Item).where(
                Item.company_id == company_id, Item.code == code.strip()
            )
        )
        if it:
            return it
    if name:
        return db.scalar(
            select(Item).where(
                Item.company_id == company_id,
                ulower(Item.name) == name.strip().lower(),
            )
        )
    return None


def _trade_lines(
    db: Session,
    company_id: int,
    inv: EInvoice,
    direction: str,
    default_account: str,
) -> list[TradeLine]:
    lines = []
    for ln in inv.lines:
        item = _match_item(db, company_id, ln.code, ln.description)
        if direction == "in":
            account = (
                purchases.default_debit_account(item)
                if item
                else default_account
            )
        else:
            account = (
                item.revenue_account
                if item and item.revenue_account
                else default_account
            )
        lines.append(
            TradeLine(
                description=ln.description[:500],
                amount=ln.amount,
                vat_rate=ln.vat_rate,
                vat_amount=ln.vat_amount,
                item_id=item.id if item else None,
                unit=ln.unit[:30],
                quantity=ln.quantity,
                unit_price=ln.unit_price,
                account=account,
            )
        )
    return lines


def _header(
    db: Session, company_id: int, inv: EInvoice, direction: str
) -> TradeHeader:
    other = inv.seller if direction == "in" else inv.buyer
    tax = normalize_tax_code(other.tax_code)
    partner = None
    if tax:
        partner = db.scalar(
            select(Partner).where(
                Partner.company_id == company_id, Partner.tax_code == tax
            )
        )
    return TradeHeader(
        date=inv.issue_date,
        doc_date=inv.issue_date,
        partner_id=partner.id if partner else None,
        partner_name=other.name[:255],
        address=other.address[:300],
        tax_code=tax,
        contact_name=(other.buyer_person if direction == "out" else "")[:200],
        invoice_template=inv.template[:20],
        invoice_series=inv.series[:20],
        invoice_no=inv.number[:30],
        invoice_date=inv.issue_date,
        payment_method="credit",
        bank_account_id=None,
        due_date=None,
        description="",
        note=f"Nhập từ hóa đơn điện tử {inv.filename}",
    )


def confirm(
    db: Session,
    ctx,
    token: str,
    keys: set[str],
    *,
    purchase_account: str = "6422",
    sales_account: str = "5113",
) -> dict:
    """Create draft documents for the selected invoices.

    Returns the count per direction and the list of errors.
    """
    invoices, prev = load(db, ctx, token)
    status = {r["key"]: r for r in prev.rows}
    result = {"in": 0, "out": 0, "errors": [], "entry_ids": []}
    for inv in invoices:
        row = status.get(inv.key)
        if inv.key not in keys or row is None or row["status"] != "new":
            continue
        direction = row["direction"]
        perm = "purchases.create" if direction == "in" else "sales.create"
        if not ctx.can(perm):
            result["errors"].append(
                f"{inv.ref}: không có quyền lập chứng từ "
                f"{'mua' if direction == 'in' else 'bán'}"
            )
            continue
        header = _header(db, ctx.company_id, inv, direction)
        lines = _trade_lines(
            db,
            ctx.company_id,
            inv,
            direction,
            purchase_account if direction == "in" else sales_account,
        )
        try:
            if direction == "in":
                doc = purchases.create(
                    db,
                    ctx,
                    purchases.PurchaseInput(
                        kind="purchase",
                        header=header,
                        lines=lines,
                        einvoice_key=inv.key,
                    ),
                )
            else:
                doc = sales.create(
                    db,
                    ctx,
                    sales.SalesInput(
                        kind="sale",
                        header=header,
                        lines=lines,
                        einvoice_key=inv.key,
                    ),
                )
        except BusinessError as e:
            db.rollback()
            result["errors"].append(f"{inv.ref}: {e.message}")
            continue
        entry = db.get(js.JournalEntry, doc.entry_id)
        try:
            js.add_attachment(
                db, ctx, entry, inv.filename or f"{inv.ref}.xml", inv.raw
            )
        except BusinessError:
            db.rollback()
        result[direction] += 1
        result["entry_ids"].append(doc.entry_id)
    discard(ctx, token)
    return result
