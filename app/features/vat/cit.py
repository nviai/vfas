"""Annual CIT finalization worksheet.

Follows the main indicators of the 03/TNDN return.

Accounting profit comes from revenue/expense account movements in the
year, EXCLUDING closing entries (KC). Tax rates follow CIT Law
67/2025/QH15 (from the 2025 tax period): 15% if the previous year's
total revenue is <= VND 3 billion; 17% if > 3 billion and <= 50
billion; 20% otherwise. The 15%/17% rates do not apply to subsidiaries
or affiliates that do not meet the conditions, so the rate can be
chosen manually.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BusinessError
from app.features.journal.queries import account_match, ledger_source
from app.features.vat.models import FORM_CIT, TaxDeclaration

MANUAL = ("B1", "B8", "C2", "C3", "C5", "C9")
LABELS = {
    "A1": "Tổng lợi nhuận kế toán trước thuế TNDN",
    "B1": "Điều chỉnh tăng lợi nhuận trước thuế (chi phí không được trừ, …)",
    "B8": "Điều chỉnh giảm lợi nhuận trước thuế",
    "B12": "Tổng thu nhập chịu thuế (A1 + B1 − B8)",
    "C2": "Thu nhập miễn thuế",
    "C3": "Lỗ từ các năm trước được chuyển",
    "C4": "Thu nhập tính thuế (B12 − C2 − C3)",
    "C5": "Trích lập quỹ phát triển khoa học và công nghệ",
    "C6": "Thu nhập tính thuế sau khi trích quỹ (C4 − C5)",
    "C7": "Thuế suất",
    "C8": "Thuế TNDN tính theo thuế suất (C6 × C7)",
    "C9": "Thuế TNDN được miễn, giảm",
    "D": "Thuế TNDN phải nộp của năm (C8 − C9)",
    "E": "Thuế TNDN đã tạm nộp trong năm",
    "F": "Thuế TNDN còn phải nộp (+) / nộp thừa (−) (D − E)",
}
REVENUE_PREFIXES = ("511", "515", "711")
COST_PREFIXES = ("632", "635", "642", "811")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _turnover(
    db: Session,
    company_id: int,
    prefix: str,
    start: date,
    end: date,
    *,
    contra=None,
) -> tuple[int, int]:
    lr = ledger_source(db, company_id, include_opening=False)
    conds = [
        account_match(lr.c.account, prefix),
        lr.c.date >= start,
        lr.c.date <= end,
        lr.c.doc_type != "KC",
    ]
    if contra:
        conds.append(account_match(lr.c.contra, contra))
    d, c = db.execute(
        select(
            func.coalesce(func.sum(lr.c.debit), 0),
            func.coalesce(func.sum(lr.c.credit), 0),
        ).where(*conds)
    ).one()
    return int(d), int(c)


@dataclass
class ProfitLoss:
    revenue: int  # net 511 (after revenue deductions)
    financial_income: int
    other_income: int
    cogs: int
    financial_cost: int
    operating_cost: int
    other_cost: int

    @property
    def total_revenue(self) -> int:
        return self.revenue + self.financial_income + self.other_income

    @property
    def profit_before_tax(self) -> int:
        return (
            self.total_revenue
            - self.cogs
            - self.financial_cost
            - self.operating_cost
            - self.other_cost
        )


def profit_and_loss(
    db: Session, company_id: int, start: date, end: date
) -> ProfitLoss:
    def net_credit(p):
        d, c = _turnover(db, company_id, p, start, end)
        return c - d

    def net_debit(p):
        d, c = _turnover(db, company_id, p, start, end)
        return d - c

    return ProfitLoss(
        revenue=net_credit("511"),
        financial_income=net_credit("515"),
        other_income=net_credit("711"),
        cogs=net_debit("632"),
        financial_cost=net_debit("635"),
        operating_cost=net_debit("642"),
        other_cost=net_debit("811"),
    )


def rate_for_revenue(prev_revenue: int | None) -> int:
    if prev_revenue is None:
        return 20
    if prev_revenue <= 3_000_000_000:
        return 15
    if prev_revenue <= 50_000_000_000:
        return 17
    return 20


def compute(
    db: Session,
    company_id: int,
    year: int,
    manual: dict | None = None,
    rate: int | None = None,
) -> dict:
    m = {k: int((manual or {}).get(k) or 0) for k in MANUAL}
    start, end = date(year, 1, 1), date(year, 12, 31)
    pl = profit_and_loss(db, company_id, start, end)
    prev = profit_and_loss(
        db, company_id, date(year - 1, 1, 1), date(year - 1, 12, 31)
    )
    auto_rate = rate_for_revenue(prev.total_revenue)
    r = rate if rate in (15, 17, 20, 25) else auto_rate
    v: dict = {"A1": pl.profit_before_tax, **m}
    v["B12"] = v["A1"] + v["B1"] - v["B8"]
    v["C4"] = max(0, v["B12"] - v["C2"] - v["C3"])
    v["C5"] = min(v["C5"], v["C4"])
    v["C6"] = v["C4"] - v["C5"]
    v["C7"] = r
    v["C8"] = (v["C6"] * r + 50) // 100
    v["C9"] = min(v["C9"], v["C8"])
    v["D"] = v["C8"] - v["C9"]
    # Provisional payments: debit 3334 against cash (111, 112) during
    # the year.
    paid, _ = _turnover(
        db, company_id, "3334", start, end, contra=("111", "112")
    )
    v["E"] = paid
    v["F"] = v["D"] - v["E"]
    v["_pl"] = pl.__dict__ | {"total_revenue": pl.total_revenue}
    v["_prev_revenue"] = prev.total_revenue
    v["_auto_rate"] = auto_rate
    v["_rate_override"] = rate if rate in (15, 17, 20, 25) else None
    return v


def get(db: Session, company_id: int, year: int) -> TaxDeclaration | None:
    return db.scalar(
        select(TaxDeclaration).where(
            TaxDeclaration.company_id == company_id,
            TaxDeclaration.form == FORM_CIT,
            TaxDeclaration.year == year,
            TaxDeclaration.period_type == "year",
            TaxDeclaration.period == 0,
            TaxDeclaration.version == 0,
        )
    )


def build(
    db: Session, company_id: int, year: int
) -> tuple[dict, TaxDeclaration | None]:
    decl = get(db, company_id, year)
    if decl is not None and decl.status != "draft":
        return dict(decl.values), decl
    stored = (decl.values or {}) if decl else {}
    return compute(
        db, company_id, year, stored.get("manual"), stored.get("rate")
    ), decl


def save(
    db: Session,
    ctx,
    year: int,
    manual: dict,
    rate: int | None,
    finalize: bool = False,
) -> TaxDeclaration:
    decl = get(db, ctx.company_id, year)
    if decl is not None and decl.status != "draft":
        raise BusinessError(f"Bảng tính thuế TNDN năm {year} đã chốt")
    if any(int(manual.get(k) or 0) < 0 for k in MANUAL):
        raise BusinessError("Số điều chỉnh không được âm")
    if decl is None:
        decl = TaxDeclaration(
            company_id=ctx.company_id,
            form=FORM_CIT,
            period_type="year",
            year=year,
            period=0,
            version=0,
            status="draft",
            created_by=ctx.user_id,
            created_at=_now(),
        )
        db.add(decl)
    stored = {
        "manual": {k: int(manual.get(k) or 0) for k in MANUAL},
        "rate": rate,
    }
    values = compute(db, ctx.company_id, year, stored["manual"], rate)
    decl.values = (
        values | {"manual": stored["manual"], "rate": rate}
        if finalize
        else stored
    )
    decl.updated_at = _now()
    if finalize:
        decl.status = "final"
        decl.finalized_by = ctx.user_id
        decl.finalized_at = _now()
    write_audit(
        db,
        ctx,
        action="vat.cit_final" if finalize else "vat.cit_save",
        entity_type="tax_declaration",
        entity_id=f"{FORM_CIT} {year}",
        summary=("Chốt" if finalize else "Lưu")
        + f" bảng tính thuế TNDN năm {year}",
    )
    db.commit()
    return decl


def reopen(db: Session, ctx, year: int) -> None:
    decl = get(db, ctx.company_id, year)
    if decl is None:
        return
    values = decl.values or {}
    decl.values = {
        "manual": values.get("manual", {}),
        "rate": values.get("rate"),
    }
    decl.status = "draft"
    write_audit(
        db,
        ctx,
        action="vat.cit_reopen",
        entity_type="tax_declaration",
        entity_id=f"{FORM_CIT} {year}",
        summary=f"Mở lại bảng tính thuế TNDN năm {year}",
    )
    db.commit()
