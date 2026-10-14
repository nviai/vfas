"""Fixed assets and tools / prepaid expenses.

Asset records, depreciation/allocation schedules, monthly runs and
disposals.

Straight-line monthly depreciation (D-v2-15): monthly amount =
round(amount to depreciate / remaining months), with the last month
absorbing the rounding difference; it starts in the month of
acquisition (or, optionally, the following month). The schedule is
based on calendar months, so re-running a month always yields the
same amount. "Posted" accumulated depreciation = opening accumulated
depreciation + runs whose entries have been posted.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import snapshot, write_audit
from app.core.dates import fmt_date, month_bounds
from app.core.errors import BusinessError, NotFoundError
from app.core.money import format_vnd
from app.core.regimes import account as regime_account
from app.core.regimes import regime_of
from app.features.accounts.models import Account
from app.features.assets.models import (
    ASSET_CATEGORIES,
    DEPRECIATION_ACCOUNT,
    DISPOSED,
    FIXED_ASSET_PREFIXES,
    IN_USE,
    PREPAID_KINDS,
    RUN_ALLOCATION,
    RUN_DEPRECIATION,
    AssetRun,
    AssetRunLine,
    FixedAsset,
    PrepaidItem,
)
from app.features.journal import locks
from app.features.journal import service as js
from app.features.journal.models import DRAFT, POSTED, JournalEntry
from app.features.org.models import Company


def _now() -> datetime:
    return datetime.now(timezone.utc)


def first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def month_index(start: date, year: int, month: int) -> int:
    """Month number within the schedule (1 = start month)."""
    return (year - start.year) * 12 + (month - start.month) + 1


def scheduled_amount(base: int, n: int, k: int) -> int:
    """Amount for month k (1..n) of a straight-line schedule.

    The last month absorbs the rounding difference.
    """
    if base <= 0 or n <= 0 or k < 1 or k > n:
        return 0
    monthly = int(
        (Decimal(base) / Decimal(n)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    done = monthly * (k - 1)
    if k == n:
        return max(0, base - done)
    return max(0, min(monthly, base - done))


# ---------------------------------- fixed assets: depreciation schedule


def asset_base(a: FixedAsset) -> tuple[int, int]:
    """Return (amount still to depreciate in the app, remaining
    months of depreciation).
    """
    return max(0, a.cost - (a.opening_accumulated or 0)), max(
        0, a.useful_life_months - (a.opening_months_used or 0)
    )


def depreciation_for(a: FixedAsset, year: int, month: int) -> int:
    """Scheduled depreciation for the month.

    0 if depreciation has not started yet, has ended, or the asset is
    disposed of from this month on.
    """
    if (
        a.status == DISPOSED
        and a.disposal_date
        and (year, month) >= (a.disposal_date.year, a.disposal_date.month)
    ):
        return 0
    base, n = asset_base(a)
    return scheduled_amount(
        base, n, month_index(a.depreciation_start, year, month)
    )


def monthly_rate(a: FixedAsset) -> int:
    base, n = asset_base(a)
    return scheduled_amount(base, n, 1)


def posted_depreciation(
    db: Session,
    company_id: int,
    asset_ids: list[int] | None = None,
    *,
    upto: tuple[int, int] | None = None,
    kind: str = "asset",
) -> dict[int, int]:
    """Total run amounts with posted entries, per asset.

    Counts runs up to the end of month `upto` if given.
    """
    line, run, entry = AssetRunLine, AssetRun, JournalEntry
    stmt = (
        select(line.ref_id, func.coalesce(func.sum(line.amount), 0))
        .join(run, run.id == line.run_id)
        .join(entry, entry.id == run.entry_id)
        .where(
            line.company_id == company_id,
            line.ref_kind == kind,
            entry.status == POSTED,
        )
        .group_by(line.ref_id)
    )
    if asset_ids is not None:
        stmt = stmt.where(line.ref_id.in_(asset_ids))
    if upto is not None:
        stmt = stmt.where(
            (run.year * 100 + run.month) <= upto[0] * 100 + upto[1]
        )
    return {rid: int(total) for rid, total in db.execute(stmt)}


@dataclass
class AssetValue:
    asset: FixedAsset
    cost: int
    accumulated: int
    period_depreciation: int = 0

    @property
    def remaining(self) -> int:
        return self.cost - self.accumulated


def register(
    db: Session, company_id: int, on: date, *, include_disposed: bool = False
) -> list[AssetValue]:
    """Fixed asset register at a date: cost, posted accumulated
    depreciation and remaining value.
    """
    assets = list_assets(
        db, company_id, status=None if include_disposed else IN_USE
    )
    posted = posted_depreciation(
        db, company_id, [a.id for a in assets], upto=(on.year, on.month)
    )
    out = []
    for a in assets:
        if a.acquired_date > on:
            continue
        if (
            not include_disposed
            and a.status == DISPOSED
            and a.disposal_date
            and a.disposal_date <= on
        ):
            continue
        out.append(
            AssetValue(
                asset=a,
                cost=a.cost,
                accumulated=(a.opening_accumulated or 0) + posted.get(a.id, 0),
            )
        )
    return out


def schedule(a: FixedAsset) -> list[tuple[date, int, int]]:
    """Full depreciation schedule: (month, amount, accumulated
    depreciation after that month).
    """
    base, n = asset_base(a)
    acc = a.opening_accumulated or 0
    out = []
    for k in range(1, n + 1):
        amt = scheduled_amount(base, n, k)
        acc += amt
        out.append((add_months(a.depreciation_start, k - 1), amt, acc))
    return out


# ------------------------------------------------ fixed assets: records


@dataclass
class AssetInput:
    name: str
    cost: int
    useful_life_months: int
    acquired_date: date
    code: str = ""
    category: str = "office"
    # Empty = the regime's defaults (TT133 2111/6422, TT99 211/6424).
    asset_account: str = ""
    depreciation_account: str = ""
    expense_account: str = ""
    department: str = ""
    manufacturer: str = ""
    serial_no: str = ""
    year_made: int | None = None
    opening_accumulated: int = 0
    opening_months_used: int = 0
    start_next_month: bool = False
    depreciation_start: date | None = None
    source: str = "manual"
    entry_id: int | None = None
    partner_id: int | None = None
    note: str = ""


FIELDS = (
    "code",
    "name",
    "category",
    "asset_account",
    "depreciation_account",
    "expense_account",
    "department",
    "manufacturer",
    "serial_no",
    "year_made",
    "cost",
    "opening_accumulated",
    "opening_months_used",
    "useful_life_months",
    "acquired_date",
    "depreciation_start",
    "source",
    "entry_id",
    "partner_id",
    "note",
)


def list_assets(
    db: Session, company_id: int, *, status: str | None = None, q: str = ""
) -> list[FixedAsset]:
    stmt = select(FixedAsset).where(FixedAsset.company_id == company_id)
    if status:
        stmt = stmt.where(FixedAsset.status == status)
    if q:
        from app.core.db import ulower

        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            ulower(FixedAsset.name).like(like)
            | ulower(FixedAsset.code).like(like)
        )
    return list(db.scalars(stmt.order_by(FixedAsset.code)))


def get_asset(db: Session, company_id: int, asset_id: int) -> FixedAsset:
    a = db.get(FixedAsset, asset_id)
    if a is None or a.company_id != company_id:
        raise NotFoundError("Không tìm thấy tài sản")
    return a


def next_code(db: Session, company_id: int, model, prefix: str) -> str:
    n = 0
    for c in db.scalars(
        select(model.code).where(
            model.company_id == company_id, model.code.like(f"{prefix}%")
        )
    ):
        tail = c[len(prefix) :]
        if tail.isdigit():
            n = max(n, int(tail))
    return f"{prefix}{n + 1:04d}"


def _postable(
    db: Session,
    company_id: int,
    code: str,
    prefixes: tuple[str, ...],
    label: str,
) -> None:
    acc = db.scalar(
        select(Account).where(
            Account.company_id == company_id, Account.code == code
        )
    )
    if (
        acc is None
        or not acc.is_postable
        or not acc.is_active
        or not code.startswith(prefixes)
    ):
        raise BusinessError(
            f"{label} không hợp lệ (TK chi tiết thuộc {', '.join(prefixes)})"
        )


def expense_prefixes(db: Session, company_id: int) -> tuple[str, ...]:
    """Expense accounts depreciation/allocation may be charged to."""
    return regime_of(db.get(Company, company_id)).expense_prefixes


def _check_refs(
    db: Session,
    company_id: int,
    entry_id: int | None,
    partner_id: int | None = None,
) -> None:
    """The referenced acquisition entry / partner must belong to the
    company.
    """
    if entry_id:
        e = db.get(JournalEntry, entry_id)
        if e is None or e.company_id != company_id:
            raise BusinessError("Chứng từ ghi tăng không hợp lệ")
    if partner_id:
        from app.features.partners.models import Partner

        p = db.get(Partner, partner_id)
        if p is None or p.company_id != company_id:
            raise BusinessError("Đối tượng không hợp lệ")


def _normalize_asset(
    db: Session, company_id: int, inp: AssetInput, existing: FixedAsset | None
) -> AssetInput:
    _check_refs(
        db,
        company_id,
        inp.entry_id if existing is None else None,
        inp.partner_id,
    )
    if not inp.name.strip():
        raise BusinessError("Nhập tên tài sản")
    if inp.category not in ASSET_CATEGORIES:
        raise BusinessError("Nhóm tài sản không hợp lệ")
    if inp.cost <= 0:
        raise BusinessError("Nguyên giá phải lớn hơn 0")
    if inp.useful_life_months <= 0 or inp.useful_life_months > 1200:
        raise BusinessError("Thời gian sử dụng (tháng) không hợp lệ")
    if inp.opening_accumulated < 0 or inp.opening_accumulated > inp.cost:
        raise BusinessError("Hao mòn lũy kế đầu kỳ phải từ 0 đến nguyên giá")
    if (
        inp.opening_months_used < 0
        or inp.opening_months_used >= inp.useful_life_months
        and inp.opening_accumulated < inp.cost
    ):
        raise BusinessError(
            "Số tháng đã khấu hao phải nhỏ hơn thời gian sử dụng"
        )
    company = db.get(Company, company_id)
    inp.asset_account = inp.asset_account or regime_account(
        company, "fixed_asset"
    )
    inp.expense_account = inp.expense_account or regime_account(
        company, "expense_depreciation"
    )
    _postable(
        db,
        company_id,
        inp.asset_account,
        FIXED_ASSET_PREFIXES,
        "TK nguyên giá",
    )
    inp.depreciation_account = (
        inp.depreciation_account
        or DEPRECIATION_ACCOUNT.get(inp.asset_account[:4])
        or DEPRECIATION_ACCOUNT.get(inp.asset_account[:3], "2141")
    )
    _postable(db, company_id, inp.depreciation_account, ("214",), "TK hao mòn")
    _postable(
        db,
        company_id,
        inp.expense_account,
        expense_prefixes(db, company_id),
        "TK chi phí khấu hao",
    )
    start = inp.depreciation_start or first_of_month(inp.acquired_date)
    if inp.start_next_month and inp.depreciation_start is None:
        start = add_months(start, 1)
    inp.depreciation_start = first_of_month(start)
    code = inp.code.strip()
    if code:
        dup = db.scalar(
            select(FixedAsset.id).where(
                FixedAsset.company_id == company_id, FixedAsset.code == code
            )
        )
        if dup and (existing is None or dup != existing.id):
            raise BusinessError(f"Mã tài sản {code} đã có")
    return inp


def _has_posted_runs(db: Session, asset_id: int) -> bool:
    return bool(
        posted_depreciation(
            db, db.get(FixedAsset, asset_id).company_id, [asset_id]
        ).get(asset_id)
    )


def create_asset(db: Session, ctx, inp: AssetInput) -> FixedAsset:
    inp = _normalize_asset(db, ctx.company_id, inp, None)
    a = FixedAsset(company_id=ctx.company_id, status=IN_USE, created_at=_now())
    for f in FIELDS:
        setattr(a, f, getattr(inp, f))
    a.name = inp.name.strip()[:255]
    a.code = inp.code.strip() or next_code(
        db, ctx.company_id, FixedAsset, "TS"
    )
    db.add(a)
    db.flush()
    write_audit(
        db,
        ctx,
        action="asset.create",
        entity_type="asset",
        entity_id=a.id,
        summary=(
            f"Ghi tăng TSCĐ {a.code} – {a.name}: "
            f"nguyên giá {format_vnd(a.cost)}"
        ),
        after=snapshot(a, FIELDS),
    )
    db.commit()
    return a


def update_asset(
    db: Session, ctx, a: FixedAsset, inp: AssetInput
) -> FixedAsset:
    if a.company_id != ctx.company_id:
        raise NotFoundError()
    if a.status == DISPOSED:
        raise BusinessError("Tài sản đã ghi giảm, không sửa được")
    inp = _normalize_asset(db, ctx.company_id, inp, a)
    locked = (
        "cost",
        "opening_accumulated",
        "opening_months_used",
        "useful_life_months",
        "depreciation_start",
        "asset_account",
        "depreciation_account",
    )
    if _has_posted_runs(db, a.id):
        changed = [f for f in locked if getattr(inp, f) != getattr(a, f)]
        if changed:
            raise BusinessError(
                "Tài sản đã trích khấu hao: không đổi nguyên giá, thời gian, "
                "tháng bắt đầu, tài khoản. "
                "Hủy các lần trích khấu hao trước hoặc ghi nhận điều chỉnh."
            )
    before = snapshot(a, FIELDS)
    for f in FIELDS:
        if f in ("source", "entry_id"):
            continue
        setattr(a, f, getattr(inp, f))
    a.code = inp.code.strip() or a.code
    after = snapshot(a, FIELDS)
    changed = {k for k in after if after[k] != before[k]}
    if changed:
        write_audit(
            db,
            ctx,
            action="asset.update",
            entity_type="asset",
            entity_id=a.id,
            summary=f"Sửa TSCĐ {a.code}",
            before={k: before[k] for k in changed},
            after={k: after[k] for k in changed},
        )
    db.commit()
    return a


def delete_asset(db: Session, ctx, a: FixedAsset) -> None:
    if a.company_id != ctx.company_id:
        raise NotFoundError()
    used = db.scalar(
        select(func.count(AssetRunLine.id)).where(
            AssetRunLine.ref_kind == "asset", AssetRunLine.ref_id == a.id
        )
    )
    if used or a.disposal_entry_id:
        raise BusinessError(
            "Tài sản đã có số liệu khấu hao/ghi giảm, không xóa được"
        )
    write_audit(
        db,
        ctx,
        action="asset.delete",
        entity_type="asset",
        entity_id=a.id,
        summary=f"Xóa thẻ TSCĐ {a.code}",
        before=snapshot(a, FIELDS),
    )
    db.delete(a)
    db.commit()


def purchase_prefill(db: Session, ctx, entry_id: int) -> list[dict]:
    """Suggest fixed asset cards from an entry that debits 211x.

    Purchase document lines (PurchaseLine) are preferred; otherwise the
    journal lines are used.
    """
    e = js.get_entry(db, ctx, entry_id)
    out: list[dict] = []
    try:
        from app.features.purchases.models import PurchaseLine  # group C
    except Exception:  # noqa: BLE001 - purchases module not available
        PurchaseLine = None  # noqa: N806 - keeps the imported class name
    if PurchaseLine is not None and e.source == "purchases" and e.source_id:
        try:
            lines = db.scalars(
                select(PurchaseLine).where(PurchaseLine.doc_id == e.source_id)
            ).all()
            for ln in lines:
                acc = getattr(ln, "debit_account", "") or ""
                if acc.startswith(FIXED_ASSET_PREFIXES):
                    vat_extra = (
                        0
                        if getattr(ln, "deductible", True)
                        else int(getattr(ln, "vat_amount", 0) or 0)
                    )
                    out.append(
                        {
                            "name": getattr(ln, "description", "")
                            or e.description,
                            "asset_account": acc,
                            "cost": int(getattr(ln, "amount", 0) or 0)
                            + vat_extra,
                        }
                    )
        except Exception:  # noqa: BLE001 - fall back to journal lines
            db.rollback()
            out = []
    if not out:
        for ln in e.lines:
            if ln.debit_account.startswith(FIXED_ASSET_PREFIXES):
                out.append(
                    {
                        "name": ln.description or e.description,
                        "asset_account": ln.debit_account,
                        "cost": ln.amount,
                    }
                )
    for row in out:
        row.update(
            {
                "acquired_date": e.date,
                "entry_id": e.id,
                "partner_id": e.partner_id,
                "source": "purchase",
            }
        )
    return out


# --------------------------------------------- tools / prepaid expenses


@dataclass
class PrepaidInput:
    name: str
    amount: int
    months: int
    start_month: date
    code: str = ""
    kind: str = "tool"
    quantity: int = 1
    prepaid_account: str = "242"
    # Empty = the regime's default (TT133 6422, TT99 6423).
    expense_account: str = ""
    department: str = ""
    opening_allocated: int = 0
    opening_months_used: int = 0
    entry_id: int | None = None
    note: str = ""


PREPAID_FIELDS = (
    "code",
    "name",
    "kind",
    "quantity",
    "amount",
    "months",
    "start_month",
    "prepaid_account",
    "expense_account",
    "department",
    "opening_allocated",
    "opening_months_used",
    "entry_id",
    "note",
)


def list_prepaid(
    db: Session, company_id: int, *, active: bool | None = None
) -> list[PrepaidItem]:
    stmt = select(PrepaidItem).where(PrepaidItem.company_id == company_id)
    if active is not None:
        stmt = stmt.where(PrepaidItem.is_active.is_(active))
    return list(db.scalars(stmt.order_by(PrepaidItem.code)))


def get_prepaid(db: Session, company_id: int, item_id: int) -> PrepaidItem:
    p = db.get(PrepaidItem, item_id)
    if p is None or p.company_id != company_id:
        raise NotFoundError("Không tìm thấy khoản phân bổ")
    return p


def prepaid_base(p: PrepaidItem) -> tuple[int, int]:
    return max(0, p.amount - (p.opening_allocated or 0)), max(
        0, p.months - (p.opening_months_used or 0)
    )


def allocation_for(p: PrepaidItem, year: int, month: int) -> int:
    if (
        not p.is_active
        and p.stopped_date
        and (year, month) >= (p.stopped_date.year, p.stopped_date.month)
    ):
        return 0
    base, n = prepaid_base(p)
    return scheduled_amount(base, n, month_index(p.start_month, year, month))


def _normalize_prepaid(
    db: Session,
    company_id: int,
    inp: PrepaidInput,
    existing: PrepaidItem | None,
) -> PrepaidInput:
    _check_refs(db, company_id, inp.entry_id if existing is None else None)
    if not inp.name.strip():
        raise BusinessError("Nhập tên công cụ dụng cụ / khoản chi phí")
    if inp.kind not in PREPAID_KINDS:
        raise BusinessError("Loại không hợp lệ")
    if inp.amount <= 0:
        raise BusinessError("Giá trị phân bổ phải lớn hơn 0")
    if inp.months <= 0 or inp.months > 600:
        raise BusinessError("Số tháng phân bổ không hợp lệ")
    if inp.opening_allocated < 0 or inp.opening_allocated > inp.amount:
        raise BusinessError("Số đã phân bổ đầu kỳ phải từ 0 đến giá trị")
    if inp.opening_months_used < 0 or inp.opening_months_used > inp.months:
        raise BusinessError("Số tháng đã phân bổ không hợp lệ")
    _postable(
        db, company_id, inp.prepaid_account, ("242",), "TK chi phí trả trước"
    )
    inp.expense_account = inp.expense_account or regime_account(
        db.get(Company, company_id), "expense_tools"
    )
    _postable(
        db,
        company_id,
        inp.expense_account,
        expense_prefixes(db, company_id),
        "TK chi phí",
    )
    inp.start_month = first_of_month(inp.start_month)
    code = inp.code.strip()
    if code:
        dup = db.scalar(
            select(PrepaidItem.id).where(
                PrepaidItem.company_id == company_id, PrepaidItem.code == code
            )
        )
        if dup and (existing is None or dup != existing.id):
            raise BusinessError(f"Mã {code} đã có")
    return inp


def create_prepaid(db: Session, ctx, inp: PrepaidInput) -> PrepaidItem:
    inp = _normalize_prepaid(db, ctx.company_id, inp, None)
    p = PrepaidItem(
        company_id=ctx.company_id, is_active=True, created_at=_now()
    )
    for f in PREPAID_FIELDS:
        setattr(p, f, getattr(inp, f))
    p.name = inp.name.strip()[:255]
    p.code = inp.code.strip() or next_code(
        db, ctx.company_id, PrepaidItem, "CC" if inp.kind == "tool" else "TT"
    )
    db.add(p)
    db.flush()
    write_audit(
        db,
        ctx,
        action="prepaid.create",
        entity_type="prepaid",
        entity_id=p.id,
        summary=(
            f"Ghi tăng {p.kind_label.lower()} {p.code} – {p.name}: "
            f"{format_vnd(p.amount)}, {p.months} tháng"
        ),
    )
    db.commit()
    return p


def update_prepaid(
    db: Session,
    ctx,
    p: PrepaidItem,
    inp: PrepaidInput,
    *,
    active: bool | None = None,
    stopped_date: date | None = None,
) -> PrepaidItem:
    if p.company_id != ctx.company_id:
        raise NotFoundError()
    inp = _normalize_prepaid(db, ctx.company_id, inp, p)
    posted = posted_depreciation(
        db, ctx.company_id, [p.id], kind="prepaid"
    ).get(p.id, 0)
    if posted:
        for f in (
            "amount",
            "months",
            "start_month",
            "opening_allocated",
            "opening_months_used",
            "prepaid_account",
        ):
            if getattr(inp, f) != getattr(p, f):
                raise BusinessError(
                    "Đã phân bổ: không đổi giá trị, số tháng, tháng bắt đầu, "
                    "tài khoản."
                )
    before = snapshot(p, PREPAID_FIELDS)
    for f in PREPAID_FIELDS:
        if f == "entry_id":
            continue
        setattr(p, f, getattr(inp, f))
    p.code = inp.code.strip() or p.code
    if active is not None:
        p.is_active = active
        p.stopped_date = None if active else (stopped_date or p.stopped_date)
    write_audit(
        db,
        ctx,
        action="prepaid.update",
        entity_type="prepaid",
        entity_id=p.id,
        summary=f"Sửa {p.code}",
        before=before,
        after=snapshot(p, PREPAID_FIELDS),
    )
    db.commit()
    return p


def prepaid_prefill(db: Session, ctx, entry_id: int) -> list[dict]:
    e = js.get_entry(db, ctx, entry_id)
    return [
        {
            "name": ln.description or e.description,
            "amount": ln.amount,
            "start_month": first_of_month(e.date),
            "entry_id": e.id,
        }
        for ln in e.lines
        if ln.debit_account.startswith("242")
    ]


# ------------------------------- monthly depreciation / allocation runs


@dataclass
class RunPreviewLine:
    ref_kind: str
    ref: object
    amount: int
    debit_account: str
    credit_account: str


@dataclass
class RunPreview:
    kind: str
    year: int
    month: int
    lines: list[RunPreviewLine] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(ln.amount for ln in self.lines)

    def by_account(self) -> list[tuple[str, str, int]]:
        agg: dict[tuple[str, str], int] = defaultdict(int)
        for ln in self.lines:
            agg[(ln.debit_account, ln.credit_account)] += ln.amount
        return [(d, c, v) for (d, c), v in sorted(agg.items())]


def preview(
    db: Session, company_id: int, kind: str, year: int, month: int
) -> RunPreview:
    out = RunPreview(kind=kind, year=year, month=month)
    if kind == RUN_DEPRECIATION:
        for a in list_assets(db, company_id):
            if (
                a.status == DISPOSED
                and a.disposal_date
                and (year, month)
                >= (a.disposal_date.year, a.disposal_date.month)
            ):
                continue
            amt = depreciation_for(a, year, month)
            if amt > 0:
                out.lines.append(
                    RunPreviewLine(
                        "asset",
                        a,
                        amt,
                        a.expense_account,
                        a.depreciation_account,
                    )
                )
    else:
        for p in list_prepaid(db, company_id):
            amt = allocation_for(p, year, month)
            if amt > 0:
                out.lines.append(
                    RunPreviewLine(
                        "prepaid", p, amt, p.expense_account, p.prepaid_account
                    )
                )
    return out


def get_run(
    db: Session, company_id: int, kind: str, year: int, month: int
) -> AssetRun | None:
    return db.scalar(
        select(AssetRun).where(
            AssetRun.company_id == company_id,
            AssetRun.kind == kind,
            AssetRun.year == year,
            AssetRun.month == month,
        )
    )


def list_runs(db: Session, company_id: int, kind: str) -> list[AssetRun]:
    return list(
        db.scalars(
            select(AssetRun)
            .where(AssetRun.company_id == company_id, AssetRun.kind == kind)
            .order_by(AssetRun.year.desc(), AssetRun.month.desc())
        )
    )


KIND_DOC = {RUN_DEPRECIATION: "KH", RUN_ALLOCATION: "PB"}
KIND_LABEL = {
    RUN_DEPRECIATION: "Trích khấu hao TSCĐ",
    RUN_ALLOCATION: "Phân bổ CCDC, chi phí trả trước",
}


def execute_run(
    db: Session, ctx, kind: str, year: int, month: int
) -> AssetRun:
    """Run monthly depreciation (KH) / allocation (PB).

    Creates and posts the journal entry; re-running a month cancels
    that month's previous entry.
    """
    if kind not in KIND_DOC:
        raise BusinessError("Loại không hợp lệ")
    company = db.get(Company, ctx.company_id)
    start, end = month_bounds(year, month)
    if end < company.start_date:
        raise BusinessError("Tháng này trước ngày bắt đầu hạch toán")
    locks.assert_open(db, ctx.company_id, end, KIND_LABEL[kind].lower())
    pv = preview(db, ctx.company_id, kind, year, month)
    if not pv.lines:
        raise BusinessError(
            "Không có số cần "
            f"{'trích khấu hao' if kind == RUN_DEPRECIATION else 'phân bổ'} "
            f"trong tháng {month:02d}/{year}"
        )
    run = get_run(db, ctx.company_id, kind, year, month)
    if run is not None and run.entry is not None:
        old = run.entry
        if old.status == POSTED:
            js.cancel_entry(
                db,
                ctx,
                old,
                f"Tính lại {KIND_LABEL[kind].lower()} "
                f"tháng {month:02d}/{year}",
            )
        elif old.status == DRAFT:
            run.entry = None
            db.flush()
            js.delete_draft(db, ctx, old)
            db.refresh(run)
    label = KIND_LABEL[kind]
    lines = []
    for ln in pv.lines:
        ref = ln.ref
        lines.append(
            js.LineIn(
                ln.debit_account,
                ln.credit_account,
                ln.amount,
                f"{'Khấu hao' if kind == RUN_DEPRECIATION else 'Phân bổ'} "
                f"{ref.code} – {ref.name} "
                f"tháng {month:02d}/{year}",
                asset_id=ref.id if ln.ref_kind == "asset" else None,
            )
        )
    inp = js.EntryIn(
        doc_type=KIND_DOC[kind],
        date=end,
        description=f"{label} tháng {month:02d}/{year}",
        lines=lines,
        source="assets",
        meta={"asset_run": kind, "year": year, "month": month},
    )
    e = js.create_entry(db, ctx, inp, commit=False)
    if run is None:
        run = AssetRun(
            company_id=ctx.company_id,
            kind=kind,
            year=year,
            month=month,
            created_at=_now(),
            created_by=ctx.user_id,
        )
        db.add(run)
    run.entry = e
    run.lines.clear()
    db.flush()
    for ln in pv.lines:
        run.lines.append(
            AssetRunLine(
                company_id=ctx.company_id,
                ref_kind=ln.ref_kind,
                ref_id=ln.ref.id,
                amount=ln.amount,
                debit_account=ln.debit_account,
                credit_account=ln.credit_account,
            )
        )
    e.source_id = run.id
    db.flush()
    js.post_entry(
        db,
        ctx,
        e,
        action_label=f"ghi sổ {label.lower()} tháng {month:02d}/{year}",
    )
    return run


# ------------------------------------------------- fixed asset disposal


def dispose(
    db: Session,
    ctx,
    a: FixedAsset,
    on: date,
    reason: str,
    loss_account: str = "811",
) -> JournalEntry:
    """Dispose of a fixed asset (liquidation, sale, damage).

    Debit 214 (accumulated depreciation) + debit 811 (remaining value)
    / credit 211.
    """
    if a.company_id != ctx.company_id:
        raise NotFoundError()
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("Nhập lý do ghi giảm")
    if a.status == DISPOSED:
        raise BusinessError("Tài sản đã ghi giảm")
    if on < a.acquired_date:
        raise BusinessError("Ngày ghi giảm trước ngày ghi tăng")
    later = db.scalar(
        select(func.count(AssetRunLine.id))
        .join(AssetRun, AssetRun.id == AssetRunLine.run_id)
        .join(JournalEntry, JournalEntry.id == AssetRun.entry_id)
        .where(
            AssetRunLine.ref_kind == "asset",
            AssetRunLine.ref_id == a.id,
            JournalEntry.status == POSTED,
            (AssetRun.year * 100 + AssetRun.month) >= on.year * 100 + on.month,
        )
    )
    if later:
        raise BusinessError(
            "Đã trích khấu hao tài sản này từ tháng "
            f"{on.month:02d}/{on.year} trở đi. "
            "Tính lại khấu hao các tháng đó sau khi ghi giảm."
        )
    locks.assert_open(db, ctx.company_id, on, "ghi giảm TSCĐ")
    _postable(
        db,
        ctx.company_id,
        loss_account,
        ("811", "632", "642", "641"),
        "TK ghi nhận giá trị còn lại",
    )
    accumulated = (a.opening_accumulated or 0) + posted_depreciation(
        db, ctx.company_id, [a.id]
    ).get(a.id, 0)
    remaining = a.cost - accumulated
    lines = []
    if accumulated > 0:
        lines.append(
            js.LineIn(
                a.depreciation_account,
                a.asset_account,
                accumulated,
                f"Ghi giảm hao mòn lũy kế {a.code}",
                asset_id=a.id,
            )
        )
    if remaining > 0:
        lines.append(
            js.LineIn(
                loss_account,
                a.asset_account,
                remaining,
                f"Giá trị còn lại {a.code}",
                asset_id=a.id,
            )
        )
    inp = js.EntryIn(
        doc_type="TS",
        date=on,
        description=f"Ghi giảm TSCĐ {a.code} – {a.name}: {reason}"[:500],
        lines=lines,
        source="assets",
        source_id=a.id,
        meta={"asset_disposal": a.id},
    )
    e = js.create_entry(db, ctx, inp)
    try:
        js.post_entry(db, ctx, e, action_label=f"ghi giảm TSCĐ {a.code}")
    except Exception:
        db.rollback()
        draft = db.get(JournalEntry, e.id)
        if draft is not None and draft.status == DRAFT:
            js.delete_draft(db, ctx, draft)
        raise
    a = db.get(FixedAsset, a.id)
    a.status = DISPOSED
    a.disposal_date = on
    a.disposal_reason = reason
    a.disposal_entry_id = e.id
    write_audit(
        db,
        ctx,
        action="asset.dispose",
        entity_type="asset",
        entity_id=a.id,
        summary=(
            f"Ghi giảm TSCĐ {a.code} ngày {fmt_date(on)}: "
            f"nguyên giá {format_vnd(a.cost)}, "
            f"hao mòn {format_vnd(accumulated)}"
        ),
        reason=reason,
    )
    db.commit()
    return e


def revert_disposal(db: Session, entry: JournalEntry) -> None:
    """Put the asset back in use when its disposal entry is cancelled
    or its draft is deleted.
    """
    asset_id = (entry.meta or {}).get("asset_disposal")
    if not asset_id:
        return
    a = db.get(FixedAsset, asset_id)
    if a is not None and a.disposal_entry_id == entry.id:
        a.status = IN_USE
        a.disposal_date = None
        a.disposal_reason = None
        a.disposal_entry_id = None


# --------------------------------------------------------------- checks


def missing_months(
    db: Session, company_id: int, kind: str, until: date
) -> list[tuple[int, int]]:
    """Ended months (<= until) with a scheduled amount but no posted
    entry.
    """
    company = db.get(Company, company_id)
    items = (
        list_assets(db, company_id)
        if kind == RUN_DEPRECIATION
        else list_prepaid(db, company_id)
    )
    if not items:
        return []
    starts = [
        (i.depreciation_start if kind == RUN_DEPRECIATION else i.start_month)
        for i in items
    ]
    first = max(min(starts), first_of_month(company.start_date))
    runs = {(r.year, r.month): r for r in list_runs(db, company_id, kind)}
    out = []
    d = first
    while month_bounds(d.year, d.month)[1] <= until:
        r = runs.get((d.year, d.month))
        posted = (
            r is not None and r.entry is not None and r.entry.status == POSTED
        )
        if not posted and preview(db, company_id, kind, d.year, d.month).lines:
            out.append((d.year, d.month))
        d = add_months(d, 1)
    return out
