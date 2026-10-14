"""03/TNDN (CIT finalization return) as HTKK XML.

HTKK form 892, XML version 2.9.4 (CIT Law 67/2025: rates 15/17/20%),
checked against the official schema 03_TNDN_TT80_294.xsd. Includes
appendix 03-1A/TNDN (income statement of a production, trading or
service company) built from B02-DNN.

The CIT worksheet (vat/cit.py) keeps the short letter keys of the
main indicators; the mapping to the form is:

  worksheet           form 03/TNDN
  A1                  [A1]
  B1 increases        [B1] = [B4] (non-deductible expenses)
  B8 decreases        [B8] = [B12] (other decreases)
  B12 taxable income  [B13] = [B14] = [C1]
  C2 / C3 / C4 / C5   [C2] / [C3] = [C3a] / [C4] / [C5]
  C6                  [C6] = [C7] at the single rate C7
  C8 tax at rate      [C9]
  C9 exempt/reduced   [C10] = [C13]
  D payable           [C17] = [E1] = [E]
  E prepaid           [G2] = [G]
  F = D - E           [H1] = [I1] = [I]

Detail lines without data in the books (real-estate transfers,
related-party adjustments, foreign tax credits...) are written as 0:
complete them in HTKK when they apply.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core import htkk
from app.core.htkk import el, num
from app.core.regimes import regime_of
from app.features.gl import books, periods, statements
from app.features.org.models import Company

FORM = htkk.FormInfo(
    code="892",
    name=("TỜ KHAI QUYẾT TOÁN THUẾ THU NHẬP DOANH NGHIỆP (Mẫu số 03/TNDN)"),
    note=(
        "(Ban hành kèm theo Thông tư số 80/2021/TT-BTC ngày 29 tháng 9 "
        "năm 2021 của Bộ trưởng Bộ Tài chính)"
    ),
    version="2.9.4",
)
SME_RATES = (15, 17, 20)


def _net_debit(flows: dict, prefix: str) -> int:
    return sum(
        d - c for acc, (d, c) in flows.items() if acc.startswith(prefix)
    )


def appendix_1a(db: Session, company: Company, year: int) -> dict:
    """03-1A/TNDN figures from the income statement of the year.

    TT133 (and TT58, kept on the TT133 chart): B02-DNN, selling
    expenses = 6421, administrative = 6422. TT99: B02-DN lines.
    """
    period = periods.make("year", year)
    if regime_of(company).code == "tt99":
        from app.features.gl import statements_tt99

        v = {
            r.line.code: r.current
            for r in statements_tt99.b02(db, company, period).rows
        }
        revenue, deductions, fin_income = v["01"], v["02"], v["22"]
        cogs, fin_cost, interest = v["11"], v["23"], v["24"]
        selling, admin = v["25"], v["26"]
    else:
        v = {
            r.line.code: r.current
            for r in statements.b02(db, company, period).rows
        }
        flows = books.flows(
            db, company.id, period.start, period.end, exclude_closing=True
        )
        revenue, deductions, fin_income = v["01"], v["02"], v["21"]
        cogs, fin_cost, interest = v["11"], v["22"], v["23"]
        selling = _net_debit(flows, "6421")
        admin = v["24"] - selling
    a = {
        "04": revenue,
        "05": 0,
        "06": deductions,
        "07": 0,
        "08": 0,
        "09": deductions,  # returns and allowances together
        "10": fin_income,
        "11": 0,
        "13": cogs,
        "14": selling,
        "15": admin,
        "16": fin_cost,
        "17": interest,
        "19": v["31"],
        "20": v["32"],
    }
    a["12"] = a["13"] + a["14"] + a["15"]
    a["18"] = a["04"] - a["06"] + a["10"] - a["12"] - a["16"]
    a["21"] = a["19"] - a["20"]
    a["22"] = a["18"] + a["21"]
    return a


def main_values(v: dict) -> dict:
    """Form indicators from the CIT worksheet values."""
    rate = int(v.get("C7") or 20)
    f: dict = {}
    f["A1"] = v.get("A1", 0)
    f["B1"] = f["B4"] = v.get("B1", 0)
    f["B8"] = f["B12"] = v.get("B8", 0)
    f["B13"] = f["B14"] = f["C1"] = v.get("B12", 0)
    f["C2"] = v.get("C2", 0)
    f["C3"] = f["C3a"] = v.get("C3", 0)
    f["C4"] = v.get("C4", 0)
    f["C5"] = v.get("C5", 0)
    f["C6"] = v.get("C6", 0)
    if rate in SME_RATES:
        f["C7_thuNhap"], f["C8"], f["C8a"] = v.get("C6", 0), 0, 0
    else:
        f["C7_thuNhap"], f["C8"], f["C8a"] = 0, v.get("C6", 0), rate
    f["C9"] = v.get("C8", 0)
    f["C10"] = f["C13"] = v.get("C9", 0)
    f["C17"] = f["E"] = f["E1"] = v.get("D", 0)
    f["G"] = f["G2"] = f["tamnop_sxkd"] = v.get("E", 0)
    f["H1"] = f["I"] = f["I1"] = v.get("F", 0)
    f["rate"] = rate
    return f


MAIN_ORDER = (
    "A1", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10",
    "B11", "B12", "B13", "B14", "B15", "C1", "C2",
)  # fmt: skip
AFTER_EXEMPT = ("C3", "C3a", "C3b", "C4", "C5", "C6")
AFTER_C7 = (
    "C8", "C8a", "C9", "C10", "C11", "C12", "C13", "C14", "C15", "C16",
    "C17", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "E", "E1",
    "E2", "E3", "E4", "E5", "E6", "G",
)  # fmt: skip


def build_xml(
    db: Session,
    company: Company,
    year: int,
    values: dict,
    *,
    signed_on: date | None = None,
) -> bytes:
    f = main_values(values)
    g = f.get
    rate = f["rate"]
    parts = [
        "<Header>"
        + el("kyTaiChinh_Tu", "01/01")
        + el("kyTaiChinh_Den", "31/12")
        + el("ma_THQuyetToan", "01")
        + el("ten_THQuyetToan", "QT định kỳ")
        + el("ct04_ma")
        + el("ct04_ten")
        + num("ct05", 0)
        + "</Header>"
    ]
    parts += [num(f"ct{k}", g(k)) for k in MAIN_ORDER]
    parts.append(
        '<dsThMienThue><thMienThue id="ID_1">'
        + el("ma_loaiMien")
        + el("ten_loaiMien")
        + num("ctC2_soThuNhap", g("C2"))
        + "</thMienThue></dsThMienThue>"
    )
    parts += [num(f"ct{k}", g(k)) for k in AFTER_EXEMPT]
    in_sme = rate in SME_RATES
    parts += [
        num("ctC7_thuNhap", g("C7_thuNhap")),
        el(
            "ctC7_tenThueSuat",
            f"Thu nhập tính thuế áp dụng thuế suất {rate}%" if in_sme else "",
        ),
        el("ctC7_thueSuat", rate if in_sme else ""),
    ]
    parts += [num(f"ct{k}", g(k)) for k in AFTER_C7]
    parts += [
        num("thueTNDN_tamnop_sxkd", g("tamnop_sxkd")),
        num("ctG1", 0),
        num("ctG2", g("G2")),
        num("thueTNDN_tamnop_cnbds", 0),
        num("ctG3", 0),
        num("ctG4", 0),
        num("ctG5", 0),
        num("ctH1", g("H1")),
        num("ctH2", 0),
        num("ctH3", 0),
        num("ctI", g("I")),
        num("ctI1", g("I1")),
        num("ctI2", 0),
    ]
    e1 = g("E1") or 0
    parts.append(
        "<NghiaVuThue><CtE1>"
        + num("soKK", e1)
        + num("tieuMuc1052", e1)
        + num("tieuMuc1056", 0)
        + num("tieuMuc1057", 0)
        + num("tieuMuc1099", 0)
        + "</CtE1><CtE2>"
        + num("soKK", 0)
        + num("tieuMuc1053", 0)
        + "</CtE2><CtE5>"
        + num("soKK", 0)
        + num("tieuMuc1052", 0)
        + num("tieuMuc1056", 0)
        + num("tieuMuc1057", 0)
        + num("tieuMuc1099", 0)
        + "</CtE5>"
        + num("tong1052", e1)
        + num("tong1056", 0)
        + num("tong1057", 0)
        + num("tong1053", 0)
        + "</NghiaVuThue>"
    )
    a = appendix_1a(db, company, year)
    appendix = (
        "<PLuc_03_1A_TNDN>"
        + "".join(num(f"ct{k:02d}", a[f"{k:02d}"]) for k in range(4, 23))
        + "</PLuc_03_1A_TNDN>"
    )
    return htkk.document(
        company,
        FORM,
        htkk.Period("Y", str(year), date(year, 1, 1), date(year, 12, 31)),
        "".join(parts),
        appendix,
        signer=company.legal_rep,
        signed_on=signed_on,
    )
