"""Dashboard figures.

Cash, receivables/payables, year-to-date profit and loss, 12-month
cash flow, to-do items and tax filing deadlines.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import extract, func, not_, or_, select

from app.core.dates import iter_months, month_bounds, now_local, today
from app.features.accounts.models import Account
from app.features.journal import locks, queries
from app.features.journal.models import DRAFT, JournalEntry
from app.features.org.models import Company

log = logging.getLogger(__name__)

REVENUE = ("511", "515", "711")
EXPENSE = ("632", "635", "641", "642", "811", "821")
MONEY = ("111", "112")


def _greeting(name: str) -> str:
    h = now_local().hour
    part = (
        "Chào buổi sáng"
        if h < 11
        else "Chào buổi trưa"
        if h < 13
        else "Chào buổi chiều"
        if h < 18
        else "Chào buổi tối"
    )
    return f"{part}, {name}" if name else part


def _months_back(d: date, n: int) -> list[tuple[int, int]]:
    y, m = d.year, d.month
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _pnl_by_account(db, cid: int, start: date, end: date) -> dict[str, int]:
    """Return the net movement per account, excluding closing entries.

    Credit minus debit for revenue accounts, debit minus credit for
    expense accounts.
    """
    lr = queries.ledger_source(db, cid, include_opening=False)
    rows = db.execute(
        select(lr.c.account, func.sum(lr.c.debit), func.sum(lr.c.credit))
        .where(
            lr.c.date >= start,
            lr.c.date <= end,
            lr.c.doc_type != "KC",
            or_(*[lr.c.account.like(f"{a}%") for a in REVENUE + EXPENSE]),
        )
        .group_by(lr.c.account)
    ).all()
    out = {}
    for acc, d, c in rows:
        d, c = int(d or 0), int(c or 0)
        out[acc] = (c - d) if acc.startswith(REVENUE) else (d - c)
    return out


def _cash_flow(db, cid: int, months: list[tuple[int, int]]):
    """Return monthly cash inflows and outflows.

    Internal transfers between 111 and 112 are excluded.
    """
    if not months:
        return [], []
    start = date(months[0][0], months[0][1], 1)
    end = month_bounds(*months[-1])[1]
    lr = queries.ledger_source(db, cid, include_opening=False)
    is_money = or_(*[lr.c.account.like(f"{a}%") for a in MONEY])
    contra_money = or_(*[lr.c.contra.like(f"{a}%") for a in MONEY])
    y, m = extract("year", lr.c.date), extract("month", lr.c.date)
    rows = db.execute(
        select(y, m, func.sum(lr.c.debit), func.sum(lr.c.credit))
        .where(
            is_money, not_(contra_money), lr.c.date >= start, lr.c.date <= end
        )
        .group_by(y, m)
    ).all()
    data = {
        (int(a), int(b)): (int(d or 0), int(c or 0)) for a, b, d, c in rows
    }
    return [data.get(k, (0, 0))[0] for k in months], [
        data.get(k, (0, 0))[1] for k in months
    ]


def _partner_side_total(
    db, cid: int, account: str, d: date, positive: bool
) -> int:
    rows = queries.account_sums(
        db, cid, date(1900, 1, 1), d, accounts=[account], by_partner=True
    )
    total = 0
    for r in rows:
        bal = r.closing
        if positive and bal > 0:
            total += bal
        elif not positive and bal < 0:
            total += -bal
    return total


def _tasks(db, user, company: Company) -> list[dict]:
    cid = company.id
    tasks = []
    drafts = (
        db.scalar(
            select(func.count(JournalEntry.id)).where(
                JournalEntry.company_id == cid, JournalEntry.status == DRAFT
            )
        )
        or 0
    )
    if drafts and (user.can("journal.view") or user.can("cash.view")):
        tasks.append(
            {
                "label": "Chứng từ nháp chờ ghi sổ",
                "count": drafts,
                "href": "/entries?status=draft",
                "icon": "square-pen",
                "tone": "amber",
            }
        )
    if user.can("locks.manage") or user.can("journal.view"):
        locked = locks.locked_set(db, cid)
        cutoff = today() - timedelta(days=30)
        pending = sum(
            1
            for y, m in iter_months(company.start_date, today())
            if month_bounds(y, m)[1] <= cutoff
            and not locks.month_locked(locked, y, m)
        )
        if pending:
            tasks.append(
                {
                    "label": "Tháng đã qua chưa khóa sổ",
                    "count": pending,
                    "href": "/locks",
                    "icon": "lock",
                    "tone": "blue",
                }
            )
    if user.can("bank.view"):
        try:
            from app.features.bank.statement_models import BankStatementLine

            n = (
                db.scalar(
                    select(func.count(BankStatementLine.id)).where(
                        BankStatementLine.company_id == cid,
                        BankStatementLine.status == "unmatched",
                    )
                )
                or 0
            )
            if n:
                tasks.append(
                    {
                        "label": "Dòng sao kê chưa đối chiếu",
                        "count": n,
                        "href": "/bank/statements",
                        "icon": "landmark",
                        "tone": "amber",
                    }
                )
        except Exception:  # bank module not installed
            log.debug("skipping bank statement count", exc_info=True)
    if user.can("inventory.costing"):
        try:
            from app.features.inventory.costing import months_status

            today_ym = (today().year, today().month)
            n = sum(
                1
                for m in months_status(db, company)
                if m.needs_run and (m.year, m.month) < today_ym
            )
            if n:
                tasks.append(
                    {
                        "label": "Tháng cần tính giá xuất kho",
                        "count": n,
                        "href": "/inventory/costing",
                        "icon": "calculator",
                        "tone": "amber",
                    }
                )
        except Exception:
            log.debug("skipping inventory costing check", exc_info=True)
    if user.can("checks.view"):
        tasks.append(
            {
                "label": "Kiểm tra dữ liệu trước khi khóa sổ",
                "count": None,
                "href": "/checks",
                "icon": "shield-check",
                "tone": None,
            }
        )
    return tasks


def _deadlines(db, company: Company) -> list[dict]:
    try:
        from app.features.vat.calendar import deadlines
    except Exception:
        return []
    try:
        out = []
        for d in deadlines(company, today(), db)[:5]:
            due = d["due"] if isinstance(d, dict) else d.due
            title = d["title"] if isinstance(d, dict) else d.title
            url = (
                d.get("url")
                if isinstance(d, dict)
                else getattr(d, "url", None)
            ) or "/vat/calendar"
            status = (
                d.get("status")
                if isinstance(d, dict)
                else getattr(d, "status", None)
            ) or ""
            out.append(
                {
                    "label": title,
                    "due": due,
                    "days": (due - today()).days,
                    "url": url,
                    "status": status,
                }
            )
        return out
    except Exception:
        log.exception("tax filing calendar failed")
        return []


def build(db, user) -> dict:
    cid = user.company_id
    company = db.get(Company, cid)
    d = today()
    can_books = user.can("journal.view")
    can_cash = user.can("cash.view") or can_books
    ctx: dict = {
        "greeting": _greeting(
            (user.full_name or "").split()[-1] if user.full_name else ""
        ),
        "today": d,
        "can_books": can_books,
        "can_cash": can_cash,
        "breadcrumbs": [],
    }

    months = [
        ym
        for ym in _months_back(d, 12)
        if month_bounds(*ym)[1] >= company.start_date
    ] or [(d.year, d.month)]
    ctx["month_labels"] = [f"T{m}/{str(y)[2:]}" for y, m in months]

    if can_cash:
        cash = queries.balance(db, cid, "111", upto=d)
        prev_end = date(d.year, d.month, 1) - timedelta(days=1)
        ctx["cash"] = cash
        if can_books:
            bank = queries.balance(db, cid, "112", upto=d)
            money = cash + bank
            money_prev = queries.balance(db, cid, list(MONEY), upto=prev_end)
            ctx["bank"] = bank
            ctx["money"] = money
            ctx["money_delta"] = (
                round((money - money_prev) / abs(money_prev) * 100, 1)
                if money_prev
                else None
            )
            inflow, outflow = _cash_flow(db, cid, months)
            ctx["inflow"], ctx["outflow"] = inflow, outflow
            # cash and bank balance at the end of each month
            balances = []
            for y, m in months:
                balances.append(
                    queries.balance(
                        db,
                        cid,
                        list(MONEY),
                        upto=min(month_bounds(y, m)[1], d),
                    )
                )
            ctx["money_balances"] = balances

    if can_books:
        ctx["receivable"] = _partner_side_total(db, cid, "131", d, True)
        ctx["payable"] = _partner_side_total(db, cid, "331", d, False)
        year_start = max(date(d.year, 1, 1), company.start_date)
        pnl = _pnl_by_account(db, cid, year_start, d)
        revenue = sum(v for k, v in pnl.items() if k.startswith(REVENUE))
        expense = sum(
            v
            for k, v in pnl.items()
            if k.startswith(EXPENSE) and not k.startswith("821")
        )
        ctx["revenue_ytd"] = revenue
        ctx["profit_ytd"] = revenue - expense
        names = dict(
            db.execute(
                select(Account.code, Account.name).where(
                    Account.company_id == cid
                )
            ).all()
        )
        exp_items = sorted(
            (
                (names.get(k, k), v)
                for k, v in pnl.items()
                if k.startswith(EXPENSE) and v > 0
            ),
            key=lambda x: -x[1],
        )[:6]
        ctx["expense_items"] = [
            {"label": f"{label}", "value": v} for label, v in exp_items
        ]
        ctx["revenue_mix"] = [
            {"label": names.get(k, k), "value": v, "slot": i + 1}
            for i, (k, v) in enumerate(
                sorted(
                    (
                        (k, v)
                        for k, v in pnl.items()
                        if k.startswith(REVENUE) and v > 0
                    ),
                    key=lambda x: -x[1],
                )[:6]
            )
        ]

    ctx["tasks"] = _tasks(db, user, company)
    ctx["deadlines"] = (
        _deadlines(db, company) if (user.can("vat.view") or can_books) else []
    )
    stmt = select(JournalEntry).where(JournalEntry.company_id == cid)
    if not can_books:
        stmt = stmt.where(JournalEntry.doc_type.in_(("PT", "PC")))
    ctx["recent"] = list(
        db.scalars(stmt.order_by(JournalEntry.id.desc()).limit(8))
    )
    return ctx
