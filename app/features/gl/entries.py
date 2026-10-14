"""Shared voucher helpers: list, PK form and detail-page data.

Provides the filtered voucher list, the general journal voucher (PK)
form and reusable data for the voucher detail page.

Other modules that want the partials `gl/_entry_lines.html`,
`gl/_attachments.html`, `gl/_entry_actions.html` and `gl/_history.html`
pass `**entry_context(db, ctx, entry)` to the template and then
`{% include %}` them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import AuditLog
from app.core.dates import parse_date
from app.core.db import ulower
from app.core.money import MoneyError, parse_vnd
from app.core.registry import can_entry, doc_label, registry
from app.features.accounts.models import Account
from app.features.bank.models import BankAccount
from app.features.journal import locks
from app.features.journal import service as js
from app.features.journal.models import JournalEntry, JournalLine
from app.features.partners.models import Partner

PAGE = 50


# ------------------------------------------------------------ list


@dataclass
class EntryFilters:
    q: str = ""
    doc_type: str = ""
    status: str = ""
    start: date | None = None
    end: date | None = None
    account: str = ""
    partner_id: int | None = None
    page: int = 1

    @classmethod
    def from_params(cls, p) -> EntryFilters:
        def _date(v):
            try:
                return parse_date(v) if v else None
            except ValueError:
                return None

        def _int(v):
            try:
                return int(v) if v not in (None, "") else None
            except ValueError:
                return None

        return cls(
            q=(p.get("q") or "").strip()[:100],
            doc_type=(p.get("doc_type") or "").strip()[:8],
            status=p.get("status")
            if p.get("status") in ("draft", "posted", "cancelled")
            else "",
            start=_date(p.get("start")),
            end=_date(p.get("end")),
            account=(p.get("account") or "").strip()[:20],
            partner_id=_int(p.get("partner_id")),
            page=max(1, _int(p.get("page")) or 1),
        )

    def query(self, **override) -> str:
        from urllib.parse import urlencode

        d = {
            "q": self.q,
            "doc_type": self.doc_type,
            "status": self.status,
            "start": self.start.isoformat() if self.start else "",
            "end": self.end.isoformat() if self.end else "",
            "account": self.account,
            "partner_id": self.partner_id or "",
            "page": self.page,
        }
        d.update(override)
        return urlencode({k: v for k, v in d.items() if v not in ("", None)})


def _filtered(company_id: int, f: EntryFilters):
    je, jl = JournalEntry, JournalLine
    conds = [je.company_id == company_id]
    if f.doc_type:
        conds.append(je.doc_type == f.doc_type)
    if f.status:
        conds.append(je.status == f.status)
    if f.start:
        conds.append(je.date >= f.start)
    if f.end:
        conds.append(je.date <= f.end)
    if f.q:
        like = f"%{f.q.lower()}%"
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
            exists().where(
                jl.entry_id == je.id,
                or_(
                    jl.debit_account.like(f"{f.account}%"),
                    jl.credit_account.like(f"{f.account}%"),
                ),
            )
        )
    if f.partner_id:
        conds.append(
            or_(
                je.partner_id == f.partner_id,
                exists().where(
                    jl.entry_id == je.id,
                    or_(
                        jl.debit_partner_id == f.partner_id,
                        jl.credit_partner_id == f.partner_id,
                    ),
                ),
            )
        )
    return and_(*conds)


def list_entries(
    db: Session, company_id: int, f: EntryFilters
) -> tuple[list[JournalEntry], int, int]:
    cond = _filtered(company_id, f)
    total = db.scalar(select(func.count(JournalEntry.id)).where(cond)) or 0
    amount = (
        db.scalar(
            select(func.coalesce(func.sum(JournalEntry.total), 0)).where(
                cond, JournalEntry.status == "posted"
            )
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(JournalEntry)
            .where(cond)
            .order_by(JournalEntry.date.desc(), JournalEntry.id.desc())
            .limit(PAGE)
            .offset((f.page - 1) * PAGE)
        )
    )
    return rows, int(total), int(amount)


def status_counts(
    db: Session, company_id: int, f: EntryFilters
) -> dict[str, int]:
    base = EntryFilters(**{**f.__dict__, "status": "", "page": 1})
    rows = db.execute(
        select(JournalEntry.status, func.count(JournalEntry.id))
        .where(_filtered(company_id, base))
        .group_by(JournalEntry.status)
    ).all()
    out = {s: int(n) for s, n in rows}
    out["all"] = sum(out.values())
    return out


def doc_type_options() -> list[tuple[str, str]]:
    return [
        (d.code, d.label)
        for d in sorted(registry.doc_types.values(), key=lambda d: d.code)
    ]


# ------------------------------------------------------- display names


@dataclass
class Names:
    accounts: dict[str, str]
    partners: dict[int, Partner]
    banks: dict[int, BankAccount]

    def account(self, code: str | None) -> str:
        return self.accounts.get(code or "", "")

    def partner(self, pid: int | None) -> str:
        p = self.partners.get(pid) if pid else None
        return p.name if p else ""

    def partner_label(self, pid: int | None) -> str:
        p = self.partners.get(pid) if pid else None
        return p.label if p else ""

    def bank(self, bid: int | None) -> str:
        b = self.banks.get(bid) if bid else None
        return b.label if b else ""


def names_for(
    db: Session, company_id: int, entries: list[JournalEntry]
) -> Names:
    pids, bids = set(), set()
    for e in entries:
        if e.partner_id:
            pids.add(e.partner_id)
        for ln in e.lines:
            pids.update(
                x for x in (ln.debit_partner_id, ln.credit_partner_id) if x
            )
            bids.update(
                x
                for x in (ln.debit_bank_account_id, ln.credit_bank_account_id)
                if x
            )
        for v in e.vat_records:
            if v.partner_id:
                pids.add(v.partner_id)
    accounts = dict(
        db.execute(
            select(Account.code, Account.name).where(
                Account.company_id == company_id
            )
        ).all()
    )
    partners = (
        {
            p.id: p
            for p in db.scalars(
                select(Partner).where(
                    Partner.company_id == company_id, Partner.id.in_(pids)
                )
            )
        }
        if pids
        else {}
    )
    banks = (
        {
            b.id: b
            for b in db.scalars(
                select(BankAccount).where(
                    BankAccount.company_id == company_id,
                    BankAccount.id.in_(bids),
                )
            )
        }
        if bids
        else {}
    )
    return Names(accounts, partners, banks)


def history(db: Session, company_id: int, entry_id: int) -> list[dict]:
    from app.core.dates import fmt_datetime

    icons = {
        "entry.create": ("file-plus", "gray"),
        "entry.update": ("pencil", "gray"),
        "entry.post": ("check", "green"),
        "entry.cancel": ("ban", "red"),
        "entry.update_info": ("pencil", "gray"),
        "attachment.add": ("paperclip", "gray"),
        "attachment.delete": ("trash", "gray"),
    }
    logs = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.company_id == company_id,
            AuditLog.entity_type == "entry",
            AuditLog.entity_id == str(entry_id),
        )
        .order_by(AuditLog.id.desc())
    )
    out = []
    for log in logs:
        ic, tone = icons.get(log.action, ("history", "gray"))
        out.append(
            {
                "at": fmt_datetime(log.at),
                "actor": log.username or "",
                "action": log.summary or log.action,
                "detail": log.reason,
                "icon": ic,
                "tone": tone,
                "via": log.via,
            }
        )
    return out


def entry_context(db: Session, ctx, e: JournalEntry) -> dict:
    """Template variables for the shared voucher detail partials."""
    names = names_for(db, ctx.company_id, [e])
    user = ctx.user
    period_open = not locks.is_locked(db, ctx.company_id, e.date)
    files = []
    for a in e.attachments:
        kind = (
            "pdf"
            if a.content_type == "application/pdf"
            else "image"
            if a.content_type.startswith("image/")
            else "xml"
            if "xml" in a.content_type
            else "file"
        )
        files.append(
            {
                "name": a.filename,
                "size": f"{max(1, a.size // 1024)} KB",
                "href": f"/attachments/{a.id}",
                "kind": kind,
                "delete_action": f"/attachments/{a.id}/delete",
            }
        )
    reversed_by = list(
        db.scalars(
            select(JournalEntry).where(
                JournalEntry.company_id == ctx.company_id,
                JournalEntry.reverses_id == e.id,
            )
        )
    )
    reverses = db.get(JournalEntry, e.reverses_id) if e.reverses_id else None
    return {
        "entry": e,
        "names": names,
        "history": history(db, ctx.company_id, e.id),
        "files": files,
        "period_open": period_open,
        "reversed_by": reversed_by,
        "reverses": reverses,
        "doc_label": doc_label(e.doc_type),
        "perm": {
            "post": can_entry(user, e.doc_type, "post"),
            "cancel": can_entry(user, e.doc_type, "cancel"),
            "create": can_entry(user, e.doc_type, "create"),
            "view": can_entry(user, e.doc_type, "view"),
        },
    }


# --------------------------------------- general journal voucher form


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "", "null", "undefined") else None
    except (TypeError, ValueError):
        return None


@dataclass
class PKForm:
    inp: js.EntryIn | None
    errors: dict
    lines: list[dict]  # data to rebuild the grid (with labels) on error
    values: dict


def parse_pk_form(
    db: Session, company_id: int, form, *, doc_type: str = "PK"
) -> PKForm:
    """Parse the general journal voucher form into an EntryIn.

    The grid holds debit/credit pairs as `lines-i-*` fields.
    """
    errors: dict[str, str] = {}
    values = {
        k: (form.get(k) or "").strip()
        for k in (
            "date",
            "doc_date",
            "description",
            "ref_no",
            "attached_docs",
            "contact_name",
        )
    }
    d = dd = None
    try:
        d = parse_date(values["date"])
        if d is None:
            errors["date"] = "Nhập ngày hạch toán"
    except ValueError:
        errors["date"] = "Ngày không hợp lệ"
    try:
        dd = parse_date(values["doc_date"]) if values["doc_date"] else None
    except ValueError:
        errors["doc_date"] = "Ngày không hợp lệ"
    if not values["description"]:
        errors["description"] = "Nhập diễn giải"
    try:
        attached = int(values["attached_docs"] or 0)
        if attached < 0:
            raise ValueError
    except ValueError:
        attached = 0
        errors["attached_docs"] = "Số chứng từ gốc không hợp lệ"
    try:
        count = min(int(form.get("lines-count") or 0), 500)
    except ValueError:
        count = 0
    accs = {
        a.code: a
        for a in db.scalars(
            select(Account).where(Account.company_id == company_id)
        )
    }
    lines: list[js.LineIn] = []
    raw_lines: list[dict] = []
    line_errors: list[str] = []
    for i in range(count):
        g = lambda f: (form.get(f"lines-{i}-{f}") or "").strip()  # noqa: E731
        da, ca, amt_raw, desc = (
            g("debit_account"),
            g("credit_account"),
            g("amount"),
            g("description"),
        )
        dp, cp, bank = (
            _int(g("debit_partner_id")),
            _int(g("credit_partner_id")),
            _int(g("bank_account_id")),
        )
        raw = {
            "debit_account": da,
            "credit_account": ca,
            "amount": None,
            "description": desc,
            "debit_partner_id": dp or "",
            "credit_partner_id": cp or "",
            "bank_account_id": bank or "",
            "cash_flow": g("cash_flow"),
        }
        if not (da or ca or amt_raw):
            continue
        try:
            amount = parse_vnd(amt_raw) if amt_raw else 0
        except MoneyError:
            amount = 0
            line_errors.append(
                f"Dòng {len(raw_lines) + 1}: số tiền không hợp lệ"
            )
        raw["amount"] = amount or None
        raw_lines.append(raw)
        acc_d, acc_c = accs.get(da), accs.get(ca)
        lines.append(
            js.LineIn(
                debit_account=da,
                credit_account=ca,
                amount=amount,
                description=desc or values["description"],
                debit_partner_id=dp,
                credit_partner_id=cp,
                debit_bank_account_id=bank
                if acc_d is not None and acc_d.needs_bank
                else None,
                credit_bank_account_id=bank
                if acc_c is not None and acc_c.needs_bank
                else None,
                cash_flow=raw["cash_flow"] or None,
            )
        )
    if not lines:
        line_errors.append("Nhập ít nhất một dòng định khoản")
    if line_errors:
        errors["lines"] = "; ".join(line_errors)
    vat = None
    if form.get("has_vat"):
        try:
            goods = parse_vnd(form.get("vat_goods_amount") or 0)
            vat_amount = parse_vnd(form.get("vat_amount") or 0)
            vat = [
                js.VatIn(
                    direction=form.get("vat_direction")
                    if form.get("vat_direction") in ("in", "out")
                    else "in",
                    goods_amount=goods,
                    vat_rate=(form.get("vat_rate") or "10").strip(),
                    vat_amount=vat_amount,
                    invoice_no=(form.get("vat_invoice_no") or "").strip(),
                    invoice_series=(
                        form.get("vat_invoice_series") or ""
                    ).strip(),
                    invoice_date=parse_date(form.get("vat_invoice_date"))
                    if form.get("vat_invoice_date")
                    else d,
                    partner_name=(form.get("vat_partner_name") or "").strip(),
                    partner_tax_code=(
                        form.get("vat_partner_tax_code") or ""
                    ).strip(),
                    vat_account="1331"
                    if form.get("vat_direction") != "out"
                    else "33311",
                )
            ]
        except (MoneyError, ValueError):
            errors["vat"] = "Thông tin hóa đơn GTGT không hợp lệ"
    else:
        vat = []
    labelled = label_lines(db, company_id, raw_lines)
    if errors:
        return PKForm(None, errors, labelled, values)
    inp = js.EntryIn(
        doc_type=doc_type,
        date=d,
        doc_date=dd or d,
        description=values["description"],
        lines=lines,
        ref_no=values["ref_no"],
        attached_docs=attached,
        contact_name=values["contact_name"],
        vat=vat,
    )
    return PKForm(inp, errors, labelled, values)


def label_lines(db: Session, company_id: int, lines: list[dict]) -> list[dict]:
    """Add combobox labels to the grid lines.

    Labels cover accounts, partners and bank accounts.
    """
    codes = {
        x
        for ln in lines
        for x in (ln.get("debit_account"), ln.get("credit_account"))
        if x
    }
    pids = {
        int(x)
        for ln in lines
        for x in (ln.get("debit_partner_id"), ln.get("credit_partner_id"))
        if x
    }
    bids = {
        int(ln["bank_account_id"]) for ln in lines if ln.get("bank_account_id")
    }
    acc = (
        dict(
            db.execute(
                select(Account.code, Account.name).where(
                    Account.company_id == company_id, Account.code.in_(codes)
                )
            ).all()
        )
        if codes
        else {}
    )
    par = (
        {
            p.id: p.label
            for p in db.scalars(
                select(Partner).where(
                    Partner.company_id == company_id, Partner.id.in_(pids)
                )
            )
        }
        if pids
        else {}
    )
    ban = (
        {
            b.id: b.label
            for b in db.scalars(
                select(BankAccount).where(
                    BankAccount.company_id == company_id,
                    BankAccount.id.in_(bids),
                )
            )
        }
        if bids
        else {}
    )
    out = []
    for ln in lines:
        x = dict(ln)
        for side in ("debit", "credit"):
            code = ln.get(f"{side}_account")
            x[f"{side}_account_label"] = (
                f"{code} – {acc[code]}" if code in acc else (code or "")
            )
            pid = ln.get(f"{side}_partner_id")
            x[f"{side}_partner_id_label"] = (
                par.get(int(pid), "") if pid else ""
            )
        bid = ln.get("bank_account_id")
        x["bank_account_id_label"] = ban.get(int(bid), "") if bid else ""
        out.append(x)
    return out


def lines_from_entry(db: Session, e: JournalEntry) -> list[dict]:
    raw = [
        {
            "debit_account": ln.debit_account,
            "credit_account": ln.credit_account,
            "amount": ln.amount,
            "description": ln.description,
            "debit_partner_id": ln.debit_partner_id or "",
            "credit_partner_id": ln.credit_partner_id or "",
            "bank_account_id": ln.debit_bank_account_id
            or ln.credit_bank_account_id
            or "",
            "cash_flow": ln.cash_flow or "",
        }
        for ln in e.lines
    ]
    return label_lines(db, e.company_id, raw)


def entry_values(e: JournalEntry) -> dict:
    return {
        "date": e.date.isoformat(),
        "doc_date": (e.doc_date or e.date).isoformat(),
        "description": e.description,
        "ref_no": e.ref_no,
        "attached_docs": e.attached_docs or "",
        "contact_name": e.contact_name,
    }
