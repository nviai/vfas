"""VAT: purchase/sales invoice lists and the 01/GTGT return.

Credit (deduction) method, Circular 80/2021/TT-BTC as amended by
Circular 40/2025/TT-BTC.

Periods follow the POSTING DATE of posted documents (see
docs/requests/vat.md). The single data source is the `vat_records`
table of posted documents (purchases, sales, cash payments with an
invoice, ...).

Indicators (formulas per form 01/GTGT):
  [22] = [43] of the previous period; [23]/[24] value/VAT of goods and
  services purchased (incl. imports [23a]/[24a]); [25] deductible VAT
  [26] not subject to VAT; [29] 0%; [30]/[31] 5%; [32]/[33] 10%
  (incl. 8% under Decree 174/2025/ND-CP); [32a] not required to be
  declared and paid
  [27] = [29]+[30]+[32]+[32a]; [28] = [31]+[33]; [34] = [26]+[27];
  [35] = [28]; [36] = [35]-[25]
  [37] decrease / [38] increase adjustments of VAT still deductible
  from previous periods; [39a] deductible VAT received on transfer
  X = [36]-[22]+[37]-[38]-[39a]; [40a] = X if X >= 0;
  [40] = [40a]-[40b]; [41] = -X if X < 0; [43] = [41]-[42]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.dates import month_bounds
from app.core.errors import BusinessError, NotFoundError
from app.features.journal import queries
from app.features.journal import service as js
from app.features.journal.models import POSTED, JournalEntry, VatRecord
from app.features.org.models import Company
from app.features.vat.models import FORM_VAT, TaxDeclaration

MANUAL_FIELDS = ("37", "38", "39a", "40b", "42")
# Indicators compared in a supplementary return.
KHBS_KEYS = (
    "22", "23", "23a", "24", "24a", "25", "26", "27", "28", "29", "30",
    "31", "32", "32a", "33", "34", "35", "36", "37", "38", "39a", "40a",
    "40b", "40", "41", "42", "43",
)  # fmt: skip
LABELS = {
    "21": "Không phát sinh hoạt động mua, bán trong kỳ",
    "22": "Thuế GTGT còn được khấu trừ kỳ trước chuyển sang",
    "23": "Giá trị hàng hóa, dịch vụ mua vào",
    "23a": "Trong đó: hàng hóa, dịch vụ nhập khẩu",
    "24": "Thuế GTGT của hàng hóa, dịch vụ mua vào",
    "24a": "Trong đó: thuế GTGT hàng hóa, dịch vụ nhập khẩu",
    "25": "Thuế GTGT của hàng hóa, dịch vụ mua vào được khấu trừ kỳ này",
    "26": "Hàng hóa, dịch vụ bán ra không chịu thuế GTGT",
    "27": "Hàng hóa, dịch vụ bán ra chịu thuế GTGT",
    "28": "Thuế GTGT của hàng hóa, dịch vụ bán ra",
    "29": "Hàng hóa, dịch vụ bán ra chịu thuế suất 0%",
    "30": "Hàng hóa, dịch vụ bán ra chịu thuế suất 5% – giá trị",
    "31": "Hàng hóa, dịch vụ bán ra chịu thuế suất 5% – thuế GTGT",
    "32": "Hàng hóa, dịch vụ bán ra chịu thuế suất 10% – giá trị",
    "33": "Hàng hóa, dịch vụ bán ra chịu thuế suất 10% – thuế GTGT",
    "32a": "Hàng hóa, dịch vụ bán ra không phải kê khai, tính nộp thuế GTGT",
    "34": "Tổng doanh thu hàng hóa, dịch vụ bán ra",
    "35": "Tổng thuế GTGT của hàng hóa, dịch vụ bán ra",
    "36": "Thuế GTGT phát sinh trong kỳ",
    "37": "Điều chỉnh giảm thuế GTGT còn được khấu trừ của các kỳ trước",
    "38": "Điều chỉnh tăng thuế GTGT còn được khấu trừ của các kỳ trước",
    "39a": "Thuế GTGT nhận bàn giao được khấu trừ trong kỳ",
    "40a": "Thuế GTGT phải nộp của hoạt động sản xuất kinh doanh trong kỳ",
    "40b": (
        "Thuế GTGT mua vào của dự án đầu tư được bù trừ với thuế GTGT phải nộp"
    ),
    "40": "Thuế GTGT còn phải nộp trong kỳ",
    "41": "Thuế GTGT chưa khấu trừ hết kỳ này",
    "42": "Thuế GTGT đề nghị hoàn",
    "43": "Thuế GTGT còn được khấu trừ chuyển kỳ sau",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------- tax periods


@dataclass(frozen=True)
class TaxPeriod:
    period_type: str  # month | quarter
    year: int
    period: int

    @property
    def start(self) -> date:
        m = (
            self.period
            if self.period_type == "month"
            else (self.period - 1) * 3 + 1
        )
        return date(self.year, m, 1)

    @property
    def end(self) -> date:
        m = self.period if self.period_type == "month" else self.period * 3
        return month_bounds(self.year, m)[1]

    @property
    def label(self) -> str:
        return (
            f"Tháng {self.period:02d}/{self.year}"
            if self.period_type == "month"
            else f"Quý {self.period}/{self.year}"
        )

    @property
    def code(self) -> str:
        """Period code used in HTKK XML.

        07/2026 for a month, 3/2026 for a quarter.
        """
        return (
            f"{self.period:02d}/{self.year}"
            if self.period_type == "month"
            else f"{self.period}/{self.year}"
        )

    def previous(self) -> TaxPeriod:
        limit = 12 if self.period_type == "month" else 4
        if self.period > 1:
            return TaxPeriod(self.period_type, self.year, self.period - 1)
        return TaxPeriod(self.period_type, self.year - 1, limit)

    def next(self) -> TaxPeriod:
        limit = 12 if self.period_type == "month" else 4
        if self.period < limit:
            return TaxPeriod(self.period_type, self.year, self.period + 1)
        return TaxPeriod(self.period_type, self.year + 1, 1)


def deadline(period: TaxPeriod) -> date:
    """Filing deadline moved off a weekend: the 20th of the next month,
    or the last day of the first month of the next quarter.
    """
    from app.features.vat.calendar import next_workday

    nxt = period.end + timedelta(days=1)
    if period.period_type == "month":
        return next_workday(nxt.replace(day=20))
    return next_workday(month_bounds(nxt.year, nxt.month)[1])


def period_of(company: Company, d: date) -> TaxPeriod:
    if company.vat_period == "month":
        return TaxPeriod("month", d.year, d.month)
    return TaxPeriod("quarter", d.year, (d.month - 1) // 3 + 1)


def parse_period(
    company: Company, period_type: str | None, year, period
) -> TaxPeriod:
    pt = (
        period_type
        if period_type in ("month", "quarter")
        else company.vat_period
    )
    try:
        y, p = int(year), int(period)
    except (TypeError, ValueError) as e:
        raise BusinessError("Kỳ tính thuế không hợp lệ") from e
    limit = 12 if pt == "month" else 4
    if not (1 <= p <= limit and 2000 <= y <= 2100):
        raise BusinessError("Kỳ tính thuế không hợp lệ")
    return TaxPeriod(pt, y, p)


# -------------------------------------------------------- invoice lists


def records(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    direction: str | None = None,
):
    """(VatRecord, JournalEntry) pairs of posted documents in the
    period, by posting date.
    """
    stmt = (
        select(VatRecord, JournalEntry)
        .join(JournalEntry, JournalEntry.id == VatRecord.entry_id)
        .where(
            VatRecord.company_id == company_id,
            JournalEntry.status == POSTED,
            JournalEntry.date >= start,
            JournalEntry.date <= end,
        )
    )
    if direction:
        stmt = stmt.where(VatRecord.direction == direction)
    return list(
        db.execute(
            stmt.order_by(
                JournalEntry.date, VatRecord.invoice_date, VatRecord.id
            )
        ).all()
    )


def signed(rec: VatRecord) -> int:
    return -1 if rec.group == "adjust_down" else 1


@dataclass
class ListSummary:
    rows: list = field(default_factory=list)
    goods: int = 0
    vat: int = 0
    by_rate: dict = field(default_factory=dict)


def vat_list(
    db: Session, company_id: int, start: date, end: date, direction: str
) -> ListSummary:
    s = ListSummary()
    for rec, entry in records(db, company_id, start, end, direction):
        sign = signed(rec)
        s.rows.append((rec, entry, sign))
        s.goods += sign * rec.goods_amount
        s.vat += sign * rec.vat_amount
        g = s.by_rate.setdefault(rec.vat_rate, [0, 0])
        g[0] += sign * rec.goods_amount
        g[1] += sign * rec.vat_amount
    return s


# ------------------------------------------------------- 01/GTGT return


def compute(
    db: Session,
    company_id: int,
    start: date,
    end: date,
    *,
    carried: int = 0,
    manual: dict | None = None,
) -> dict:
    """Compute the 01/GTGT indicators (amounts in VND)."""
    m = {k: int((manual or {}).get(k) or 0) for k in MANUAL_FIELDS}
    v: dict = {k: 0 for k in LABELS if k != "21"}
    v["22"] = int(carried or 0)
    reduced_8 = []  # 8% lines for the tax reduction appendix
    for rec, entry in records(db, company_id, start, end):
        sign = signed(rec)
        g, t = sign * rec.goods_amount, sign * rec.vat_amount
        if rec.direction == "in":
            v["23"] += g
            v["24"] += t
            if rec.group == "import":
                v["23a"] += g
                v["24a"] += t
            if rec.deductible:
                v["25"] += t
            continue
        rate = rec.vat_rate
        if rate == "KCT":
            v["26"] += g
        elif rate == "0":
            v["29"] += g
        elif rate == "5":
            v["30"] += g
            v["31"] += t
        elif rate in ("10", "8"):
            v["32"] += g
            v["33"] += t
            if rate == "8":
                reduced_8.append((rec, entry, sign))
        elif rate == "KKKNT":
            v["32a"] += g
        else:
            # Any other rate: report it under 10% so nothing is
            # missed; a warning is raised for it.
            v["32"] += g
            v["33"] += t
    v["27"] = v["29"] + v["30"] + v["32"] + v["32a"]
    v["28"] = v["31"] + v["33"]
    v["34"] = v["26"] + v["27"]
    v["35"] = v["28"]
    v["36"] = v["35"] - v["25"]
    v.update({k: m[k] for k in MANUAL_FIELDS})
    x = v["36"] - v["22"] + v["37"] - v["38"] - v["39a"]
    v["40a"] = x if x >= 0 else 0
    v["40b"] = min(v["40b"], v["40a"])
    v["40"] = v["40a"] - v["40b"]
    v["41"] = -x if x < 0 else 0
    v["42"] = min(v["42"], v["41"])
    v["43"] = v["41"] - v["42"]
    v["21"] = not any(v[k] for k in ("23", "24", "26", "27", "28"))
    v["_reduced"] = [
        {
            "description": r.description,
            "goods": s * r.goods_amount,
            "invoice": r.invoice_no,
            "series": r.invoice_series,
            "date": r.invoice_date.isoformat() if r.invoice_date else None,
            "reduced": s * round(r.goods_amount * 2 / 100),
        }
        for r, _e, s in reduced_8
    ]
    return v


def get_declaration(
    db: Session, company_id: int, period: TaxPeriod, version: int = 0
) -> TaxDeclaration | None:
    return db.scalar(
        select(TaxDeclaration).where(
            TaxDeclaration.company_id == company_id,
            TaxDeclaration.form == FORM_VAT,
            TaxDeclaration.period_type == period.period_type,
            TaxDeclaration.year == period.year,
            TaxDeclaration.period == period.period,
            TaxDeclaration.version == version,
        )
    )


def carried_from_previous(
    db: Session, company_id: int, period: TaxPeriod
) -> tuple[int, str]:
    """[22] = [43] of the immediately preceding period's return.

    Uses the latest version. Returns (amount, source).
    """
    prev = period.previous()
    row = db.scalar(
        select(TaxDeclaration)
        .where(
            TaxDeclaration.company_id == company_id,
            TaxDeclaration.form == FORM_VAT,
            TaxDeclaration.period_type == prev.period_type,
            TaxDeclaration.year == prev.year,
            TaxDeclaration.period == prev.period,
        )
        .order_by(TaxDeclaration.version.desc())
    )
    if row is None:
        return 0, ""
    return int((row.values or {}).get("43") or 0), f"{prev.label}" + (
        " (đã chốt)" if row.status != "draft" else " (nháp)"
    )


def build(
    db: Session,
    company_id: int,
    period: TaxPeriod,
    decl: TaxDeclaration | None = None,
) -> dict:
    """Return figures: stored manual values/[22] if present, the rest
    computed from the ledger.
    """
    if decl is not None and decl.status != "draft":
        return dict(decl.values)
    stored = (decl.values or {}) if decl else {}
    manual = stored.get("manual", {})
    if "22_override" in stored:
        carried, source = int(stored["22_override"]), "nhập tay"
    else:
        carried, source = carried_from_previous(db, company_id, period)
    v = compute(
        db,
        company_id,
        period.start,
        period.end,
        carried=carried,
        manual=manual,
    )
    v["manual"] = manual
    v["_22_source"] = source
    if "22_override" in stored:
        v["22_override"] = stored["22_override"]
    return v


def save(
    db: Session,
    ctx,
    period: TaxPeriod,
    *,
    manual: dict,
    carried_override: int | None,
    finalize: bool = False,
) -> TaxDeclaration:
    decl = get_declaration(db, ctx.company_id, period)
    if decl is not None and decl.status != "draft":
        raise BusinessError(
            f"Tờ khai {period.label} đã chốt. "
            "Mở lại để sửa (hoặc lập tờ khai bổ sung)."
        )
    if decl is None:
        decl = TaxDeclaration(
            company_id=ctx.company_id,
            form=FORM_VAT,
            period_type=period.period_type,
            year=period.year,
            period=period.period,
            version=0,
            values={},
            status="draft",
            created_by=ctx.user_id,
            created_at=_now(),
        )
        db.add(decl)
    stored = {"manual": {k: int(manual.get(k) or 0) for k in MANUAL_FIELDS}}
    if carried_override is not None:
        stored["22_override"] = int(carried_override)
    decl.values = stored
    values = build(db, ctx.company_id, period, decl)
    for k in MANUAL_FIELDS:
        if values[k] < 0:
            raise BusinessError(f"Chỉ tiêu [{k}] không được âm")
    if values["manual"].get("42", 0) > values["41"]:
        raise BusinessError("Thuế đề nghị hoàn [42] không được lớn hơn [41]")
    decl.values = values if finalize else stored | {"43": values["43"]}
    decl.updated_at = _now()
    if finalize:
        decl.status = "final"
        decl.finalized_by = ctx.user_id
        decl.finalized_at = _now()
    write_audit(
        db,
        ctx,
        action="vat.declaration_final" if finalize else "vat.declaration_save",
        entity_type="tax_declaration",
        entity_id=f"{FORM_VAT} {period.code}",
        summary=("Chốt" if finalize else "Lưu")
        + f" tờ khai 01/GTGT {period.label}: [40] {values['40']:,} – [43] "
        f"{values['43']:,}".replace(",", "."),
    )
    db.commit()
    return decl


def reopen(db: Session, ctx, period: TaxPeriod) -> TaxDeclaration:
    from app.features.vat import khbs

    decl = get_declaration(db, ctx.company_id, period)
    if decl is None:
        raise NotFoundError("Chưa có tờ khai kỳ này")
    if khbs.has_supplements(db, ctx.company_id, FORM_VAT, period):
        raise BusinessError(
            "Tờ khai đã có khai bổ sung: xóa các lần bổ sung trước khi mở lại"
        )
    nxt = get_declaration(db, ctx.company_id, period.next())
    if nxt is not None and nxt.status != "draft":
        raise BusinessError(
            f"Tờ khai {period.next().label} đã chốt "
            "(đang dùng [43] kỳ này). Mở lại kỳ sau trước."
        )
    values = decl.values or {}
    decl.values = {
        "manual": values.get("manual", {}),
        **(
            {"22_override": values["22_override"]}
            if "22_override" in values
            else {}
        ),
    }
    decl.status = "draft"
    decl.updated_at = _now()
    write_audit(
        db,
        ctx,
        action="vat.declaration_reopen",
        entity_type="tax_declaration",
        entity_id=f"{FORM_VAT} {period.code}",
        summary=f"Mở lại tờ khai 01/GTGT {period.label}",
    )
    db.commit()
    return decl


def list_declarations(db: Session, company_id: int) -> list[TaxDeclaration]:
    return list(
        db.scalars(
            select(TaxDeclaration)
            .where(
                TaxDeclaration.company_id == company_id,
                TaxDeclaration.form == FORM_VAT,
            )
            .order_by(TaxDeclaration.year.desc(), TaxDeclaration.period.desc())
        )
    )


# ------------------------------------------------ supplementary returns


def recompute(
    db: Session, company_id: int, period: TaxPeriod, decl: TaxDeclaration
) -> dict:
    """The return of a filed version recomputed from the ledger, keeping
    its manual indicators and the [22] it declared.
    """
    stored = decl.values or {}
    manual = stored.get("manual", {})
    v = compute(
        db,
        company_id,
        period.start,
        period.end,
        carried=int(stored.get("22") or 0),
        manual=manual,
    )
    v["manual"] = manual
    v["_22_source"] = "đã kê khai"
    return v


def pending_changes(db: Session, company_id: int, period: TaxPeriod) -> list:
    """Differences between the ledger and the latest filed version."""
    from app.features.vat import khbs

    rows = khbs.versions(db, company_id, FORM_VAT, period)
    if not rows or rows[0].status == "draft":
        return []
    latest = rows[-1]
    return khbs.compare(
        FORM_VAT,
        latest.values,
        recompute(db, company_id, period, latest),
        KHBS_KEYS,
        LABELS,
    )


def supplement(
    db: Session, ctx, period: TaxPeriod, *, reason: str, until: date
) -> TaxDeclaration:
    from app.features.vat import khbs

    rows = khbs.versions(db, ctx.company_id, FORM_VAT, period)
    if not rows or rows[0].status == "draft":
        raise BusinessError(f"Chưa chốt tờ khai lần đầu {period.label}")
    return khbs.create(
        db,
        ctx,
        FORM_VAT,
        period,
        recompute(db, ctx.company_id, period, rows[-1]),
        keys=KHBS_KEYS,
        labels=LABELS,
        due=deadline(period),
        until=until,
        reason=reason,
    )


def supplements(db: Session, company_id: int, period: TaxPeriod) -> list:
    from app.features.vat import khbs

    return [
        khbs.summary_of(
            db,
            company_id,
            FORM_VAT,
            period,
            d.version,
            KHBS_KEYS,
            LABELS,
            deadline(period),
        )
        for d in khbs.versions(db, company_id, FORM_VAT, period)
        if d.version > 0
    ]


# ------------------------------------------------ period-end VAT offset


def offset_amounts(
    db: Session, company_id: int, end: date
) -> tuple[int, int, int]:
    """(debit balance 1331 + 1332, credit balance 33311, offset = min)
    at the end of day `end`.
    """
    deductible = max(0, queries.balance(db, company_id, "133", upto=end))
    output = max(
        0, -queries.balance(db, company_id, "33311", exact=True, upto=end)
    )
    return deductible, output, min(deductible, output)


def create_offset_entry(db: Session, ctx, period: TaxPeriod) -> JournalEntry:
    """Create the period-end VAT offset entry (draft): debit 33311 /
    credit 1331 (and 1332).
    """
    d1331 = max(
        0,
        queries.balance(
            db, ctx.company_id, "1331", exact=True, upto=period.end
        ),
    )
    d1332 = max(
        0,
        queries.balance(
            db, ctx.company_id, "1332", exact=True, upto=period.end
        ),
    )
    output = max(
        0,
        -queries.balance(
            db, ctx.company_id, "33311", exact=True, upto=period.end
        ),
    )
    if not output or not (d1331 or d1332):
        raise BusinessError(
            "Không có số thuế để khấu trừ (dư Có 33311 hoặc dư Nợ 133 bằng 0)"
        )
    lines = []
    remaining = output
    for acc, bal in (("1331", d1331), ("1332", d1332)):
        amt = min(bal, remaining)
        if amt > 0:
            lines.append(
                js.LineIn(
                    debit_account="33311",
                    credit_account=acc,
                    amount=amt,
                    description=f"Khấu trừ thuế GTGT {period.label}",
                )
            )
            remaining -= amt
    return js.create_entry(
        db,
        ctx,
        js.EntryIn(
            doc_type="PK",
            date=period.end,
            lines=lines,
            description=f"Khấu trừ thuế GTGT đầu vào {period.label}",
            source="vat",
            meta={"vat_period": period.code},
        ),
    )
