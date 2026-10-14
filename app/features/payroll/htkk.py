"""05/QTT-TNCN (PIT finalization return of the employer) as HTKK XML.

HTKK form 953, XML version 2.9.3, checked against the official schema
05_QTT_TNCN_TT80_293.xsd (the "địa bàn hành chính" envelope: ward
name/code instead of phuongXa).

Built from posted payroll runs of the year:
- appendix 05-1/BK-QTT-TNCN: residents with a labor contract of 3
  months or more (progressive schedule);
- appendix 05-2/BK-QTT-TNCN: everyone taxed at a flat rate (short
  contracts, probation, no contract, non-residents).
Appendix 05-3 (dependants) needs names, birth dates and ID numbers of
dependants, which the payroll does not store: add it in HTKK. The
company does not settle on behalf of employees here (indicators
[35]–[41] are 0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.core import htkk
from app.core.htkk import el, num, tin
from app.features.org.models import Company
from app.features.payroll import service

FORM = htkk.FormInfo(
    code="953",
    name=("TỜ KHAI QUYẾT TOÁN THUẾ THU NHẬP CÁ NHÂN (Mẫu số 05/QTT-TNCN)"),
    note=(
        "(Ban hành kèm theo Thông tư số 80/2021/TT-BTC ngày 29 tháng 9 "
        "năm 2021 của Bộ trưởng Bộ Tài chính)"
    ),
    version="2.9.3",
    dbhc=True,
)


def progressive(s) -> bool:
    e = s.employee
    return e.is_resident and e.contract_type == "long"


@dataclass
class Settlement:
    year: int
    progressive: list = field(default_factory=list)
    flat: list = field(default_factory=list)

    @property
    def everyone(self) -> list:
        return self.progressive + self.flat


def collect(db: Session, company_id: int, year: int) -> Settlement:
    st = Settlement(year)
    for s in service.year_summary(db, company_id, year):
        (st.progressive if progressive(s) else st.flat).append(s)
    return st


def _dependents(s) -> int:
    return max((ln.dependents for ln in s.months.values()), default=0)


def _row_05_1(i: int, s) -> str:
    e = s.employee
    return (
        f'<BKeCTietCNhan id="ID_{i}">'
        + el("ct07", e.full_name)
        + tin("ct08", e.partner.tax_code if e.partner else "")
        + num("nguoiVNSongNN_NguoiNN", 0)
        + el("ct09a_ma")
        + el("ct09a_ten")
        + el("ct09")
        + num("ct10", 0)
        + num("ct11", 0)
        + num("ct12", s.taxable_income)
        + num("ct13", 0)
        + num("ct14", 0)
        + num("ct15", 0)
        + num("ct15.1", 0)
        + num("ct16", _dependents(s))
        + num("ct17", s.self_deduction + s.dependent_deduction)
        + num("ct18", 0)
        + num("ct19", s.emp_insurance)
        + num("ct20", 0)
        + num("ct21", s.assessable_income)
        + num("ct22", s.pit)
        + "".join(num(f"ct{n}", 0) for n in range(23, 28))
        + "</BKeCTietCNhan>"
    )


def _appendix_05_1(rows: list) -> str:
    if not rows:
        return ""
    totals = {
        "ct28": sum(s.taxable_income for s in rows),
        "ct32": sum(_dependents(s) for s in rows),
        "ct33": sum(s.self_deduction + s.dependent_deduction for s in rows),
        "ct35": sum(s.emp_insurance for s in rows),
        "ct37": sum(s.assessable_income for s in rows),
        "ct38": sum(s.pit for s in rows),
    }
    order = (
        "ct28", "ct29", "ct30", "ct31", "ct31.1", "ct32", "ct33", "ct34",
        "ct35", "ct36", "ct37", "ct38", "ct39", "ct40", "ct41", "ct42",
        "ct43",
    )  # fmt: skip
    return (
        "<PLuc_05_1_BK_QTT>"
        + "".join(_row_05_1(i, s) for i, s in enumerate(rows, start=1))
        + "".join(num(k, totals.get(k, 0)) for k in order)
        + "</PLuc_05_1_BK_QTT>"
    )


def _appendix_05_2(rows: list) -> str:
    if not rows:
        return ""
    body = []
    for i, s in enumerate(rows, start=1):
        e = s.employee
        body.append(
            f'<BKeCTietCNhan id="ID_{i}">'
            + el("ct07", e.full_name)
            + tin("ct08", e.partner.tax_code if e.partner else "")
            + num("nguoiVNSongNN_NguoiNN", 0)
            + el("ct09a_ma")
            + el("ct09a_ten")
            + el("ct09")
            + num("ct10", 0 if e.is_resident else 1)
            + num("ct11", s.taxable_income)
            + num("ct12", 0)
            + num("ct13", 0)
            + num("ct14", 0)
            + num("ct14.1", 0)
            + num("ct15", s.pit)
            + num("ct16", 0)
            + "</BKeCTietCNhan>"
        )
    return (
        "<PLuc_05_2_BK_QTT>"
        + "".join(body)
        + num("ct17", sum(s.taxable_income for s in rows))
        + "".join(num(k, 0) for k in ("ct18", "ct19", "ct20", "ct20.1"))
        + num("ct21", sum(s.pit for s in rows))
        + num("ct22", 0)
        + "</PLuc_05_2_BK_QTT>"
    )


def main_values(st: Settlement) -> dict:
    people = st.everyone
    resident = [s for s in people if s.employee.is_resident]
    foreign = [s for s in people if not s.employee.is_resident]
    withheld = [s for s in people if s.pit > 0]
    return {
        "16": len(people),
        "17": len(st.progressive),
        "18": len(withheld),
        "19": len([s for s in withheld if s.employee.is_resident]),
        "20": len([s for s in withheld if not s.employee.is_resident]),
        "21": 0,
        "22": len([s for s in people if s.self_deduction > 0]),
        "23": sum(s.taxable_income for s in people),
        "24": sum(s.taxable_income for s in resident),
        "25": sum(s.taxable_income for s in foreign),
        "26": 0,
        "27": 0,
        "28": sum(s.taxable_income for s in withheld),
        "29": sum(
            s.taxable_income for s in withheld if s.employee.is_resident
        ),
        "30": sum(
            s.taxable_income for s in withheld if not s.employee.is_resident
        ),
        "31": sum(s.pit for s in people),
        "32": sum(s.pit for s in resident),
        "33": sum(s.pit for s in foreign),
        "34": 0,
    }


def build_xml(
    db: Session,
    company: Company,
    year: int,
    *,
    signed_on: date | None = None,
) -> bytes:
    st = collect(db, company.id, year)
    v = main_values(st)
    main = (
        tin("mst_cu", company.tax_code)
        + "<Header>"
        + num("toChucCoQTTTheoUyQuyen", 0)
        + el("ma_THQuyetToan", "01")
        + el("ten_THQuyetToan", "QT định kỳ")
        + el("hanNop")
        + "</Header><NVuKhauTruThue>"
        + "".join(num(f"ct{k}", v[str(k)]) for k in range(16, 35))
        + "</NVuKhauTruThue><NVuQToanThay>"
        + "".join(num(f"ct{k}", 0) for k in range(35, 42))
        + "</NVuQToanThay>"
    )
    appendices = _appendix_05_1(st.progressive) + _appendix_05_2(st.flat)
    return htkk.document(
        company,
        FORM,
        htkk.Period(
            "Y", str(year), from_month=f"01/{year}", to_month=f"12/{year}"
        ),
        main,
        appendices,
        signer=company.legal_rep,
        signed_on=signed_on,
    )
