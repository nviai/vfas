"""Voucher engine: draft → post → cancel; adjustments; numbering;
validation; attachments.

Every module (cash, bank, purchases/sales, inventory, fixed assets,
payroll, closing transfers) creates its vouchers through this engine.
The engine does NOT check permissions (each module checks its own:
cash.post, sales.post...).

Posting / cancelling runs inside `company_tx`, serialised per company
(row lock on companies). After changing the status and flushing, the
engine runs a retroactive negative-balance check for accounts that
must not go negative (1111, 1112, each 112x bank account) and calls the
posting_validators registered by modules (e.g. negative stock); any
violation → rollback.
"""

from __future__ import annotations

import re
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.audit import snapshot, write_audit
from app.core.config import get_settings
from app.core.dates import fmt_date
from app.core.db import is_sqlite
from app.core.errors import BusinessError, NegativeBalanceError, NotFoundError
from app.core.money import format_vnd
from app.core.registry import doc_label, emit, registry
from app.features.accounts.models import Account
from app.features.bank.models import BankAccount
from app.features.items.models import Item
from app.features.journal import locks, queries
from app.features.journal.models import (
    AUDIT_FIELDS,
    CANCELLED,
    DRAFT,
    POSTED,
    Attachment,
    JournalEntry,
    JournalLine,
    NumberSequence,
    VatRecord,
)
from app.features.org.models import DEFAULT_NUMBER_FORMAT, Company
from app.features.partners.models import Partner

MAX_AMOUNT = 10**15


class EntryValidationError(BusinessError):
    """Several validation errors.

    `errors` is the list of messages; `message` is the first one.
    """

    def __init__(self, errors: list[str]):
        super().__init__(
            errors[0], code="validation", details={"errors": errors}
        )
        self.errors = errors


@dataclass
class LineIn:
    debit_account: str
    credit_account: str
    amount: int
    description: str = ""
    debit_partner_id: int | None = None
    credit_partner_id: int | None = None
    debit_bank_account_id: int | None = None
    credit_bank_account_id: int | None = None
    item_id: int | None = None
    quantity: Decimal | None = None
    asset_id: int | None = None
    cash_flow: str | None = None


@dataclass
class VatIn:
    direction: str  # in | out
    goods_amount: int
    vat_rate: str
    vat_amount: int
    invoice_no: str = ""
    invoice_series: str = ""
    invoice_template: str = ""
    invoice_date: date | None = None
    partner_id: int | None = None
    partner_name: str = ""
    partner_tax_code: str = ""
    description: str = ""
    vat_account: str = ""
    deductible: bool = True
    group: str = "normal"


@dataclass
class EntryIn:
    doc_type: str
    date: date
    lines: list[LineIn]
    description: str = ""
    doc_date: date | None = None
    partner_id: int | None = None
    contact_name: str = ""
    contact_address: str = ""
    ref_no: str = ""
    attached_docs: int = 0
    source: str = "manual"
    source_id: int | None = None
    meta: dict = field(default_factory=dict)
    vat: list[VatIn] | None = None  # None = keep as is (draft edit)
    number: str | None = None  # only for imports keeping original no.
    reverses_id: int | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------- per-company transaction

_SQLITE_LOCK = threading.RLock()


@contextmanager
def company_tx(db: Session, company_id: int) -> Iterator[None]:
    """Posting transaction serialised per company.

    Commits on success, rolls back on error. Commits any pending
    session changes before starting (ending the previous read
    transaction).
    """
    lock = _SQLITE_LOCK if is_sqlite(db) else nullcontext()
    with lock:
        db.commit()
        try:
            db.execute(
                update(Company)
                .where(Company.id == company_id)
                .values(lock_version=Company.lock_version + 1)
            )
            yield
            db.commit()
        except Exception:
            db.rollback()
            raise


# ---------------------------------------------------------------- reads


def get_company(db: Session, company_id: int) -> Company:
    c = db.get(Company, company_id)
    if c is None:
        raise NotFoundError("Không tìm thấy công ty")
    return c


def get_entry(
    db: Session,
    ctx,
    entry_id: int,
    *,
    doc_types: tuple[str, ...] | None = None,
) -> JournalEntry:
    e = db.get(JournalEntry, entry_id)
    if (
        e is None
        or e.company_id != ctx.company_id
        or (doc_types and e.doc_type not in doc_types)
    ):
        raise NotFoundError("Không tìm thấy chứng từ")
    return e


def accounts_map(db: Session, company_id: int) -> dict[str, Account]:
    return {
        a.code: a
        for a in db.scalars(
            select(Account).where(Account.company_id == company_id)
        )
    }


# ----------------------------------------------------------- validation


def _ids(db: Session, model, company_id: int, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    return set(
        db.scalars(
            select(model.id).where(
                model.company_id == company_id, model.id.in_(ids)
            )
        )
    )


def validate(
    db: Session,
    company: Company,
    inp: EntryIn,
    *,
    for_post: bool,
    allow_incomplete: bool = False,
) -> None:
    """Validate voucher data. for_post: full validation for posting."""
    errors: list[str] = []
    if inp.doc_type not in registry.doc_types:
        errors.append(f"Loại chứng từ {inp.doc_type} không hợp lệ")
    if inp.date is None:
        errors.append("Nhập ngày hạch toán")
    elif inp.date < company.start_date:
        errors.append(
            "Ngày hạch toán trước ngày bắt đầu hạch toán "
            f"{fmt_date(company.start_date)}. "
            "Số liệu trước ngày này nhập vào số dư đầu kỳ."
        )
    if inp.doc_date and inp.date and inp.doc_date > inp.date:
        errors.append("Ngày chứng từ không được sau ngày hạch toán")
    if not inp.lines:
        errors.append("Chứng từ phải có ít nhất một dòng định khoản")
    accounts = accounts_map(db, company.id)
    partner_ids = {
        x
        for ln in inp.lines
        for x in (ln.debit_partner_id, ln.credit_partner_id)
        if x
    }
    if inp.partner_id:
        partner_ids.add(inp.partner_id)
    bank_ids = {
        x
        for ln in inp.lines
        for x in (ln.debit_bank_account_id, ln.credit_bank_account_id)
        if x
    }
    item_ids = {ln.item_id for ln in inp.lines if ln.item_id}
    ok_partners = _ids(db, Partner, company.id, partner_ids)
    banks = (
        {
            b.id: b
            for b in db.scalars(
                select(BankAccount).where(
                    BankAccount.company_id == company.id,
                    BankAccount.id.in_(bank_ids),
                )
            )
        }
        if bank_ids
        else {}
    )
    ok_items = _ids(db, Item, company.id, item_ids)
    if partner_ids - ok_partners:
        errors.append("Đối tượng không hợp lệ")
    if item_ids - ok_items:
        errors.append("Vật tư hàng hóa không hợp lệ")
    for n, ln in enumerate(inp.lines, start=1):
        if not isinstance(ln.amount, int) or ln.amount <= 0:
            errors.append(f"Dòng {n}: số tiền phải lớn hơn 0")
        elif ln.amount > MAX_AMOUNT:
            errors.append(f"Dòng {n}: số tiền quá lớn")
        for side, label in (("debit", "Nợ"), ("credit", "Có")):
            code = (getattr(ln, f"{side}_account") or "").strip()
            if not code:
                if for_post or not allow_incomplete:
                    errors.append(f"Dòng {n}: chưa chọn TK {label}")
                continue
            acc = accounts.get(code)
            if acc is None:
                errors.append(
                    f"Dòng {n}: TK {code} không có trong hệ thống tài khoản"
                )
                continue
            if not acc.is_postable:
                errors.append(
                    f"Dòng {n}: TK {code} là tài khoản tổng hợp, "
                    "hãy chọn tài khoản chi tiết"
                )
            if for_post and not acc.is_active:
                errors.append(f"Dòng {n}: TK {code} đang ngừng sử dụng")
            pid = getattr(ln, f"{side}_partner_id")
            if for_post and acc.partner_mode == "required" and not pid:
                errors.append(
                    f"Dòng {n}: TK {code} cần chọn đối tượng ({label})"
                )
            bid = getattr(ln, f"{side}_bank_account_id")
            if acc.needs_bank:
                if for_post and not bid:
                    errors.append(
                        f"Dòng {n}: TK {code} cần chọn tài khoản ngân hàng"
                    )
                elif bid and bid in banks and banks[bid].account_code != code:
                    errors.append(
                        f"Dòng {n}: tài khoản ngân hàng không thuộc TK {code}"
                    )
                elif bid and bid not in banks:
                    errors.append(
                        f"Dòng {n}: tài khoản ngân hàng không hợp lệ"
                    )
        if (
            ln.debit_account
            and ln.debit_account == ln.credit_account
            and (ln.debit_partner_id, ln.debit_bank_account_id)
            == (ln.credit_partner_id, ln.credit_bank_account_id)
        ):
            errors.append(f"Dòng {n}: TK Nợ và TK Có trùng nhau")
    for v in inp.vat or []:
        if v.direction not in ("in", "out"):
            errors.append("Hóa đơn GTGT: chiều không hợp lệ")
        if v.goods_amount < 0 or v.vat_amount < 0:
            errors.append("Hóa đơn GTGT: số tiền không được âm")
    if errors:
        raise EntryValidationError(errors)


def _entry_input_from(e: JournalEntry) -> EntryIn:
    return EntryIn(
        doc_type=e.doc_type,
        date=e.date,
        doc_date=e.doc_date,
        description=e.description,
        partner_id=e.partner_id,
        lines=[
            LineIn(
                debit_account=ln.debit_account,
                credit_account=ln.credit_account,
                amount=ln.amount,
                description=ln.description,
                debit_partner_id=ln.debit_partner_id,
                credit_partner_id=ln.credit_partner_id,
                debit_bank_account_id=ln.debit_bank_account_id,
                credit_bank_account_id=ln.credit_bank_account_id,
                item_id=ln.item_id,
                quantity=ln.quantity,
                asset_id=ln.asset_id,
                cash_flow=ln.cash_flow,
            )
            for ln in e.lines
        ],
        vat=None,
    )


# --------------------------------------------------------------- drafts


def _lines_summary(e: JournalEntry) -> list[str]:
    return [
        f"Nợ {ln.debit_account}/Có {ln.credit_account}: "
        f"{format_vnd(ln.amount)}"
        for ln in e.lines
    ]


def _snap(e: JournalEntry) -> dict:
    d = snapshot(e, AUDIT_FIELDS)
    d["lines"] = _lines_summary(e)
    return d


def _apply(db: Session, e: JournalEntry, inp: EntryIn) -> None:
    e.doc_type = inp.doc_type
    e.date = inp.date
    e.doc_date = inp.doc_date or inp.date
    e.description = (inp.description or "").strip()[:500]
    e.partner_id = inp.partner_id
    e.contact_name = (inp.contact_name or "").strip()[:200]
    e.contact_address = (inp.contact_address or "").strip()[:300]
    e.ref_no = (inp.ref_no or "").strip()[:100]
    e.attached_docs = max(0, int(inp.attached_docs or 0))
    e.source = inp.source or "manual"
    e.source_id = inp.source_id
    e.meta = dict(inp.meta or {})
    e.reverses_id = inp.reverses_id
    if inp.number:
        e.number = inp.number.strip()
    e.lines.clear()
    db.flush()
    for n, ln in enumerate(inp.lines, start=1):
        e.lines.append(
            JournalLine(
                company_id=e.company_id,
                line_no=n,
                description=(ln.description or "").strip()[:500],
                debit_account=(ln.debit_account or "").strip(),
                credit_account=(ln.credit_account or "").strip(),
                amount=int(ln.amount),
                debit_partner_id=ln.debit_partner_id,
                credit_partner_id=ln.credit_partner_id,
                debit_bank_account_id=ln.debit_bank_account_id,
                credit_bank_account_id=ln.credit_bank_account_id,
                item_id=ln.item_id,
                quantity=ln.quantity,
                asset_id=ln.asset_id,
                cash_flow=ln.cash_flow or None,
            )
        )
    e.total = sum(int(ln.amount) for ln in inp.lines)
    if inp.vat is not None:
        e.vat_records.clear()
        db.flush()
        for v in inp.vat:
            e.vat_records.append(
                VatRecord(
                    company_id=e.company_id,
                    direction=v.direction,
                    invoice_template=v.invoice_template or "",
                    invoice_series=v.invoice_series or "",
                    invoice_no=v.invoice_no or "",
                    invoice_date=v.invoice_date or inp.doc_date or inp.date,
                    partner_id=v.partner_id,
                    partner_name=v.partner_name or "",
                    partner_tax_code=v.partner_tax_code or "",
                    description=(v.description or "")[:500],
                    goods_amount=int(v.goods_amount),
                    vat_rate=v.vat_rate,
                    vat_amount=int(v.vat_amount),
                    vat_account=v.vat_account or "",
                    deductible=v.deductible,
                    group=v.group or "normal",
                )
            )


def create_entry(
    db: Session,
    ctx,
    inp: EntryIn,
    *,
    post: bool = False,
    allow_incomplete: bool = False,
    commit: bool = True,
) -> JournalEntry:
    """Create a draft voucher (post=True: post it immediately)."""
    company = get_company(db, ctx.company_id)
    validate(
        db, company, inp, for_post=False, allow_incomplete=allow_incomplete
    )
    e = JournalEntry(
        company_id=company.id,
        status=DRAFT,
        created_by=ctx.user_id,
        created_at=_now(),
        doc_type=inp.doc_type,
        date=inp.date,
    )
    db.add(e)
    db.flush()
    _apply(db, e, inp)
    db.flush()
    write_audit(
        db,
        ctx,
        action="entry.create",
        entity_type="entry",
        entity_id=e.id,
        summary=(
            f"Tạo {doc_label(e.doc_type).lower()} nháp {format_vnd(e.total)}"
        ),
        after=_snap(e),
    )
    if post:
        return post_entry(db, ctx, e)
    if commit:
        db.commit()
    return e


def update_draft(
    db: Session,
    ctx,
    e: JournalEntry,
    inp: EntryIn,
    *,
    allow_incomplete: bool = False,
    commit: bool = True,
) -> JournalEntry:
    if e.company_id != ctx.company_id:
        raise NotFoundError("Không tìm thấy chứng từ")
    if e.status != DRAFT:
        raise BusinessError(
            "Chứng từ đã ghi sổ không được sửa số liệu. "
            "Hãy hủy (kỳ đang mở) hoặc lập chứng từ điều chỉnh (kỳ đã khóa)."
        )
    company = get_company(db, ctx.company_id)
    validate(
        db, company, inp, for_post=False, allow_incomplete=allow_incomplete
    )
    before = _snap(e)
    _apply(db, e, inp)
    e.updated_at = _now()
    db.flush()
    after = _snap(e)
    changed = {k for k in after if after[k] != before.get(k)}
    if changed:
        write_audit(
            db,
            ctx,
            action="entry.update",
            entity_type="entry",
            entity_id=e.id,
            summary=f"Sửa {doc_label(e.doc_type).lower()} nháp #{e.id}",
            before={k: before.get(k) for k in changed},
            after={k: after[k] for k in changed},
        )
    if commit:
        db.commit()
    return e


def update_posted_info(
    db: Session,
    ctx,
    e: JournalEntry,
    *,
    description: str | None = None,
    contact_name: str | None = None,
    contact_address: str | None = None,
    ref_no: str | None = None,
    attached_docs: int | None = None,
) -> JournalEntry:
    """Edit the descriptive fields of a posted voucher.

    Amounts are not changed. The period must be open.
    """
    if e.status == CANCELLED:
        raise BusinessError("Chứng từ đã hủy, không được sửa")
    if e.status == DRAFT:
        raise BusinessError("Chứng từ nháp: dùng chức năng sửa nháp")
    locks.assert_open(db, ctx.company_id, e.date, "sửa chứng từ")
    before = snapshot(e, AUDIT_FIELDS)
    if description is not None:
        e.description = description.strip()[:500]
    if contact_name is not None:
        e.contact_name = contact_name.strip()[:200]
    if contact_address is not None:
        e.contact_address = contact_address.strip()[:300]
    if ref_no is not None:
        e.ref_no = ref_no.strip()[:100]
    if attached_docs is not None:
        e.attached_docs = max(0, int(attached_docs))
    e.updated_at = _now()
    after = snapshot(e, AUDIT_FIELDS)
    changed = {k for k in after if after[k] != before[k]}
    if changed:
        write_audit(
            db,
            ctx,
            action="entry.update_info",
            entity_type="entry",
            entity_id=e.id,
            summary=f"Sửa thông tin {e.display_number}",
            before={k: before[k] for k in changed},
            after={k: after[k] for k in changed},
        )
    db.commit()
    return e


def delete_draft(db: Session, ctx, e: JournalEntry) -> None:
    if e.company_id != ctx.company_id:
        raise NotFoundError("Không tìm thấy chứng từ")
    if e.status != DRAFT:
        raise BusinessError(
            "Chỉ được xóa chứng từ nháp. "
            "Chứng từ đã ghi sổ phải hủy hoặc điều chỉnh."
        )
    write_audit(
        db,
        ctx,
        action="entry.delete",
        entity_type="entry",
        entity_id=e.id,
        summary=f"Xóa {doc_label(e.doc_type).lower()} nháp #{e.id}",
        before=_snap(e),
    )
    emit("entry.deleted", db, ctx, e)
    for a in list(e.attachments):
        _delete_attachment_file(a)
    db.delete(e)
    db.commit()


# ------------------------------------------------------------ numbering

_NUMBER_TOKEN = r"\{(?:prefix|year|yy|month|seq(?::0?(?:[1-9]|10)d)?)\}"
_NUMBER_FORMAT_RE = re.compile(rf"(?:{_NUMBER_TOKEN}|[A-Za-z0-9 ./_-])+")
_PREFIX_RE = re.compile(r"[A-Z0-9]{1,8}")
MAX_NUMBER_LEN = 40


def validate_number_format(fmt: str | None) -> str | None:
    """Is the voucher number format valid? Return an error or None.

    Only the tokens {prefix} {year} {yy} {month} {seq} / {seq:0Nd}
    (N ≤ 10) and plain characters are allowed – this blocks arbitrary
    Python format strings (e.g. {seq:>50000000} producing a huge
    string, or {seq.__class__}).
    """
    fmt = fmt or ""
    if not fmt or len(fmt) > 60 or not _NUMBER_FORMAT_RE.fullmatch(fmt):
        return (
            "Định dạng số chứng từ chỉ gồm {prefix}, {year}, {yy}, "
            "{month}, {seq} hoặc {seq:04d} (tối đa 10 chữ số) "
            "và chữ, số, dấu . / _ -"
        )
    if len(re.findall(r"\{seq(?::[^}]*)?\}", fmt)) != 1:
        return (
            "Định dạng số chứng từ phải có đúng một {seq}, "
            "ví dụ {prefix}{year}-{seq:04d}"
        )
    literal = re.sub(_NUMBER_TOKEN, "", fmt)
    if len(literal) > 20:
        return (
            "Phần chữ cố định trong định dạng số chứng từ quá dài "
            "(tối đa 20 ký tự)"
        )
    return None


def safe_prefix(value: str | None, fallback: str) -> str:
    v = (value or "").strip().upper()
    return v if _PREFIX_RE.fullmatch(v) else fallback


def format_number(fmt: str, prefix: str, d: date, seq: int | None) -> str:
    # Defensive: invalid format/prefix (legacy data, manual DB edits)
    # → fall back to the default.
    if validate_number_format(fmt):
        fmt = DEFAULT_NUMBER_FORMAT
    prefix = safe_prefix(prefix, "CT")
    values = {
        "prefix": prefix,
        "year": d.year,
        "yy": f"{d.year % 100:02d}",
        "month": f"{d.month:02d}",
    }
    if seq is None:
        # Placeholder shown before a number is assigned: PT2026-____
        out = fmt
        for k, v in values.items():
            out = out.replace("{" + k + "}", str(v))
        return re.sub(r"\{seq[^}]*\}", "____", out)
    out = fmt.format(seq=seq, **values)
    if len(out) > MAX_NUMBER_LEN:
        out = DEFAULT_NUMBER_FORMAT.format(seq=seq, **values)
    return out


def doc_prefix(company: Company, doc_type: str) -> str:
    return safe_prefix(
        (company.setting("prefixes") or {}).get(doc_type), doc_type
    )


def next_number(
    db: Session, company: Company, doc_type: str, d: date, *, is_taken=None
) -> tuple[str, int]:
    """Assign the next voucher number (call inside company_tx).

    `is_taken(number) -> bool`: extra duplicate check against another
    table (e.g. inventory vouchers that are not journal entries).
    """
    fmt = company.number_format or DEFAULT_NUMBER_FORMAT
    yearly = "{year" in fmt or "{yy" in fmt
    year = d.year if yearly else 0
    seq_row = db.scalar(
        select(NumberSequence).where(
            NumberSequence.company_id == company.id,
            NumberSequence.series == doc_type,
            NumberSequence.year == year,
        )
    )
    if seq_row is None:
        seq_row = NumberSequence(
            company_id=company.id, series=doc_type, year=year, last_seq=0
        )
        db.add(seq_row)
    prefix = doc_prefix(company, doc_type)
    value = seq_row.last_seq
    while True:
        value += 1
        number = format_number(fmt, prefix, d, value)
        taken = db.scalar(
            select(JournalEntry.id).where(
                JournalEntry.company_id == company.id,
                JournalEntry.number == number,
            )
        )
        if taken is None and not (is_taken and is_taken(number)):
            break
    seq_row.last_seq = value
    return number, value


def _next_posting_seq(db: Session, company_id: int) -> int:
    return (
        int(
            db.scalar(
                select(
                    func.coalesce(func.max(JournalEntry.posting_seq), 0)
                ).where(JournalEntry.company_id == company_id)
            )
            or 0
        )
        + 1
    )


# ----------------------------------------------- posting / cancellation


def _negative_message(
    action: str,
    account: str,
    bank: BankAccount | None,
    p: queries.NegativePoint,
) -> str:
    book = f"sổ TK {account}" + (f" ({bank.label})" if bank else "")
    where = f"ngày {fmt_date(p.date)}, dòng {p.line_no} của {book}"
    if p.number:
        where += f" (chứng từ {p.number})"
    what = "tồn quỹ" if account.startswith("111") else "số dư"
    return (
        f"Không thể {action}: {what} sẽ bị âm tại {where}, "
        f"còn {format_vnd(p.balance)} đồng."
    )


def check_no_negative(
    db: Session,
    company_id: int,
    entries: list[JournalEntry],
    action: str,
    only_accounts: set[str] | None = None,
) -> None:
    """Check that no-negative accounts stay ≥ 0 after a flush.

    Balances are checked from the earliest date of the given vouchers.
    only_accounts: check only these accounts (e.g. a historical data
    import when bank balances are not yet available).
    """
    keys = queries.entry_cash_keys(db, company_id, entries)
    if only_accounts is not None:
        keys = {k for k in keys if k[0] in only_accounts}
    if not keys:
        return
    from_date = min(e.date for e in entries)
    for account, bank_id in sorted(keys, key=lambda k: (k[0], k[1] or 0)):
        p = queries.first_negative(
            db,
            company_id,
            account,
            bank_account_id=bank_id,
            from_date=from_date,
        )
        if p is not None:
            bank = db.get(BankAccount, bank_id) if bank_id else None
            raise NegativeBalanceError(
                _negative_message(action, account, bank, p),
                date=p.date,
                line_no=p.line_no,
                balance=p.balance,
                voucher_number=p.number,
            )


def _run_validators(db: Session, ctx, e: JournalEntry, action: str) -> None:
    for func_ in registry.posting_validators:
        func_(db, ctx, e, action)


def post_entry(
    db: Session, ctx, e: JournalEntry, *, action_label: str | None = None
) -> JournalEntry:
    """Post a draft voucher.

    Runs full validation, the open-period check and the retroactive
    negative-balance check; assigns the voucher number and posting
    sequence.
    """
    if e.company_id != ctx.company_id:
        raise NotFoundError("Không tìm thấy chứng từ")
    company = get_company(db, ctx.company_id)
    label = action_label or f"ghi sổ {doc_label(e.doc_type).lower()}"
    with company_tx(db, company.id):
        db.refresh(e)
        if e.status != DRAFT:
            raise BusinessError("Chứng từ này không ở trạng thái nháp")
        validate(db, company, _entry_input_from(e), for_post=True)
        locks.assert_open(db, company.id, e.date, "ghi sổ")
        if not e.number:
            e.number, e.number_seq = next_number(
                db, company, e.doc_type, e.date
            )
        e.posting_seq = _next_posting_seq(db, company.id)
        e.status = POSTED
        e.posted_by = ctx.user_id
        e.posted_at = _now()
        db.flush()
        check_no_negative(db, company.id, [e], label)
        _run_validators(db, ctx, e, "post")
        write_audit(
            db,
            ctx,
            action="entry.post",
            entity_type="entry",
            entity_id=e.id,
            summary=(
                f"Ghi sổ {doc_label(e.doc_type).lower()} {e.number} "
                f"ngày {fmt_date(e.date)}: {format_vnd(e.total)}"
            ),
            after=_snap(e),
        )
        emit("entry.posted", db, ctx, e)
    return e


def post_batch(
    db: Session,
    ctx,
    entries: list[JournalEntry],
    *,
    negative_accounts: set[str] | None = None,
    action_label: str = "nhập dữ liệu",
) -> list[JournalEntry]:
    """Post many draft vouchers at once (historical data import).

    MUST be called inside `company_tx`.

    Keeps existing voucher numbers (original numbers) and assigns
    numbers to vouchers without one; the posting order follows
    (date, order passed in). Validation is as thorough as
    `post_entry`, except that the retroactive negative-balance check
    can be limited to `negative_accounts`. On error it raises and the
    outer transaction rolls everything back.
    """
    if not entries:
        return []
    company = get_company(db, ctx.company_id)
    seq = _next_posting_seq(db, company.id) - 1
    ordered = sorted(enumerate(entries), key=lambda x: (x[1].date, x[0]))
    for _i, e in ordered:
        if e.company_id != company.id:
            raise NotFoundError("Không tìm thấy chứng từ")
        if e.status != DRAFT:
            raise BusinessError(
                f"Chứng từ {e.display_number} không ở trạng thái nháp"
            )
        validate(db, company, _entry_input_from(e), for_post=True)
        locks.assert_open(db, company.id, e.date, "ghi sổ")
        if not e.number:
            e.number, e.number_seq = next_number(
                db, company, e.doc_type, e.date
            )
        seq += 1
        e.posting_seq = seq
        e.status = POSTED
        e.posted_by = ctx.user_id
        e.posted_at = _now()
    db.flush()
    check_no_negative(
        db, company.id, entries, action_label, only_accounts=negative_accounts
    )
    for _i, e in ordered:
        _run_validators(db, ctx, e, "post")
        emit("entry.posted", db, ctx, e)
    return entries


def cancel_entry(
    db: Session, ctx, e: JournalEntry, reason: str
) -> JournalEntry:
    """Cancel a posted voucher.

    The voucher stays in the DB; a reason is required, the period must
    be open and no balance may go negative.
    """
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("Hủy chứng từ bắt buộc phải nhập lý do")
    if e.company_id != ctx.company_id:
        raise NotFoundError("Không tìm thấy chứng từ")
    with company_tx(db, ctx.company_id):
        db.refresh(e)
        if e.status != POSTED:
            raise BusinessError(
                "Chỉ hủy được chứng từ đã ghi sổ (chứng từ nháp thì xóa)"
            )
        locks.assert_open(db, ctx.company_id, e.date, "hủy chứng từ")
        before = _snap(e)
        e.status = CANCELLED
        e.cancel_reason = reason
        e.cancelled_by = ctx.user_id
        e.cancelled_at = _now()
        db.flush()
        check_no_negative(
            db,
            ctx.company_id,
            [e],
            f"hủy {doc_label(e.doc_type).lower()} {e.number}",
        )
        _run_validators(db, ctx, e, "cancel")
        write_audit(
            db,
            ctx,
            action="entry.cancel",
            entity_type="entry",
            entity_id=e.id,
            summary=f"Hủy {doc_label(e.doc_type).lower()} {e.number}",
            before=before,
            after=_snap(e),
            reason=reason,
        )
        emit("entry.cancelled", db, ctx, e)
    return e


def reverse_entry(
    db: Session, ctx, e: JournalEntry, on_date: date, reason: str = ""
) -> JournalEntry:
    """Create a (draft) adjusting voucher reversing every line.

    Used when the period of the original voucher is already locked.
    """
    if e.status != POSTED:
        raise BusinessError("Chỉ điều chỉnh được chứng từ đã ghi sổ")
    lines = [
        LineIn(
            debit_account=ln.credit_account,
            credit_account=ln.debit_account,
            amount=ln.amount,
            description=ln.description,
            debit_partner_id=ln.credit_partner_id,
            credit_partner_id=ln.debit_partner_id,
            debit_bank_account_id=ln.credit_bank_account_id,
            credit_bank_account_id=ln.debit_bank_account_id,
            item_id=ln.item_id,
            quantity=ln.quantity,
            asset_id=ln.asset_id,
            cash_flow=ln.cash_flow,
        )
        for ln in e.lines
    ]
    desc = f"Điều chỉnh {e.number} ngày {fmt_date(e.date)}" + (
        f": {reason.strip()}" if reason.strip() else ""
    )
    inp = EntryIn(
        doc_type=e.doc_type,
        date=on_date,
        doc_date=on_date,
        lines=lines,
        description=desc,
        partner_id=e.partner_id,
        contact_name=e.contact_name,
        contact_address=e.contact_address,
        source=e.source,
        reverses_id=e.id,
        meta={"reverses": e.number},
    )
    return create_entry(db, ctx, inp)


# ---------------------------------------------------------- attachments

ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "application/xml": ".xml",
}


def attachment_path(a: Attachment) -> Path:
    return get_settings().attachments_dir / str(a.company_id) / a.stored_name


def _delete_attachment_file(a: Attachment) -> None:
    try:
        attachment_path(a).unlink(missing_ok=True)
    except OSError:
        pass


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:200].lstrip(b"\xef\xbb\xbf").lstrip()
    if (
        head.startswith(b"<?xml")
        or head.startswith(b"<HDon")
        or head.startswith(b"<TDiep")
    ):
        return "application/xml"
    return None


def add_attachment(
    db: Session, ctx, e: JournalEntry, filename: str, data: bytes
) -> Attachment:
    if e.status == CANCELLED:
        raise BusinessError("Chứng từ đã hủy, không thêm file được")
    settings = get_settings()
    if not data:
        raise BusinessError("File rỗng")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise BusinessError(f"File vượt quá {settings.max_upload_mb} MB")
    ctype = _sniff(data)
    if ctype not in ALLOWED_TYPES:
        raise BusinessError(
            "Chỉ nhận ảnh (JPG, PNG, WEBP), PDF hoặc XML hóa đơn"
        )
    stored = secrets.token_hex(16) + ALLOWED_TYPES[ctype]
    a = Attachment(
        company_id=e.company_id,
        entry_id=e.id,
        filename=Path(filename or "file").name[:200],
        stored_name=stored,
        content_type=ctype,
        size=len(data),
        uploaded_by=ctx.user_id,
        uploaded_at=_now(),
    )
    path = attachment_path(a)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    db.add(a)
    db.flush()
    write_audit(
        db,
        ctx,
        action="attachment.add",
        entity_type="entry",
        entity_id=e.id,
        summary=f"Đính kèm '{a.filename}' vào {e.display_number}",
        after={"attachment_id": a.id, "filename": a.filename},
    )
    db.commit()
    return a


def delete_attachment(db: Session, ctx, a: Attachment) -> None:
    e = a.entry
    if e.company_id != ctx.company_id:
        raise NotFoundError()
    if e.status != DRAFT:
        raise BusinessError("Chỉ xóa được file đính kèm của chứng từ nháp")
    write_audit(
        db,
        ctx,
        action="attachment.delete",
        entity_type="entry",
        entity_id=e.id,
        summary=f"Xóa file '{a.filename}' khỏi {e.display_number}",
        before={"filename": a.filename},
    )
    _delete_attachment_file(a)
    db.delete(a)
    db.commit()


def get_attachment(db: Session, ctx, attachment_id: int) -> Attachment:
    a = db.get(Attachment, attachment_id)
    if a is None or a.company_id != ctx.company_id:
        raise NotFoundError("Không tìm thấy file")
    return a
