"""Data checks for the general journal."""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import or_, select

from app.core.dates import fmt_date, iter_months, month_bounds, today
from app.core.money import format_vnd
from app.core.registry import CheckIssue, data_check
from app.features.accounts.models import Account
from app.features.bank.models import BankAccount
from app.features.journal import locks, opening, queries
from app.features.journal.models import (
    CANCELLED,
    DRAFT,
    POSTED,
    JournalEntry,
    JournalLine,
)
from app.features.org.models import Company


@data_check(
    code="opening_unbalanced",
    title="Số dư đầu kỳ chưa cân",
    perm="journal.view",
    order=10,
)
def check_opening(db, ctx):
    d, c = opening.totals(db, ctx.company_id)
    if d != c:
        return [
            CheckIssue(
                f"Tổng dư Nợ đầu kỳ {format_vnd(d)} "
                f"khác tổng dư Có {format_vnd(c)} "
                f"(lệch {format_vnd(abs(d - c))})",
                "error",
                "/opening",
            )
        ]
    return []


@data_check(
    code="negative_money",
    title="Tiền mặt, tiền gửi bị âm",
    perm="journal.view",
    order=20,
)
def check_negative(db, ctx):
    out = []
    codes = db.scalars(
        select(Account.code).where(
            Account.company_id == ctx.company_id, Account.no_negative.is_(True)
        )
    )
    for code in codes:
        banks = (
            [None]
            if not code.startswith("112")
            else list(
                db.scalars(
                    select(BankAccount.id).where(
                        BankAccount.company_id == ctx.company_id,
                        BankAccount.account_code == code,
                    )
                )
            )
        )
        for bid in banks:
            p = queries.first_negative(
                db, ctx.company_id, code, bank_account_id=bid
            )
            if p is not None:
                out.append(
                    CheckIssue(
                        f"TK {code}: số dư âm {format_vnd(p.balance)} "
                        f"tại ngày {fmt_date(p.date)}"
                        + (f" (chứng từ {p.number})" if p.number else ""),
                        "error",
                        f"/entries/{p.entry_id}" if p.entry_id else None,
                        p.date,
                    )
                )
    return out


@data_check(
    code="number_gaps",
    title="Số chứng từ bị nhảy hoặc trùng",
    perm="journal.view",
    order=30,
)
def check_number_gaps(db, ctx):
    rows = db.execute(
        select(JournalEntry.doc_type, JournalEntry.number).where(
            JournalEntry.company_id == ctx.company_id,
            JournalEntry.status.in_((POSTED, CANCELLED)),
            JournalEntry.number.is_not(None),
        )
    ).all()
    series: dict[str, list[int]] = {}
    pat = re.compile(r"^(.*?)(\d+)$")
    for _doc, number in rows:
        m = pat.match(number)
        if m:
            series.setdefault(m.group(1), []).append(int(m.group(2)))
    out = []
    for prefix, nums in sorted(series.items()):
        nums.sort()
        dup = sorted({n for n in nums if nums.count(n) > 1})
        missing = sorted(set(range(nums[0], nums[-1] + 1)) - set(nums))
        if missing:
            shown = ", ".join(f"{prefix}{n:04d}" for n in missing[:5]) + (
                "…" if len(missing) > 5 else ""
            )
            out.append(
                CheckIssue(
                    f"Dãy {prefix}…: thiếu {len(missing)} số ({shown})",
                    "warning",
                )
            )
        if dup:
            out.append(
                CheckIssue(
                    f"Dãy {prefix}…: trùng số "
                    f"{', '.join(str(n) for n in dup[:5])}",
                    "error",
                )
            )
    return out


@data_check(
    code="missing_partner",
    title="Thiếu đối tượng công nợ",
    perm="journal.view",
    order=40,
)
def check_missing_partner(db, ctx):
    required = list(
        db.scalars(
            select(Account.code).where(
                Account.company_id == ctx.company_id,
                Account.partner_mode == "required",
            )
        )
    )
    if not required:
        return []
    jl, je = JournalLine, JournalEntry
    rows = db.execute(
        select(je.id, je.number, je.date)
        .join(jl, jl.entry_id == je.id)
        .where(
            je.company_id == ctx.company_id,
            je.status == POSTED,
            or_(
                (jl.debit_account.in_(required))
                & jl.debit_partner_id.is_(None),
                (jl.credit_account.in_(required))
                & jl.credit_partner_id.is_(None),
            ),
        )
        .distinct()
        .limit(50)
    ).all()
    return [
        CheckIssue(
            f"Chứng từ {n} ngày {fmt_date(d)} "
            "có dòng công nợ chưa chọn đối tượng",
            "warning",
            f"/entries/{i}",
            d,
        )
        for i, n, d in rows
    ]


@data_check(
    code="old_drafts",
    title="Chứng từ nháp quá 7 ngày",
    perm="journal.view",
    order=60,
)
def check_old_drafts(db, ctx):
    cutoff = today() - timedelta(days=7)
    rows = db.execute(
        select(
            JournalEntry.id,
            JournalEntry.doc_type,
            JournalEntry.date,
            JournalEntry.description,
        )
        .where(
            JournalEntry.company_id == ctx.company_id,
            JournalEntry.status == DRAFT,
            JournalEntry.date <= cutoff,
        )
        .order_by(JournalEntry.date)
        .limit(50)
    ).all()
    from app.core.registry import doc_label, doc_url

    return [
        CheckIssue(
            f"{doc_label(t)} nháp ngày {fmt_date(d)}: "
            f"{desc or '(không diễn giải)'}",
            "info",
            doc_url(t, i),
            d,
        )
        for i, t, d, desc in rows
    ]


@data_check(
    code="unlocked_periods",
    title="Kỳ đã qua chưa khóa sổ",
    perm="journal.view",
    order=70,
)
def check_unlocked(db, ctx):
    company = db.get(Company, ctx.company_id)
    locked = locks.locked_set(db, company.id)
    cutoff = today() - timedelta(days=30)
    pending = []
    for y, m in iter_months(company.start_date, today()):
        if month_bounds(y, m)[1] <= cutoff and not locks.month_locked(
            locked, y, m
        ):
            pending.append(f"{m:02d}/{y}")
    if not pending:
        return []
    shown = ", ".join(pending[:6]) + ("…" if len(pending) > 6 else "")
    return [
        CheckIssue(
            f"{len(pending)} tháng đã kết thúc hơn 30 ngày "
            f"chưa khóa sổ: {shown}",
            "info",
            "/locks",
        )
    ]
