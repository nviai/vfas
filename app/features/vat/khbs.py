"""Supplementary returns ("khai bổ sung"): 01/KHBS and 01-1/KHBS.

A supplementary return is the whole return again with the corrected
figures ("Bổ sung lần thứ n", HTKK `loaiTKhai` B and `soLan` n) plus
two appendices:
- 01/KHBS (`PL01_KHBS`): the increase/decrease of the tax payable per
  budget sub-item, of the deductible and of the refund amount, and the
  late-payment interest on an increase;
- 01-1/KHBS (`PL01_1_KHBS`): each corrected indicator with the
  declared figure, the corrected figure, the difference and the
  reason.

The appendix structure follows the HTKK schemas (identical in
KHBS_01_GTGT_TT80_283, KHBS_05_KK_TNCN_TT80_293 and
KHBS_03_TNDN_TT80_294). The keys HTKK writes into the free-text code
fields (`ct2_ma`/`ct3_ma` of 01-1/KHBS, the currency code) are not in
the schemas: VFAS writes the indicator element name and "[code]"
(see docs/DECISIONS.md D-v2-33), so the file must be opened in HTKK
before filing.

Late-payment interest: 0.03% per day of the increase (Law on Tax
Administration 108/2025/QH15, Article 16(2)(a)), counted from the day
after the original deadline to the day before payment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import htkk
from app.core.audit import write_audit
from app.core.errors import BusinessError, NotFoundError
from app.core.htkk import el, nil, num, tin
from app.features.vat.models import FORM_PIT, FORM_VAT, TaxDeclaration
from app.features.vat.service import TaxPeriod

LATE_RATE = Decimal("0.0003")
# Budget sub-items ("tiểu mục") of the tax on each return.
SUB_ITEMS = {
    FORM_VAT: ("1701", "Thuế giá trị gia tăng hàng sản xuất, kinh doanh "
               "trong nước"),
    FORM_PIT: ("1001", "Thuế thu nhập cá nhân từ tiền lương, tiền công"),
}  # fmt: skip
# Indicators carrying the obligation: (payable, deductible, refund).
EFFECTS = {
    FORM_VAT: ("40", "43", "42"),
    FORM_PIT: ("29", None, None),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Change:
    code: str
    label: str
    declared: int
    adjusted: int
    # Effect on the obligation: "payable", "deductible" or "refund".
    effect: str | None = None

    @property
    def diff(self) -> int:
        return self.adjusted - self.declared


@dataclass
class Supplement:
    form: str
    period: TaxPeriod
    version: int
    changes: list[Change] = field(default_factory=list)
    payable: int = 0
    deductible: int = 0
    refund: int = 0
    due: date | None = None
    until: date | None = None
    days: int = 0
    interest: int = 0
    reason: str = ""


def compare(
    form: str, declared: dict, adjusted: dict, keys, labels: dict
) -> list[Change]:
    """Indicators whose figure differs between two versions."""
    payable, deductible, refund = EFFECTS[form]
    effect = {payable: "payable", deductible: "deductible", refund: "refund"}
    out = []
    for k in keys:
        old, new = int(declared.get(k) or 0), int(adjusted.get(k) or 0)
        if old != new:
            out.append(Change(k, labels.get(k, k), old, new, effect.get(k)))
    return out


def late_interest(amount: int, due: date, until: date) -> tuple[int, int]:
    """(days, interest) on an increase of tax payable."""
    days = max(0, (until - due).days - 1)
    if amount <= 0 or not days:
        return 0, 0
    value = Decimal(amount) * LATE_RATE * days
    return days, int(value.quantize(Decimal(1), ROUND_HALF_UP))


def summarize(
    form: str,
    period: TaxPeriod,
    version: int,
    declared: dict,
    adjusted: dict,
    keys,
    labels: dict,
    *,
    due: date,
    until: date,
    reason: str = "",
) -> Supplement:
    s = Supplement(form, period, version, due=due, until=until)
    s.reason = reason
    s.changes = compare(form, declared, adjusted, keys, labels)
    for c in s.changes:
        if c.effect == "payable":
            s.payable += c.diff
        elif c.effect == "deductible":
            s.deductible += c.diff
        elif c.effect == "refund":
            s.refund += c.diff
    s.days, s.interest = late_interest(s.payable, due, until)
    return s


# ---------------------------------------------------------- storage


def versions(
    db: Session, company_id: int, form: str, period: TaxPeriod
) -> list[TaxDeclaration]:
    return list(
        db.scalars(
            select(TaxDeclaration)
            .where(
                TaxDeclaration.company_id == company_id,
                TaxDeclaration.form == form,
                TaxDeclaration.period_type == period.period_type,
                TaxDeclaration.year == period.year,
                TaxDeclaration.period == period.period,
            )
            .order_by(TaxDeclaration.version)
        )
    )


def has_supplements(
    db: Session, company_id: int, form: str, period: TaxPeriod
) -> bool:
    n = db.scalar(
        select(func.count(TaxDeclaration.id)).where(
            TaxDeclaration.company_id == company_id,
            TaxDeclaration.form == form,
            TaxDeclaration.period_type == period.period_type,
            TaxDeclaration.year == period.year,
            TaxDeclaration.period == period.period,
            TaxDeclaration.version > 0,
        )
    )
    return bool(n)


def create(
    db: Session,
    ctx,
    form: str,
    period: TaxPeriod,
    values: dict,
    *,
    keys,
    labels: dict,
    due: date,
    until: date,
    reason: str,
) -> TaxDeclaration:
    """Store the next supplementary version of a finalized return."""
    rows = versions(db, ctx.company_id, form, period)
    if not rows or rows[0].status == "draft":
        raise BusinessError(f"Chưa chốt tờ khai lần đầu {form} {period.label}")
    reason = (reason or "").strip()
    if not reason:
        raise BusinessError("Nhập lý do khai bổ sung")
    latest = rows[-1]
    s = summarize(
        form,
        period,
        latest.version + 1,
        latest.values,
        values,
        keys,
        labels,
        due=due,
        until=until,
        reason=reason,
    )
    if not s.changes:
        raise BusinessError(
            "Số liệu không khác lần khai gần nhất – không cần khai bổ sung"
        )
    decl = TaxDeclaration(
        company_id=ctx.company_id,
        form=form,
        period_type=period.period_type,
        year=period.year,
        period=period.period,
        version=s.version,
        values=values
        | {"_khbs": {"reason": reason, "until": until.isoformat()}},
        status="final",
        created_by=ctx.user_id,
        created_at=_now(),
        updated_at=_now(),
        finalized_by=ctx.user_id,
        finalized_at=_now(),
    )
    db.add(decl)
    write_audit(
        db,
        ctx,
        action="tax.supplement_create",
        entity_type="tax_declaration",
        entity_id=f"{form} {period.code} BS{s.version}",
        summary=f"Khai bổ sung lần {s.version} tờ khai {form} "
        f"{period.label}: phải nộp {s.payable:+,}".replace(",", "."),
    )
    db.commit()
    return decl


def delete_latest(
    db: Session, ctx, form: str, period: TaxPeriod, version: int
) -> None:
    rows = versions(db, ctx.company_id, form, period)
    if version < 1 or not rows or rows[-1].version != version:
        raise NotFoundError("Chỉ xóa được lần khai bổ sung gần nhất")
    db.delete(rows[-1])
    write_audit(
        db,
        ctx,
        action="tax.supplement_delete",
        entity_type="tax_declaration",
        entity_id=f"{form} {period.code} BS{version}",
        summary=f"Xóa khai bổ sung lần {version} tờ khai {form} "
        f"{period.label}",
    )
    db.commit()


def summary_of(
    db: Session,
    company_id: int,
    form: str,
    period: TaxPeriod,
    version: int,
    keys,
    labels: dict,
    due: date,
) -> Supplement:
    """Rebuild the comparison of a stored supplement with the version
    before it.
    """
    rows = {d.version: d for d in versions(db, company_id, form, period)}
    if version not in rows or version - 1 not in rows:
        raise NotFoundError("Không tìm thấy tờ khai bổ sung")
    cur = rows[version]
    meta = cur.values.get("_khbs") or {}
    until = date.fromisoformat(meta["until"]) if meta.get("until") else due
    return summarize(
        form,
        period,
        version,
        rows[version - 1].values,
        cur.values,
        keys,
        labels,
        due=due,
        until=until,
        reason=meta.get("reason", ""),
    )


# ---------------------------------------------------------------- XML


def _header(company, form: htkk.FormInfo, s: Supplement) -> str:
    p = s.period
    kind = "M" if p.period_type == "month" else "Q"
    return (
        "<Header>"
        + el("maTKhai", form.code)
        + el("tenTKhai", form.name)
        + el("maGiaoDich")
        + "<KyKKhaiThue>"
        + el("kieuKy", kind)
        + el("kyKKhai", p.code)
        + el("kyKKhaiTuNgay", p.start.strftime("%d/%m/%Y"))
        + el("kyKKhaiDenNgay", p.end.strftime("%d/%m/%Y"))
        + el("kyKKhaiTuThang")
        + el("kyKKhaiDenThang")
        + "</KyKKhaiThue>"
        + num("soLan", s.version)
        + el("mst", company.tax_code)
        + el("tenNNT", company.name)
        + tin("mstDLyThue", "")
        + el("tenDLyThue")
        + el("soHDongDLyThue")
        + nil("ngayKyHDDLyThue")
        + "</Header>"
        + el("ma_DonViTien", "VND")
        + el("ten_DonViTien", "Đồng Việt Nam")
    )


def _sub_item_rows(form: str, amount: int, total_tag: str) -> str:
    code, name = SUB_ITEMS[form]
    return (
        '<ChiTiet id="ID_1">'
        + el("ct2_ma", code)
        + el("ct2_ten", name)
        + num("ct3", amount)
        + "</ChiTiet>"
        + num(total_tag, amount)
    )


def appendices_xml(company, form: htkk.FormInfo, s: Supplement) -> str:
    """PL01_KHBS + PL01_1_KHBS for a supplementary return."""
    head = _header(company, form, s)
    interest = (
        el("ct3a_1", s.until.isoformat())
        + num("ct3a_2", s.days)
        + num("ct3b", s.interest)
        if s.payable > 0 and s.until
        else nil("ct3a_1") + nil("ct3a_2") + nil("ct3b")
    )
    pl01 = (
        "<PL01_KHBS>"
        + head
        + "<Muc_A><Muc_I><Muc_1>"
        + _sub_item_rows(s.form, s.payable, "tongCong_ct10")
        + "</Muc_1><Muc_2><DSachPLuc>"
        + '<BKePLuc id="ID_1">'
        + el("ma_PLuc")
        + el("ten_PLuc")
        + "<CTietPLuc/></BKePLuc>"
        + num("tongCong_ct11", 0)
        + "</DSachPLuc></Muc_2><Muc_3>"
        + interest
        + "</Muc_3></Muc_I><Muc_II>"
        + _sub_item_rows(s.form, s.deductible, "tongCong_ct12")
        + "</Muc_II><Muc_III>"
        + _sub_item_rows(s.form, s.refund, "tongCong_ct13")
        + "</Muc_III></Muc_A>"
        + "<Muc_B><Muc_I>"
        + nil("ct_1")
        + el("ct_2_so")
        + nil("ct_2_ngay")
        + el("ct_2_CQT_ma")
        + el("ct_2_CQT_ten")
        + el("ct_3_so")
        + nil("ct_3_ngay")
        + "</Muc_I><Muc_II>"
        + nil("ct_1")
        + nil("ct_2")
        + "</Muc_II></Muc_B>"
        + "</PL01_KHBS>"
    )
    rows = []
    for i, c in enumerate(s.changes, start=1):
        obligation = c.diff if c.effect else 0
        rows.append(
            f'<ChiTiet id="ID_{i}">'
            + el("ct2_ma", f"ct{c.code}")
            + el("ct2_ten", c.label)
            + el("ct3_ma", f"[{c.code}]")
            + el("ct3_ten", f"[{c.code}]")
            + el("ct3_1_ma")
            + el("ct3_1_ten")
            + el("ct3_2_ma")
            + el("ct3_2_ten")
            + el("ct04", c.declared)
            + el("ct05", c.adjusted)
            + el("ct06", c.diff)
            + el("ct7", obligation)
            + el("ct8", s.reason)
            + "</ChiTiet>"
        )
    pl011 = (
        "<PL01_1_KHBS>"
        + head
        + '<Muc_A><DSachHSo><BKeHSo id="ID_1">'
        + el("ma_HSo", form.code)
        + el("ten_HSo", form.name)
        + "<CTietHSo>"
        + "".join(rows)
        + "</CTietHSo></BKeHSo>"
        + "<TongCong>"
        + num("tongCong_7", s.payable)
        + num("tongCong_8", s.deductible)
        + num("tongCong_9", s.refund)
        + "</TongCong></DSachHSo></Muc_A>"
        + '<Muc_B><CTietTaiLieu id="ID_1">'
        + el("ma_TLieu")
        + el("ten_TLieu")
        + "</CTietTaiLieu></Muc_B>"
        + "</PL01_1_KHBS>"
    )
    return pl01 + pl011
