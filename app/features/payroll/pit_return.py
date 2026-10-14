"""05/KK-TNCN: return of PIT withheld from salaries and wages.

Built from posted payroll runs whose payroll month falls in the period
(the same convention as the parameters, see docs/requests/payroll.md).

Filing period:
- before 04/2026 it follows the VAT period (Decree 126/2020/ND-CP,
  Article 9(1));
- from 04/2026 it is quarterly for every employer: Resolution
  66.16/2026/NQ-CP (HTKK 5.6.8 refuses monthly periods from 04/2026),
  then Circular 89/2026/TT-BTC, Article 22(1)(a.1). The return is due
  for every quarter in which salaries are paid, even when no tax was
  withheld.

Indicators (HTKK form 864, TT80/2021 layout still used by HTKK 5.8.0):
  [16] employees paid; [17] residents with a labor contract of 3
  months or more; [18] = [19] + [20] individuals with tax withheld;
  [21] = [22] + [23] taxable income paid; [26] = [27] + [28] taxable
  income of the months in which tax was withheld; [29] = [30] + [31]
  tax withheld. [24], [25], [25_1] and [32] (foreign insurers,
  petroleum contracts, exemptions under resolutions) are not tracked
  by the payroll and stay 0.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BusinessError, NotFoundError
from app.features.journal.models import POSTED
from app.features.org.models import Company
from app.features.payroll import service
from app.features.vat import khbs
from app.features.vat.calendar import next_workday
from app.features.vat.models import FORM_PIT, TaxDeclaration
from app.features.vat.service import TaxPeriod

QUARTERLY_FROM = date(2026, 4, 1)

KEYS = (
    "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "25_1",
    "26", "27", "28", "29", "30", "31", "32",
)  # fmt: skip
LABELS = {
    "16": "Tổng số người lao động",
    "17": "Trong đó: Cá nhân cư trú có hợp đồng lao động",
    "18": "Tổng số cá nhân đã khấu trừ thuế",
    "19": "Cá nhân cư trú",
    "20": "Cá nhân không cư trú",
    "21": "Tổng thu nhập chịu thuế (TNCT) trả cho cá nhân",
    "22": "Cá nhân cư trú",
    "23": "Cá nhân không cư trú",
    "24": (
        "Trong đó: TNCT từ phí mua bảo hiểm nhân thọ, bảo hiểm không bắt "
        "buộc khác của doanh nghiệp bảo hiểm không thành lập tại Việt Nam"
    ),
    "25": "Trong đó: TNCT được miễn theo Hợp đồng dầu khí",
    "25_1": "Thu nhập được miễn thuế theo Nghị quyết",
    "26": "Tổng TNCT trả cho cá nhân thuộc diện phải khấu trừ thuế",
    "27": "Cá nhân cư trú",
    "28": "Cá nhân không cư trú",
    "29": "Tổng số thuế thu nhập cá nhân (TNCN) đã khấu trừ",
    "30": "Cá nhân cư trú",
    "31": "Cá nhân không cư trú",
    "32": (
        "Trong đó: Số thuế TNCN đã khấu trừ trên phí mua bảo hiểm nhân "
        "thọ, bảo hiểm không bắt buộc khác của doanh nghiệp bảo hiểm "
        "không thành lập tại Việt Nam"
    ),
}
COUNTS = ("16", "17", "18", "19", "20")
# Printed layout: (item no., code, formula, level). The unit is
# "Người" for COUNTS and "VNĐ" otherwise.
LAYOUT = (
    ("1", "16", "", 0),
    ("", "17", "", 1),
    ("2", "18", "[18]=[19]+[20]", 0),
    ("2.1", "19", "", 1),
    ("2.2", "20", "", 1),
    ("3", "21", "[21]=[22]+[23]", 0),
    ("3.1", "22", "", 1),
    ("3.2", "23", "", 1),
    ("", "24", "", 1),
    ("", "25", "", 1),
    ("", "25_1", "", 1),
    ("4", "26", "[26]=[27]+[28]", 0),
    ("4.1", "27", "", 1),
    ("4.2", "28", "", 1),
    ("5", "29", "[29]=[30]+[31]", 0),
    ("5.1", "30", "", 1),
    ("5.2", "31", "", 1),
    ("", "32", "", 1),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------- periods


def monthly_allowed(period: TaxPeriod) -> bool:
    return period.start < QUARTERLY_FROM


def period_of(company: Company, d: date) -> TaxPeriod:
    """Filing period containing `d`."""
    if d < QUARTERLY_FROM and company.vat_period == "month":
        return TaxPeriod("month", d.year, d.month)
    return TaxPeriod("quarter", d.year, (d.month - 1) // 3 + 1)


def previous(company: Company, p: TaxPeriod) -> TaxPeriod:
    return period_of(company, p.start - timedelta(days=1))


def following(company: Company, p: TaxPeriod) -> TaxPeriod:
    return period_of(company, p.end + timedelta(days=1))


def parse_period(period_type: str, year, period) -> TaxPeriod:
    if period_type not in ("month", "quarter"):
        raise BusinessError("Kỳ tính thuế không hợp lệ")
    try:
        y, n = int(year), int(period)
    except (TypeError, ValueError) as e:
        raise BusinessError("Kỳ tính thuế không hợp lệ") from e
    limit = 12 if period_type == "month" else 4
    if not (1 <= n <= limit and 2000 <= y <= 2100):
        raise BusinessError("Kỳ tính thuế không hợp lệ")
    p = TaxPeriod(period_type, y, n)
    if period_type == "month" and not monthly_allowed(p):
        raise BusinessError(
            "Từ kỳ tháng 04/2026, tờ khai 05/KK-TNCN khai theo quý "
            "(Nghị quyết 66.16/2026/NQ-CP, Thông tư 89/2026/TT-BTC)"
        )
    return p


def months_of(p: TaxPeriod) -> tuple[int, ...]:
    return tuple(range(p.start.month, p.end.month + 1))


def deadline(p: TaxPeriod) -> date:
    """Deadline moved off a weekend (late-payment interest starts the
    day after).
    """
    return next_workday(due_date(p))


def due_date(p: TaxPeriod) -> date:
    """Statutory deadline, before moving off a weekend: the 20th of
    the next month, or the last day of the first month of the next
    quarter.
    """
    nxt = p.end + timedelta(days=1)
    if p.period_type == "month":
        return nxt.replace(day=20)
    return nxt.replace(day=calendar.monthrange(nxt.year, nxt.month)[1])


# --------------------------------------------------------- computation


@dataclass
class PitRow:
    """One employee's figures for the period."""

    employee: object
    resident: bool
    long_contract: bool
    taxable_income: int = 0
    # Taxable income of the months in which tax was withheld.
    withheld_income: int = 0
    pit: int = 0
    months: int = 0


def collect(db: Session, company_id: int, p: TaxPeriod) -> list[PitRow]:
    rows: dict[int, PitRow] = {}
    lines = service.posted_lines(db, company_id, p.year, months=months_of(p))
    for ln in sorted(lines, key=lambda x: x.run.month):
        r = rows.get(ln.employee_id)
        if r is None:
            r = rows[ln.employee_id] = PitRow(ln.employee, True, False)
        # The latest month's snapshot decides the residence and the
        # contract type.
        r.resident = ln.is_resident
        r.long_contract = ln.is_resident and ln.contract_type == "long"
        r.taxable_income += ln.taxable_income
        r.pit += ln.pit
        if ln.pit > 0:
            r.withheld_income += ln.taxable_income
        r.months += 1
    return sorted(rows.values(), key=lambda r: r.employee.code)


def compute(rows: list[PitRow]) -> dict:
    v = {k: 0 for k in KEYS}
    v["16"] = len(rows)
    v["17"] = sum(1 for r in rows if r.long_contract)
    for r in rows:
        side = (
            ("19", "22", "27", "30")
            if r.resident
            else ("20", "23", "28", "31")
        )
        if r.pit > 0:
            v[side[0]] += 1
        v[side[1]] += r.taxable_income
        v[side[2]] += r.withheld_income
        v[side[3]] += r.pit
    v["18"] = v["19"] + v["20"]
    v["21"] = v["22"] + v["23"]
    v["26"] = v["27"] + v["28"]
    v["29"] = v["30"] + v["31"]
    return v


def live_values(db: Session, company_id: int, p: TaxPeriod) -> dict:
    return compute(collect(db, company_id, p))


def unposted_months(db: Session, company_id: int, p: TaxPeriod) -> list[str]:
    """Payroll runs of the period that are not posted (not counted)."""
    out = []
    for m in months_of(p):
        run = service.find_run(db, company_id, p.year, m)
        if run is not None and run.status != POSTED:
            out.append(run.period_label)
    return out


# ---------------------------------------------------- saved returns


def get_declaration(
    db: Session, company_id: int, p: TaxPeriod, version: int = 0
) -> TaxDeclaration | None:
    return db.scalar(
        select(TaxDeclaration).where(
            TaxDeclaration.company_id == company_id,
            TaxDeclaration.form == FORM_PIT,
            TaxDeclaration.period_type == p.period_type,
            TaxDeclaration.year == p.year,
            TaxDeclaration.period == p.period,
            TaxDeclaration.version == version,
        )
    )


def list_declarations(db: Session, company_id: int) -> list[TaxDeclaration]:
    return list(
        db.scalars(
            select(TaxDeclaration)
            .where(
                TaxDeclaration.company_id == company_id,
                TaxDeclaration.form == FORM_PIT,
            )
            .order_by(
                TaxDeclaration.year.desc(),
                TaxDeclaration.period.desc(),
                TaxDeclaration.version,
            )
        )
    )


def values_of(
    db: Session, company_id: int, p: TaxPeriod, decl: TaxDeclaration | None
) -> dict:
    """Stored figures of a finalized return, live figures otherwise."""
    if decl is not None and decl.status != "draft":
        return {k: int(decl.values.get(k) or 0) for k in KEYS}
    return live_values(db, company_id, p)


def _require_posted(db: Session, company_id: int, p: TaxPeriod) -> None:
    months = unposted_months(db, company_id, p)
    if months:
        raise BusinessError(
            "Còn bảng lương chưa ghi sổ trong kỳ: " + ", ".join(months)
        )


def finalize(db: Session, ctx, p: TaxPeriod) -> TaxDeclaration:
    decl = get_declaration(db, ctx.company_id, p)
    if decl is not None and decl.status != "draft":
        raise BusinessError(f"Tờ khai 05/KK-TNCN {p.label} đã chốt")
    _require_posted(db, ctx.company_id, p)
    values = live_values(db, ctx.company_id, p)
    if decl is None:
        decl = TaxDeclaration(
            company_id=ctx.company_id,
            form=FORM_PIT,
            period_type=p.period_type,
            year=p.year,
            period=p.period,
            version=0,
            created_by=ctx.user_id,
            created_at=_now(),
        )
        db.add(decl)
    decl.values = values
    decl.status = "final"
    decl.updated_at = decl.finalized_at = _now()
    decl.finalized_by = ctx.user_id
    write_audit(
        db,
        ctx,
        action="payroll.pit_return_final",
        entity_type="tax_declaration",
        entity_id=f"{FORM_PIT} {p.code}",
        summary=f"Chốt tờ khai 05/KK-TNCN {p.label}: [29] "
        + f"{values['29']:,}".replace(",", "."),
    )
    db.commit()
    return decl


def reopen(db: Session, ctx, p: TaxPeriod) -> None:
    decl = get_declaration(db, ctx.company_id, p)
    if decl is None or decl.status == "draft":
        raise NotFoundError("Tờ khai kỳ này chưa chốt")
    if khbs.has_supplements(db, ctx.company_id, FORM_PIT, p):
        raise BusinessError(
            "Tờ khai đã có khai bổ sung: xóa các lần bổ sung trước khi mở lại"
        )
    db.delete(decl)
    write_audit(
        db,
        ctx,
        action="payroll.pit_return_reopen",
        entity_type="tax_declaration",
        entity_id=f"{FORM_PIT} {p.code}",
        summary=f"Mở lại tờ khai 05/KK-TNCN {p.label}",
    )
    db.commit()


# ------------------------------------------------ supplementary returns


def supplement(
    db: Session, ctx, p: TaxPeriod, *, reason: str, until: date
) -> TaxDeclaration:
    """File the payroll as it is now as the next supplementary
    return.
    """
    _require_posted(db, ctx.company_id, p)
    return khbs.create(
        db,
        ctx,
        FORM_PIT,
        p,
        live_values(db, ctx.company_id, p),
        keys=KEYS,
        labels=LABELS,
        due=deadline(p),
        until=until,
        reason=reason,
    )


def supplements(
    db: Session, company_id: int, p: TaxPeriod
) -> list[khbs.Supplement]:
    return [
        khbs.summary_of(
            db, company_id, FORM_PIT, p, d.version, KEYS, LABELS, deadline(p)
        )
        for d in khbs.versions(db, company_id, FORM_PIT, p)
        if d.version > 0
    ]


def latest_values(db: Session, company_id: int, p: TaxPeriod) -> dict:
    """Figures of the latest filed version, live figures when the
    return is not finalized.
    """
    rows = khbs.versions(db, company_id, FORM_PIT, p)
    if not rows or rows[0].status == "draft":
        return live_values(db, company_id, p)
    return {k: int(rows[-1].values.get(k) or 0) for k in KEYS}


def export(
    db: Session, company_id: int, p: TaxPeriod, version: int = 0
) -> tuple[dict, khbs.Supplement | None]:
    """Figures of one version and, for a supplement, its comparison."""
    if version == 0:
        decl = get_declaration(db, company_id, p)
        return values_of(db, company_id, p, decl), None
    decl = get_declaration(db, company_id, p, version)
    if decl is None:
        raise NotFoundError("Không tìm thấy tờ khai bổ sung")
    s = khbs.summary_of(
        db, company_id, FORM_PIT, p, version, KEYS, LABELS, deadline(p)
    )
    return {k: int(decl.values.get(k) or 0) for k in KEYS}, s
