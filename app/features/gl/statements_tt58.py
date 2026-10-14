"""Financial statements under Circular 58/2026/TT-BTC (micro
enterprises): B01-DNSN and B02-DNSN.

TT58 prescribes no chart of accounts; VFAS keeps the books double-entry
on the TT133 chart and derives the two statements from the balances
and movements (docs/DECISIONS.md D-v2-31). Line codes and contents
follow the forms in TT58 (signed text, 25/5/2026). The statements are
mandatory only for enterprises paying CIT on taxable income (Art. 10).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.features.gl import books
from app.features.gl.periods import ReportPeriod, day_before
from app.features.gl.statements import (
    Line,
    Statement,
    StatementRow,
    _calc_formulas,
    _grouped_balances,
    _sum_flow,
)
from app.features.org.models import Company

B01_LINES: list[Line] = [
    Line("A", "TÀI SẢN", 0),
    Line("110", "1. Tiền", 2),
    Line("120", "2. Các khoản nợ phải thu", 2),
    Line("130", "3. Hàng tồn kho", 2),
    Line("140", "4. Tài sản cố định", 2),
    Line("150", "5. Tài sản khác", 2),
    Line(
        "200",
        "TỔNG CỘNG TÀI SẢN (200 = 110 + 120 + 130 + 140 + 150)",
        0,
        ("110", "120", "130", "140", "150"),
    ),
    Line("B", "NGUỒN VỐN", 0),
    Line("300", "I. Nợ phải trả (300 = 310 + 320)", 1, ("310", "320")),
    Line("310", "1. Các khoản nợ phải trả", 2),
    Line("320", "2. Thuế và các khoản phải nộp Nhà nước", 2),
    Line(
        "400",
        "II. Vốn chủ sở hữu (400 = 410 + 420 + 430)",
        1,
        ("410", "420", "430"),
    ),
    Line("410", "1. Vốn đầu tư của chủ sở hữu", 2),
    Line("420", "2. Lợi nhuận sau thuế chưa phân phối", 2),
    Line("430", "3. Các quỹ thuộc vốn chủ sở hữu", 2),
    Line("500", "TỔNG CỘNG NGUỒN VỐN (500 = 300 + 400)", 0, ("300", "400")),
]
SOURCE_LINES = {"300", "310", "320", "400", "410", "420", "430", "500"}
# (account prefix, balance side D|C|*) -> line. Longest prefix wins.
B01_RULES: list[tuple[str, str, str]] = [
    ("111", "*", "110"),
    ("112", "*", "110"),
    ("113", "*", "110"),
    ("121", "*", "150"),
    ("128", "*", "150"),
    ("228", "*", "150"),
    ("229", "*", "150"),
    ("2293", "*", "120"),
    ("2294", "*", "130"),
    ("131", "D", "120"),
    ("131", "C", "310"),
    ("331", "D", "120"),
    ("331", "C", "310"),
    ("133", "*", "120"),
    ("136", "D", "120"),
    ("136", "C", "310"),
    ("138", "D", "120"),
    ("138", "C", "310"),
    ("1381", "*", "150"),
    ("141", "D", "120"),
    ("141", "C", "310"),
    ("334", "D", "120"),
    ("334", "C", "310"),
    ("338", "D", "120"),
    ("338", "C", "310"),
    ("15", "*", "130"),
    ("211", "*", "140"),
    ("214", "*", "140"),
    ("2147", "*", "150"),
    ("217", "*", "150"),
    ("241", "*", "150"),
    ("242", "*", "150"),
    ("333", "D", "150"),
    ("333", "C", "320"),
    ("335", "*", "310"),
    ("336", "*", "310"),
    ("341", "*", "310"),
    ("352", "*", "310"),
    ("353", "*", "310"),
    ("356", "*", "310"),
    ("411", "*", "410"),
    ("413", "*", "410"),
    ("419", "*", "410"),
    ("418", "*", "430"),
    ("421", "*", "420"),
    ("5", "*", "420"),
    ("6", "*", "420"),
    ("7", "*", "420"),
    ("8", "*", "420"),
    ("9", "*", "420"),
]


def _rule_for(account: str, side: str) -> str | None:
    best, best_len = None, -1
    for prefix, s, code in B01_RULES:
        if (
            (s == "*" or s == side)
            and account.startswith(prefix)
            and len(prefix) > best_len
        ):
            best, best_len = code, len(prefix)
    return best


def _b01_values(
    db: Session, company_id: int, d: date
) -> tuple[dict[str, int], dict[str, int]]:
    values: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    for (acc, _pid), net in _grouped_balances(db, company_id, d).items():
        code = _rule_for(acc, "D" if net > 0 else "C")
        if code is None:
            unmapped[acc] = unmapped.get(acc, 0) + net
            continue
        values[code] = values.get(code, 0) + (
            -net if code in SOURCE_LINES else net
        )
    _calc_formulas(B01_LINES, values)
    return values, unmapped


def b01(db: Session, company: Company, period: ReportPeriod) -> Statement:
    cur, unmapped = _b01_values(db, company.id, period.end)
    year_start = date(period.end.year, 1, 1)
    prev, _u = _b01_values(
        db, company.id, day_before(max(year_start, company.start_date))
    )
    st = Statement(
        "B01-DNSN",
        "BÁO CÁO TÌNH HÌNH TÀI CHÍNH",
        [
            StatementRow(ln, cur.get(ln.code, 0), prev.get(ln.code, 0))
            for ln in B01_LINES
        ],
        "Số cuối năm" if period.mode == "year" else "Số cuối kỳ",
        "Số đầu năm",
        unmapped=unmapped,
    )
    st.has_notes = False
    if unmapped:
        st.warnings.append(
            "Có số dư tài khoản chưa được xếp vào chỉ tiêu nào: "
            + ", ".join(sorted(unmapped))
        )
    if st.value("200") != st.value("500"):
        st.warnings.append("Tổng tài sản khác tổng nguồn vốn.")
    return st


B02_LINES: list[Line] = [
    Line("01", "1. Doanh thu và thu nhập thuần"),
    Line("02", "2. Các khoản chi phí"),
    Line(
        "03",
        "3. Lợi nhuận kế toán trước thuế TNDN (03 = 01 - 02)",
        0,
        ("01", "-02"),
    ),
    Line("10", "4. Chi phí thuế TNDN"),
    Line("20", "5. Lợi nhuận sau thuế TNDN (20 = 03 - 10)", 0, ("03", "-10")),
]


def _b02_values(
    db: Session, company_id: int, start: date, end: date
) -> dict[str, int]:
    f = books.flows(db, company_id, start, end, exclude_closing=True)
    v = {
        "01": _sum_flow(f, ("511", "515", "711"), "net_credit")
        - _sum_flow(f, "521", "net_debit"),
        "02": _sum_flow(f, ("632", "635", "641", "642", "811"), "net_debit"),
        "10": _sum_flow(f, "821", "net_debit"),
    }
    _calc_formulas(B02_LINES, v)
    return v


def b02(db: Session, company: Company, period: ReportPeriod) -> Statement:
    cur = _b02_values(db, company.id, period.start, period.end)
    prev_p = period.previous_year()
    prev = (
        _b02_values(db, company.id, prev_p.start, prev_p.end)
        if prev_p.end >= company.start_date
        else {}
    )
    st = Statement(
        "B02-DNSN",
        "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
        [
            StatementRow(ln, cur.get(ln.code, 0), prev.get(ln.code, 0))
            for ln in B02_LINES
        ],
        "Năm nay" if period.mode == "year" else "Kỳ này",
        "Năm trước" if period.mode == "year" else "Cùng kỳ năm trước",
    )
    st.has_notes = False
    return st


STATEMENTS = {"b01a": b01, "b02": b02}
