"""AI tools (OpenAI functions) of the gl module.

Trial balance, account ledger, financial statements and drafting a
general journal voucher.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.core.dates import parse_date, today
from app.core.errors import BusinessError
from app.core.registry import ai_tool
from app.features.gl import books, statements
from app.features.gl.periods import make
from app.features.journal import service as js
from app.features.org.models import Company
from app.features.partners.models import Partner


def _d(v, default: date) -> date:
    try:
        return parse_date(v) or default
    except ValueError as e:
        raise BusinessError(f"Ngày không hợp lệ: {v}") from e


def _period(start, end) -> tuple[date, date]:
    t = today()
    s = _d(start, date(t.year, 1, 1))
    e = _d(end, t)
    if e < s:
        s, e = e, s
    return s, e


def _partner_id(db, company_id: int, code: str | None) -> int | None:
    if not code:
        return None
    p = db.scalar(
        select(Partner).where(
            Partner.company_id == company_id, Partner.code == code.strip()
        )
    )
    if p is None:
        raise BusinessError(f"Không có đối tượng mã {code}")
    return p.id


DATE = {"type": "string", "description": "Date YYYY-MM-DD"}


@ai_tool(
    name="get_trial_balance",
    label="Bảng cân đối tài khoản",
    perm="reports.view",
    description=(
        "Trial balance (F01-DNN): opening, period debit/credit and closing "
        "balance of each account between start and end dates. Amounts in VND "
        "(integers)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "start": DATE,
            "end": DATE,
            "level": {
                "type": "integer",
                "enum": [1, 2],
                "description": "1 = level-1 accounts only",
            },
        },
    },
)
def get_trial_balance(
    db, ctx, start: str | None = None, end: str | None = None, level: int = 1
) -> dict:
    s, e = _period(start, end)
    tb = books.trial_balance(
        db, ctx.company_id, s, e, level=1 if level == 1 else 2
    )
    return {
        "start": s.isoformat(),
        "end": e.isoformat(),
        "balanced": tb.balanced,
        "rows": [
            {
                "account": r.code,
                "name": r.name,
                "opening_debit": r.open_d,
                "opening_credit": r.open_c,
                "debit": r.per_d,
                "credit": r.per_c,
                "closing_debit": r.close_d,
                "closing_credit": r.close_c,
            }
            for r in tb.rows[:200]
        ],
    }


@ai_tool(
    name="get_account_ledger",
    label="Sổ chi tiết tài khoản",
    perm="reports.view",
    description=(
        "Detailed ledger of one account (prefix match, e.g. 112 includes "
        "1121) between dates: opening balance, each posted line with contra "
        "account and running balance (debit minus credit), closing balance. "
        "Optional partner_code filters by customer/supplier."
    ),
    parameters={
        "type": "object",
        "required": ["account"],
        "properties": {
            "account": {
                "type": "string",
                "description": "Account code, e.g. 131",
            },
            "start": DATE,
            "end": DATE,
            "partner_code": {"type": "string"},
            "limit": {
                "type": "integer",
                "description": "Max lines (default 50, max 200)",
            },
        },
    },
)
def get_account_ledger(
    db,
    ctx,
    account: str,
    start: str | None = None,
    end: str | None = None,
    partner_code: str | None = None,
    limit: int = 50,
) -> dict:
    s, e = _period(start, end)
    pid = _partner_id(db, ctx.company_id, partner_code)
    account = account.strip()
    can_payroll = ctx.can("payroll.view")
    if pid and not can_payroll and account.startswith(("334", "3335", "141")):
        from app.features.partners.models import Partner

        p = db.get(Partner, pid)
        if p is not None and p.is_employee:
            return {
                "error": "Cần quyền xem lương để xem sổ chi tiết của nhân viên"
            }
    book = books.ledger_book(
        db,
        ctx.company_id,
        account,
        s,
        e,
        partner_id=pid,
        mask_payroll=not can_payroll,
    )
    limit = max(1, min(int(limit or 50), 200))
    return {
        "account": book.account,
        "name": book.account_name,
        "start": s.isoformat(),
        "end": e.isoformat(),
        "opening_balance": book.opening,
        "total_debit": book.total_debit,
        "total_credit": book.total_credit,
        "closing_balance": book.closing,
        "truncated": len(book.rows) > limit,
        "lines": [
            {
                "date": r.date.isoformat(),
                "number": r.number,
                "description": r.description,
                "contra": r.contra,
                "debit": r.debit,
                "credit": r.credit,
                "balance": r.balance,
                "entry_id": r.entry_id,
            }
            for r in book.rows[:limit]
        ],
    }


def _key_lines(st, codes) -> dict:
    return {
        r.line.code: {
            "name": r.line.name,
            "current": r.current,
            "previous": r.previous,
        }
        for r in st.rows
        if r.line.code in codes
    }


@ai_tool(
    name="get_financial_statements",
    label="Báo cáo tài chính",
    perm="reports.view",
    description=(
        "Summary of the financial statements for a fiscal year under the "
        "company's accounting regime (TT133: B01a-DNN/B02-DNN; TT99: "
        "B01-DN/B02-DN with short/long-term lines; TT58: B01-DNSN/"
        "B02-DNSN): every non-zero line of the statement of financial "
        "position and of the income statement with its code, name, current "
        "and previous figures, the line codes of total assets, equity, net "
        "revenue and profit after tax, and warnings."
    ),
    parameters={
        "type": "object",
        "required": ["year"],
        "properties": {"year": {"type": "integer"}},
    },
)
def get_financial_statements(db, ctx, year: int) -> dict:
    company = db.get(Company, ctx.company_id)
    p = make("year", int(year))
    ss = statements.statement_set(company)
    b01 = ss.builders["b01a"](db, company, p)
    b02 = ss.builders["b02"](db, company, p)

    def lines(st) -> dict:
        codes = {
            r.line.code
            for r in st.rows
            if r.line.code[:1].isdigit() and (r.current or r.previous)
        }
        return _key_lines(st, codes)

    return {
        "year": int(year),
        "forms": [b01.code, b02.code],
        "key_lines": ss.lines,
        "balance_sheet": lines(b01),
        "income_statement": lines(b02),
        "warnings": b01.warnings + b02.warnings,
    }


@ai_tool(
    name="get_profit_and_loss",
    label="Kết quả kinh doanh",
    perm="reports.view",
    description=(
        "Income statement (B02 form of the company's accounting regime) for "
        "an arbitrary date range: revenue, "
        "deductions, COGS, financial income/expenses, operating expenses, "
        "other income/expenses, profit before/after tax."
    ),
    parameters={"type": "object", "properties": {"start": DATE, "end": DATE}},
)
def get_profit_and_loss(
    db, ctx, start: str | None = None, end: str | None = None
) -> dict:
    s, e = _period(start, end)
    company = db.get(Company, ctx.company_id)
    st = statements.statement_set(company).builders["b02"](
        db, company, make("custom", s.year, start=s, end=e)
    )
    return {
        "start": s.isoformat(),
        "end": e.isoformat(),
        "lines": [
            {"code": r.line.code, "name": r.line.name, "amount": r.current}
            for r in st.rows
        ],
    }


@ai_tool(
    name="create_draft_journal_entry",
    label="Lập nháp phiếu kế toán",
    perm="journal.create",
    write=True,
    description=(
        "Create a DRAFT general journal voucher (phiếu kế toán). Each line is "
        "a debit/credit account pair with an amount in VND. Partner codes are "
        "required for receivable/payable accounts (131, 331, 141). The draft "
        "is NOT posted; a user must review and post it."
    ),
    parameters={
        "type": "object",
        "required": ["date", "description", "lines"],
        "properties": {
            "date": DATE,
            "description": {"type": "string"},
            "lines": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {
                    "type": "object",
                    "required": ["debit", "credit", "amount"],
                    "properties": {
                        "debit": {
                            "type": "string",
                            "description": "Debit account code",
                        },
                        "credit": {
                            "type": "string",
                            "description": "Credit account code",
                        },
                        "amount": {
                            "type": "integer",
                            "description": "Amount in VND",
                        },
                        "description": {"type": "string"},
                        "debit_partner_code": {"type": "string"},
                        "credit_partner_code": {"type": "string"},
                    },
                },
            },
        },
    },
)
def create_draft_journal_entry(
    db, ctx, date: str, description: str, lines: list[dict]
) -> dict:  # noqa: A002
    d = _d(date, today())
    items = []
    for ln in lines or []:
        try:
            amount = int(ln.get("amount"))
        except (TypeError, ValueError) as e:
            raise BusinessError("Số tiền phải là số nguyên đồng") from e
        items.append(
            js.LineIn(
                debit_account=str(ln.get("debit") or "").strip(),
                credit_account=str(ln.get("credit") or "").strip(),
                amount=amount,
                description=(ln.get("description") or description or "")[:500],
                debit_partner_id=_partner_id(
                    db, ctx.company_id, ln.get("debit_partner_code")
                ),
                credit_partner_id=_partner_id(
                    db, ctx.company_id, ln.get("credit_partner_code")
                ),
            )
        )
    e = js.create_entry(
        db,
        ctx,
        js.EntryIn(
            doc_type="PK",
            date=d,
            doc_date=d,
            description=description,
            lines=items,
            source="ai",
        ),
    )
    return {
        "draft_id": e.id,
        "url": e.url,
        "status": e.status,
        "total": e.total,
        "summary": (
            f"Đã lập phiếu kế toán nháp #{e.id} ngày {d:%d/%m/%Y}, "
            f"tổng {e.total:,} đồng. "
            "Người dùng cần kiểm tra và ghi sổ."
        ).replace(",", "."),
    }
