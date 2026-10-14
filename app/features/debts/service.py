"""Receivables (TK 131) and payables (TK 331).

Summary, per-partner detail ledger, FIFO aging and reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.journal import queries
from app.features.journal.models import JournalEntry
from app.features.partners.models import Partner

KINDS = {
    "receivable": {
        "account": "131",
        "label": "Phải thu khách hàng",
        "partner": "Khách hàng",
        "role": "customer",
    },
    "payable": {
        "account": "331",
        "label": "Phải trả người bán",
        "partner": "Nhà cung cấp",
        "role": "supplier",
    },
}
BUCKETS = [
    ("current", "Chưa đến hạn"),
    ("d30", "1–30 ngày"),
    ("d60", "31–60 ngày"),
    ("d90", "61–90 ngày"),
    ("over90", "Trên 90 ngày"),
]


def sign_of(kind: str) -> int:
    """Positive direction of a balance: receivable = debit balance,
    payable = credit balance.
    """
    return 1 if kind == "receivable" else -1


def split(net: int) -> tuple[int, int]:
    """Net (debit - credit) -> (debit balance, credit balance)."""
    return (net, 0) if net >= 0 else (0, -net)


@dataclass
class SummaryRow:
    partner: Partner | None
    partner_id: int | None
    opening: int
    debit: int
    credit: int

    @property
    def closing(self) -> int:
        return self.opening + self.debit - self.credit

    @property
    def opening_dc(self) -> tuple[int, int]:
        return split(self.opening)

    @property
    def closing_dc(self) -> tuple[int, int]:
        return split(self.closing)


@dataclass
class Summary:
    kind: str
    start: date
    end: date
    rows: list[SummaryRow] = field(default_factory=list)

    def total(self, attr: str) -> int:
        return sum(getattr(r, attr) for r in self.rows)

    @property
    def totals(self) -> dict:
        od = sum(r.opening_dc[0] for r in self.rows)
        oc = sum(r.opening_dc[1] for r in self.rows)
        cd = sum(r.closing_dc[0] for r in self.rows)
        cc = sum(r.closing_dc[1] for r in self.rows)
        return {
            "opening_debit": od,
            "opening_credit": oc,
            "debit": self.total("debit"),
            "credit": self.total("credit"),
            "closing_debit": cd,
            "closing_credit": cc,
        }


def summary(
    db: Session,
    company_id: int,
    kind: str,
    start: date,
    end: date,
    *,
    q: str = "",
) -> Summary:
    account = KINDS[kind]["account"]
    sums = queries.account_sums(
        db, company_id, start, end, accounts=[account], by_partner=True
    )
    agg: dict = {}
    for s in sums:
        r = agg.setdefault(s.partner_id, [0, 0, 0])
        r[0] += s.opening
        r[1] += s.debit
        r[2] += s.credit
    ids = [pid for pid in agg if pid]
    partners = (
        {
            p.id: p
            for p in db.scalars(
                select(Partner).where(
                    Partner.company_id == company_id, Partner.id.in_(ids)
                )
            )
        }
        if ids
        else {}
    )
    out = Summary(kind=kind, start=start, end=end)
    ql = q.strip().lower()
    for pid, (o, d, c) in agg.items():
        p = partners.get(pid)
        if ql and (
            p is None
            or (
                ql not in p.name.lower()
                and ql not in (p.code or "").lower()
                and not (p.tax_code or "").startswith(ql)
            )
        ):
            continue
        out.rows.append(
            SummaryRow(partner=p, partner_id=pid, opening=o, debit=d, credit=c)
        )
    out.rows.sort(key=lambda r: r.partner.name if r.partner else "~")
    return out


def balances_at(
    db: Session, company_id: int, kind: str, as_of: date
) -> dict[int | None, int]:
    """Net balance (debit - credit) per partner at end of `as_of`."""
    account = KINDS[kind]["account"]
    lr = queries.ledger_source(db, company_id)
    from sqlalchemy import func

    rows = db.execute(
        select(
            lr.c.partner_id,
            func.coalesce(func.sum(lr.c.debit - lr.c.credit), 0),
        )
        .where(
            queries.account_match(lr.c.account, account), lr.c.date <= as_of
        )
        .group_by(lr.c.partner_id)
    ).all()
    return {pid: int(v) for pid, v in rows if int(v)}


def partner_ledger(
    db: Session,
    company_id: int,
    kind: str,
    partner_id: int,
    start: date,
    end: date,
):
    return queries.account_ledger(
        db,
        company_id,
        KINDS[kind]["account"],
        start,
        end,
        partner_id=partner_id,
    )


# --------------------------------------------------------- aging (FIFO)


@dataclass
class OpenItem:
    date: date
    due: date
    number: str | None
    description: str
    amount: int  # outstanding (positive in the kind's direction)
    entry_id: int | None

    def days_overdue(self, as_of: date) -> int:
        return (as_of - self.due).days

    def bucket(self, as_of: date) -> str:
        d = self.days_overdue(as_of)
        if d <= 0:
            return "current"
        if d <= 30:
            return "d30"
        if d <= 60:
            return "d60"
        if d <= 90:
            return "d90"
        return "over90"


@dataclass
class AgingRow:
    partner: Partner | None
    partner_id: int | None
    # positive = outstanding in the kind's direction;
    # negative = prepaid / advance
    balance: int
    items: list[OpenItem] = field(default_factory=list)
    buckets: dict = field(default_factory=dict)

    def max_overdue(self, as_of: date) -> int:
        return max((i.days_overdue(as_of) for i in self.items), default=0)


def _due_dates(
    db: Session, company_id: int, entry_ids: set[int]
) -> dict[int, date]:
    """Payment due dates recorded on purchase/sales documents, if
    any.
    """
    out: dict[int, date] = {}
    if not entry_ids:
        return out
    from app.features.purchases.models import PurchaseDoc
    from app.features.sales.models import SalesDoc

    for model in (SalesDoc, PurchaseDoc):
        for eid, due in db.execute(
            select(model.entry_id, model.due_date).where(
                model.company_id == company_id,
                model.entry_id.in_(entry_ids),
                model.due_date.is_not(None),
            )
        ):
            out[eid] = due
    return out


def aging(
    db: Session,
    company_id: int,
    kind: str,
    as_of: date,
    *,
    partner_id: int | None = None,
) -> list[AgingRow]:
    """Aging: balance increases (debit 131 / credit 331) are settled
    oldest first (FIFO).
    """
    account = KINDS[kind]["account"]
    sg = sign_of(kind)
    lr = queries.ledger_source(db, company_id)
    stmt = (
        select(
            lr.c.partner_id,
            lr.c.date,
            lr.c.number,
            lr.c.description,
            lr.c.debit,
            lr.c.credit,
            lr.c.entry_id,
        )
        .where(
            queries.account_match(lr.c.account, account), lr.c.date <= as_of
        )
        .order_by(
            lr.c.partner_id,
            lr.c.date,
            lr.c.posting_seq,
            lr.c.line_no,
            lr.c.side,
        )
    )
    if partner_id is not None:
        stmt = stmt.where(lr.c.partner_id == partner_id)
    rows = db.execute(stmt).all()
    terms = {
        p.id: p.payment_terms_days
        for p in db.scalars(
            select(Partner).where(Partner.company_id == company_id)
        )
    }
    dues = _due_dates(db, company_id, {r.entry_id for r in rows if r.entry_id})
    by_partner: dict = {}
    for r in rows:
        by_partner.setdefault(r.partner_id, []).append(r)
    out = []
    partner_objs = {
        p.id: p
        for p in db.scalars(
            select(Partner).where(
                Partner.company_id == company_id,
                Partner.id.in_([k for k in by_partner if k]),
            )
        )
    }
    for pid, lines in by_partner.items():
        increases: list[OpenItem] = []
        paid = 0
        # Merge lines of the same entry (goods + tax) into one item.
        grouped: list[list] = []
        for r in lines:
            amt = sg * (int(r.debit) - int(r.credit))
            if (
                grouped
                and r.entry_id is not None
                and grouped[-1][0].entry_id == r.entry_id
            ):
                grouped[-1][1] += amt
            else:
                grouped.append([r, amt])
        for r, amt in grouped:
            if amt > 0:
                due = dues.get(r.entry_id) or (
                    r.date + timedelta(days=terms.get(pid, 0) or 0)
                )
                increases.append(
                    OpenItem(
                        r.date,
                        due,
                        r.number,
                        r.description or "",
                        amt,
                        r.entry_id,
                    )
                )
            else:
                paid += -amt
        # FIFO
        open_items = []
        for it in increases:
            if paid >= it.amount:
                paid -= it.amount
                continue
            it.amount -= paid
            paid = 0
            open_items.append(it)
        balance = sum(i.amount for i in open_items) - paid
        if balance == 0 and not open_items:
            continue
        row = AgingRow(
            partner=partner_objs.get(pid),
            partner_id=pid,
            balance=balance,
            items=open_items,
        )
        row.buckets = {k: 0 for k, _ in BUCKETS}
        for it in open_items:
            row.buckets[it.bucket(as_of)] += it.amount
        row.buckets["prepaid"] = paid
        out.append(row)
    out.sort(key=lambda r: -r.balance)
    return out


def aging_totals(rows: list[AgingRow]) -> dict:
    tot = {k: 0 for k, _ in BUCKETS}
    tot["prepaid"] = 0
    tot["balance"] = 0
    for r in rows:
        for k in tot:
            if k == "balance":
                tot[k] += r.balance
            else:
                tot[k] += r.buckets.get(k, 0)
    return tot


def entry_partner_name(db: Session, entry_id: int | None) -> str:
    if not entry_id:
        return ""
    e = db.get(JournalEntry, entry_id)
    return e.contact_name if e else ""
