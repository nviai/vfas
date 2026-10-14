"""Money vouchers.

Cash receipts/payments (PT/PC, TK 1111) and bank receipts/payments
(BC/BN, TK 112x).

A money voucher has one fixed money side (debit on receipts, credit on
payments) plus one or more offsetting lines. It may carry a VAT
invoice:
- payments (PC/BN): add a line Debit 1331 / Credit the money account
  and VatIn(direction="in");
- receipts (PT/BC): add a line Debit the money account / Credit 33311
  and VatIn(direction="out").
Shared by the cash and bank modules; every entry goes through the
journal.service engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.dates import parse_date, today
from app.core.db import ulower
from app.core.errors import BusinessError
from app.core.money import MoneyError, parse_vnd
from app.features.accounts.models import Account
from app.features.bank.models import BankAccount
from app.features.items.models import VAT_RATES
from app.features.journal import service as js
from app.features.journal.models import (
    CANCELLED,
    DRAFT,
    POSTED,
    JournalEntry,
    JournalLine,
)
from app.features.org.models import Company
from app.features.partners.models import Partner

CASH_ACCOUNT = "1111"
CASH_KINDS = ("PT", "PC")
BANK_KINDS = ("BC", "BN")
IN_KINDS = ("PT", "BC")
KIND_LABELS = {
    "PT": "Phiếu thu",
    "PC": "Phiếu chi",
    "BC": "Thu tiền gửi",
    "BN": "Chi tiền gửi",
    "CK": "Chuyển tiền nội bộ",
}
VAT_IN_ACCOUNT = "1331"
VAT_OUT_ACCOUNT = "33311"
MAX_AMOUNT = js.MAX_AMOUNT


def is_in(kind: str) -> bool:
    return kind in IN_KINDS


@dataclass
class ContraLine:
    account: str
    amount: int
    description: str = ""
    partner_id: int | None = None


@dataclass
class VatInfo:
    goods_amount: int
    vat_rate: str
    vat_amount: int
    invoice_no: str = ""
    invoice_series: str = ""
    invoice_template: str = ""
    invoice_date: date | None = None
    partner_name: str = ""
    partner_tax_code: str = ""


@dataclass
class MoneyVoucher:
    kind: str
    date: date
    doc_date: date | None
    description: str
    lines: list[ContraLine]
    contact_name: str = ""
    contact_address: str = ""
    partner_id: int | None = None
    attached_docs: int = 0
    ref_no: str = ""
    # bank account of the money side (BC/BN)
    bank_account_id: int | None = None
    # for offsetting lines on a bank account (withdrawals/deposits)
    contra_bank_account_id: int | None = None
    vat: VatInfo | None = None
    source: str = "cash"
    source_id: int | None = None
    meta: dict = field(default_factory=dict)

    @property
    def lines_total(self) -> int:
        return sum(ln.amount for ln in self.lines)

    @property
    def total(self) -> int:
        return self.lines_total + (self.vat.vat_amount if self.vat else 0)


# --------------------------------------------------------- form parsing


def _money(
    raw, errors: dict, key: str, *, required: bool = False
) -> int | None:
    if raw in (None, ""):
        if required:
            errors[key] = "Nhập số tiền"
        return None
    try:
        v = parse_vnd(raw)
    except MoneyError as e:
        errors[key] = str(e)
        return None
    if v < 0:
        errors[key] = "Số tiền không được âm"
        return None
    if v > MAX_AMOUNT:
        errors[key] = "Số tiền quá lớn"
        return None
    return v


def _int_or_none(raw) -> int | None:
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def vat_from_rate(goods: int, rate: str) -> int:
    if rate not in ("0", "5", "8", "10"):
        return 0
    return int(
        (Decimal(goods) * Decimal(rate) / Decimal(100)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )


def parse_form(
    form,
    kind: str,
    *,
    company: Company,
    money_account: str = CASH_ACCOUNT,
    allow_future: bool = False,
) -> tuple[MoneyVoucher | None, dict[str, str]]:
    """Validate a money voucher form (quick or multi-line mode).

    Return (voucher, errors by field).
    """
    get = form.get
    errors: dict[str, str] = {}
    d = doc_date = None
    try:
        d = parse_date(get("date"))
        if d is None:
            errors["date"] = "Nhập ngày hạch toán"
    except ValueError:
        errors["date"] = "Ngày không hợp lệ"
    try:
        doc_date = parse_date(get("doc_date")) or d
    except ValueError:
        errors["doc_date"] = "Ngày không hợp lệ"
    if d is not None:
        if d < company.start_date:
            errors["date"] = (
                "Ngày hạch toán không được trước ngày bắt đầu "
                f"{company.start_date:%d/%m/%Y}"
            )
        elif d > today() and not allow_future:
            errors["date"] = "Thu, chi tiền không được ghi ở ngày tương lai"
    if d and doc_date and doc_date > d:
        errors["doc_date"] = "Ngày chứng từ không được sau ngày hạch toán"

    partner_id = _int_or_none(get("partner_id"))
    contact = (get("contact_name") or "").strip()
    description = (get("description") or "").strip()
    if not description:
        errors["description"] = "Nhập lý do / diễn giải"
    if kind in BANK_KINDS and not _int_or_none(get("bank_account_id")):
        errors["bank_account_id"] = "Chọn tài khoản ngân hàng"
    if kind in CASH_KINDS and not contact and not partner_id:
        errors["contact_name"] = (
            "Nhập họ tên người nộp tiền"
            if is_in(kind)
            else "Nhập họ tên người nhận tiền"
        )
    try:
        attached = int(get("attached_docs") or 0)
        if attached < 0:
            raise ValueError
    except ValueError:
        attached, errors["attached_docs"] = 0, "Số chứng từ gốc không hợp lệ"

    lines: list[ContraLine] = []
    side = "credit_account" if is_in(kind) else "debit_account"
    if (get("mode") or "quick") == "detail":
        try:
            count = min(int(get("lines-count") or 0), 200)
        except ValueError:
            count = 0
        line_errors = []
        for i in range(count):
            acc = (
                get(f"lines-{i}-{side}") or get(f"lines-{i}-account") or ""
            ).strip()
            raw = get(f"lines-{i}-amount")
            desc = (get(f"lines-{i}-description") or "").strip()
            if not acc and raw in (None, "", "0") and not desc:
                continue
            tmp: dict = {}
            amount = _money(raw, tmp, "amount")
            if tmp:
                line_errors.append(f"Dòng {i + 1}: {tmp['amount']}")
                continue
            if not amount:
                line_errors.append(f"Dòng {i + 1}: số tiền phải lớn hơn 0")
                continue
            if not acc:
                line_errors.append(f"Dòng {i + 1}: chọn tài khoản đối ứng")
                continue
            lines.append(
                ContraLine(
                    account=acc,
                    amount=amount,
                    description=desc or description,
                    partner_id=_int_or_none(get(f"lines-{i}-partner_id")),
                )
            )
        if line_errors:
            errors["lines"] = "; ".join(line_errors)
        elif not lines:
            errors["lines"] = "Nhập ít nhất một dòng đối ứng có số tiền"
    else:
        acc = (get("account") or get("account_other") or "").strip()
        amount = _money(get("amount"), errors, "amount", required=True)
        if amount == 0:
            errors["amount"] = "Số tiền phải lớn hơn 0"
        if not acc:
            errors["account"] = (
                "Chọn mục đích thu hoặc tài khoản đối ứng"
                if is_in(kind)
                else "Chọn mục đích chi hoặc tài khoản đối ứng"
            )
        if acc and amount:
            lines.append(
                ContraLine(
                    account=acc,
                    amount=amount,
                    description=description,
                    partner_id=partner_id,
                )
            )

    vat = None
    if get("vat_on") in ("1", "on", "true"):
        rate = (get("vat_rate") or "10").strip()
        if rate not in VAT_RATES:
            errors["vat_rate"] = "Thuế suất không hợp lệ"
        goods = _money(get("vat_goods_amount"), errors, "vat_goods_amount")
        if goods is None:
            goods = sum(ln.amount for ln in lines)
        vat_amount = _money(get("vat_amount"), errors, "vat_amount")
        if vat_amount is None:
            vat_amount = vat_from_rate(goods, rate)
        inv_date = None
        try:
            inv_date = parse_date(get("vat_invoice_date")) or doc_date
        except ValueError:
            errors["vat_invoice_date"] = "Ngày hóa đơn không hợp lệ"
        if not (get("vat_invoice_no") or "").strip():
            errors["vat_invoice_no"] = "Nhập số hóa đơn"
        vat = VatInfo(
            goods_amount=goods,
            vat_rate=rate,
            vat_amount=vat_amount,
            invoice_no=(get("vat_invoice_no") or "").strip()[:30],
            invoice_series=(get("vat_invoice_series") or "").strip()[:20],
            invoice_template=(get("vat_invoice_template") or "").strip()[:20],
            invoice_date=inv_date,
            partner_name=(get("vat_partner_name") or contact).strip()[:255],
            partner_tax_code=(get("vat_partner_tax_code") or "").strip()[:20],
        )

    if errors:
        return None, errors
    return MoneyVoucher(
        kind=kind,
        date=d,
        doc_date=doc_date,
        description=description[:500],
        lines=lines,
        contact_name=contact[:200],
        contact_address=(get("contact_address") or "").strip()[:300],
        partner_id=partner_id,
        attached_docs=attached,
        ref_no=(get("ref_no") or "").strip()[:100],
        bank_account_id=_int_or_none(get("bank_account_id")),
        contra_bank_account_id=_int_or_none(get("contra_bank_account_id")),
        vat=vat,
        source_id=_int_or_none(get("cash_count_id")),
        source="cashcount" if _int_or_none(get("cash_count_id")) else "cash",
    ), {}


# ------------------------------------------- building the journal entry


def money_side(
    db: Session, company_id: int, v: MoneyVoucher
) -> tuple[str, int | None]:
    """Return (money account, bank account id) of the money side."""
    if v.kind in CASH_KINDS:
        return CASH_ACCOUNT, None
    bank = (
        db.get(BankAccount, v.bank_account_id) if v.bank_account_id else None
    )
    if bank is None or bank.company_id != company_id:
        return "1121", v.bank_account_id
    return bank.account_code, bank.id


def to_entry(db: Session, company_id: int, v: MoneyVoucher) -> js.EntryIn:
    accounts = {
        a.code: a
        for a in db.scalars(
            select(Account).where(Account.company_id == company_id)
        )
    }
    money_acc, money_bank = money_side(db, company_id, v)
    incoming = is_in(v.kind)
    lines: list[js.LineIn] = []
    for ln in v.lines:
        acc = accounts.get(ln.account)
        partner = ln.partner_id
        if (
            partner is None
            and v.partner_id
            and acc is not None
            and acc.partner_mode != "none"
        ):
            partner = v.partner_id
        contra_bank = (
            v.contra_bank_account_id
            if acc is not None and acc.needs_bank
            else None
        )
        if incoming:
            lines.append(
                js.LineIn(
                    debit_account=money_acc,
                    credit_account=ln.account,
                    amount=ln.amount,
                    description=ln.description,
                    debit_bank_account_id=money_bank,
                    credit_partner_id=partner,
                    credit_bank_account_id=contra_bank,
                )
            )
        else:
            lines.append(
                js.LineIn(
                    debit_account=ln.account,
                    credit_account=money_acc,
                    amount=ln.amount,
                    description=ln.description,
                    debit_partner_id=partner,
                    debit_bank_account_id=contra_bank,
                    credit_bank_account_id=money_bank,
                )
            )
    vat_records = []
    if v.vat and v.vat.vat_amount > 0:
        if incoming:
            lines.append(
                js.LineIn(
                    debit_account=money_acc,
                    credit_account=VAT_OUT_ACCOUNT,
                    amount=v.vat.vat_amount,
                    description=(
                        f"Thuế GTGT đầu ra HĐ {v.vat.invoice_no}".strip()
                    ),
                    debit_bank_account_id=money_bank,
                )
            )
        else:
            lines.append(
                js.LineIn(
                    debit_account=VAT_IN_ACCOUNT,
                    credit_account=money_acc,
                    amount=v.vat.vat_amount,
                    description=(
                        f"Thuế GTGT đầu vào HĐ {v.vat.invoice_no}".strip()
                    ),
                    credit_bank_account_id=money_bank,
                )
            )
    if v.vat:
        vat_records.append(
            js.VatIn(
                direction="out" if incoming else "in",
                goods_amount=v.vat.goods_amount,
                vat_rate=v.vat.vat_rate,
                vat_amount=v.vat.vat_amount,
                invoice_no=v.vat.invoice_no,
                invoice_series=v.vat.invoice_series,
                invoice_template=v.vat.invoice_template,
                invoice_date=v.vat.invoice_date,
                partner_id=v.partner_id,
                partner_name=v.vat.partner_name or v.contact_name,
                partner_tax_code=v.vat.partner_tax_code,
                description=v.description,
                vat_account=VAT_OUT_ACCOUNT if incoming else VAT_IN_ACCOUNT,
            )
        )
    meta = dict(v.meta or {})
    meta["form"] = "money"
    return js.EntryIn(
        doc_type=v.kind,
        date=v.date,
        doc_date=v.doc_date,
        lines=lines,
        description=v.description,
        partner_id=v.partner_id,
        contact_name=v.contact_name,
        contact_address=v.contact_address,
        ref_no=v.ref_no,
        attached_docs=v.attached_docs,
        source=v.source,
        source_id=v.source_id,
        meta=meta,
        vat=vat_records,
    )


def fill_contact(db: Session, company_id: int, v: MoneyVoucher) -> None:
    """Fill the contact name/address from the selected partner.

    Only empty fields are filled; the partner usage is recorded.
    """
    if not v.partner_id:
        return
    p = db.get(Partner, v.partner_id)
    if p is None or p.company_id != company_id:
        return
    if not v.contact_name:
        v.contact_name = p.name
    if not v.contact_address:
        v.contact_address = p.address or ""
    from app.features.partners.service import touch

    touch(p)


def save(
    db: Session,
    ctx,
    v: MoneyVoucher,
    *,
    entry: JournalEntry | None = None,
    post: bool = False,
) -> JournalEntry:
    """Create or update a draft, optionally posting it right away.

    The engine checks locked periods, retroactive negative cash, etc.
    """
    if v.source == "cashcount" and v.source_id:
        from app.features.cash.models import CashCount

        cc = db.get(CashCount, v.source_id)
        if cc is None or cc.company_id != ctx.company_id:
            raise BusinessError("Biên bản kiểm kê không hợp lệ")
    fill_contact(db, ctx.company_id, v)
    inp = to_entry(db, ctx.company_id, v)
    if entry is None:
        e = js.create_entry(db, ctx, inp)
    else:
        if entry.meta:
            keep = {
                k: val for k, val in entry.meta.items() if k not in inp.meta
            }
            inp.meta = {**keep, **inp.meta}
        inp.source, inp.source_id = entry.source, entry.source_id
        e = js.update_draft(db, ctx, entry, inp)
    if post:
        js.post_entry(db, ctx, e)
    return e


# ----------------------------------- reading back for display / editing


@dataclass
class EntryView:
    """A money voucher split into money side and offsetting lines.

    Used for display and to refill the form.
    """

    entry: JournalEntry
    money_account: str
    bank_account_id: int | None
    lines: list[dict]  # {account, amount, partner_id, description}
    vat_line_amount: int
    incoming: bool

    @property
    def amount(self) -> int:
        return self.entry.total


def view(db: Session, e: JournalEntry) -> EntryView:
    incoming = (
        is_in(e.doc_type) if e.doc_type in IN_KINDS + ("PC", "BN") else False
    )
    money_acc = CASH_ACCOUNT if e.doc_type in CASH_KINDS else None
    bank_id = None
    lines, vat_amount = [], 0
    for ln in e.lines:
        if incoming:
            money, contra, partner = (
                ln.debit_account,
                ln.credit_account,
                ln.credit_partner_id,
            )
            bank_id = bank_id or ln.debit_bank_account_id
        else:
            money, contra, partner = (
                ln.credit_account,
                ln.debit_account,
                ln.debit_partner_id,
            )
            bank_id = bank_id or ln.credit_bank_account_id
        money_acc = money_acc or money
        if (
            e.vat_records
            and contra in (VAT_IN_ACCOUNT, VAT_OUT_ACCOUNT)
            and any(v.vat_account == contra for v in e.vat_records)
        ):
            vat_amount += ln.amount
            continue
        lines.append(
            {
                "account": contra,
                "amount": ln.amount,
                "partner_id": partner,
                "description": ln.description,
                "money_account": money,
            }
        )
    return EntryView(
        entry=e,
        money_account=money_acc or CASH_ACCOUNT,
        bank_account_id=bank_id,
        lines=lines,
        vat_line_amount=vat_amount,
        incoming=incoming,
    )


def form_values(db: Session, e: JournalEntry) -> dict:
    """Return the form values for editing a draft."""
    ev = view(db, e)
    vals = {
        "date": e.date,
        "doc_date": e.doc_date,
        "description": e.description,
        "contact_name": e.contact_name,
        "contact_address": e.contact_address,
        "partner_id": e.partner_id,
        "attached_docs": e.attached_docs,
        "ref_no": e.ref_no,
        "bank_account_id": ev.bank_account_id,
        "lines": ev.lines,
        "cash_count_id": e.source_id if e.source == "cashcount" else None,
    }
    contra_bank = None
    for ln in e.lines:
        contra_bank = contra_bank or (
            ln.credit_bank_account_id
            if ev.incoming
            else ln.debit_bank_account_id
        )
    vals["contra_bank_account_id"] = contra_bank
    if len(ev.lines) == 1 and not e.vat_records:
        vals["account"] = ev.lines[0]["account"]
        vals["amount"] = ev.lines[0]["amount"]
    if e.vat_records:
        v = e.vat_records[0]
        vals.update(
            {
                "vat_on": True,
                "vat_rate": v.vat_rate,
                "vat_amount": v.vat_amount,
                "vat_goods_amount": v.goods_amount,
                "vat_invoice_no": v.invoice_no,
                "vat_invoice_series": v.invoice_series,
                "vat_invoice_template": v.invoice_template,
                "vat_invoice_date": v.invoice_date,
                "vat_partner_name": v.partner_name,
                "vat_partner_tax_code": v.partner_tax_code,
            }
        )
    return vals


# ----------------------------------------------------------------- list


@dataclass
class ListFilter:
    kinds: tuple[str, ...]
    status: str = ""
    q: str = ""
    start: date | None = None
    end: date | None = None
    account: str = ""
    bank_account_id: int | None = None


def _where(company_id: int, f: ListFilter):
    je, jl = JournalEntry, JournalLine
    conds = [je.company_id == company_id, je.doc_type.in_(f.kinds)]
    if f.status in (DRAFT, POSTED, CANCELLED):
        conds.append(je.status == f.status)
    if f.start:
        conds.append(je.date >= f.start)
    if f.end:
        conds.append(je.date <= f.end)
    if f.q:
        like = f"%{f.q.strip().lower()}%"
        conds.append(
            or_(
                ulower(je.number).like(like),
                ulower(je.description).like(like),
                ulower(je.contact_name).like(like),
                ulower(je.ref_no).like(like),
            )
        )
    if f.account:
        conds.append(
            exists(
                select(jl.id).where(
                    jl.entry_id == je.id,
                    or_(
                        jl.debit_account.like(f"{f.account}%"),
                        jl.credit_account.like(f"{f.account}%"),
                    ),
                )
            )
        )
    if f.bank_account_id:
        conds.append(
            exists(
                select(jl.id).where(
                    jl.entry_id == je.id,
                    or_(
                        jl.debit_bank_account_id == f.bank_account_id,
                        jl.credit_bank_account_id == f.bank_account_id,
                    ),
                )
            )
        )
    return and_(*conds)


def list_entries(
    db: Session,
    company_id: int,
    f: ListFilter,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[JournalEntry]:
    je = JournalEntry
    return list(
        db.scalars(
            select(je)
            .where(_where(company_id, f))
            .order_by(
                je.date.desc(),
                je.posting_seq.desc().nulls_first(),
                je.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )


def count_entries(db: Session, company_id: int, f: ListFilter) -> int:
    return int(
        db.scalar(
            select(func.count(JournalEntry.id)).where(_where(company_id, f))
        )
        or 0
    )


def status_counts(
    db: Session, company_id: int, f: ListFilter
) -> dict[str, int]:
    base = ListFilter(**{**f.__dict__, "status": ""})
    rows = db.execute(
        select(JournalEntry.status, func.count(JournalEntry.id))
        .where(_where(company_id, base))
        .group_by(JournalEntry.status)
    ).all()
    out = {s: int(n) for s, n in rows}
    out["all"] = sum(out.values())
    return out


def totals_by_kind(
    db: Session, company_id: int, f: ListFilter
) -> dict[str, int]:
    """Return the total of POSTED vouchers by kind.

    Feeds the summary cards of the list page.
    """
    base = ListFilter(**{**f.__dict__, "status": POSTED})
    rows = db.execute(
        select(
            JournalEntry.doc_type,
            func.coalesce(func.sum(JournalEntry.total), 0),
        )
        .where(_where(company_id, base))
        .group_by(JournalEntry.doc_type)
    ).all()
    return {k: int(v) for k, v in rows}
