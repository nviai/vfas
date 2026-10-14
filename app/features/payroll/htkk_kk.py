"""05/KK-TNCN (return of PIT withheld from salaries) as HTKK XML.

HTKK form 864, XML version 2.9.3, checked against the official schema
05_KK_TNCN_TT80_293.xsd ("địa bàn hành chính" envelope). HTKK 5.8.0
still files this return on the Circular 80/2021 layout; Circular
89/2026, Article 100(8) keeps the current layout until the tax
authority's systems are upgraded.

No allocation to dependent units in other provinces ([15] = 0), so
appendix 05-1/PBT-KK-TNCN is not written.
"""

from __future__ import annotations

from datetime import date

from app.core import htkk
from app.core.htkk import num, tin
from app.features.org.models import Company
from app.features.payroll.pit_return import KEYS
from app.features.vat.service import TaxPeriod

FORM = htkk.FormInfo(
    code="864",
    name="TK khấu trừ thuế thu nhập cá nhân Mẫu 05/KK-TNCN (TT80/2021)",
    note=(
        "(Ban hành kèm theo Thông tư số 80/2021/TT-BTC ngày 29 tháng 9 "
        "năm 2021 của Bộ trưởng Bộ Tài chính)"
    ),
    version="2.9.3",
    dbhc=True,
)


def main_xml(company: Company, values: dict) -> str:
    return (
        tin("mst_cu", company.tax_code)
        + "<ct15>0</ct15>"
        + "".join(num(f"ct{k}", values.get(k)) for k in KEYS)
    )


def build_xml(
    company: Company,
    period: TaxPeriod,
    values: dict,
    *,
    signed_on: date | None = None,
    version: int = 0,
    appendices: str = "",
) -> bytes:
    kind = "M" if period.period_type == "month" else "Q"
    return htkk.document(
        company,
        FORM,
        htkk.Period(kind, period.code, period.start, period.end),
        main_xml(company, values),
        appendices,
        signer=company.legal_rep,
        signed_on=signed_on,
        version=version,
    )
