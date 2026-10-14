"""Financial statements under Circular 133/2016/TT-BTC (TT133).

Covers B01a-DNN, B02-DNN and B03-DNN (direct method). The statements of
the other regimes live in statements_tt99.py and statements_tt58.py;
`statement_set(company)` picks the set of the company's regime.

The line item ↔ account mapping is declared as data below. Reference
for the line item codes and how each figure is derived: the TT133
financial statement preparation guide (ketoanthienung.net – see
docs/requests/gl.md).

B01a: each balance (per account, and per partner for accounts tracked
by partner) is assigned to EXACTLY ONE line item by the longest
matching account prefix and the balance side, so total assets (200)
always equal total liabilities and equity (500) when the books
balance. Unclosed balances of class 5–9 accounts are added to line 417
(undistributed profit after tax) and flagged with a report warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.accounts.models import Account
from app.features.gl import books
from app.features.gl.periods import ReportPeriod, day_before
from app.features.journal.queries import ledger_source
from app.features.org.models import Company


@dataclass
class Line:
    code: str
    name: str
    level: int = 1  # 0 = grand total, 1 = Roman section, 2 = detail
    formula: tuple[str, ...] | None = None  # sum of codes ("-" subtracts)
    note: str = ""  # notes column ("Thuyết minh")
    negative: bool = False  # line item shown as negative (*)


@dataclass
class StatementRow:
    line: Line
    current: int = 0
    previous: int = 0


@dataclass
class Statement:
    code: str
    title: str
    rows: list[StatementRow]
    current_label: str
    previous_label: str
    warnings: list[str] = field(default_factory=list)
    unmapped: dict = field(default_factory=dict)
    # The form has a "Thuyết minh" (notes reference) column.
    has_notes: bool = True

    def value(self, code: str, which: str = "current") -> int:
        for r in self.rows:
            if r.line.code == code:
                return getattr(r, which)
        raise KeyError(code)


def _calc_formulas(lines: list[Line], values: dict[str, int]) -> None:
    """Fill the total lines.

    Totals may be nested and listed before their parts (B01-DN puts 100
    before 110), so passes repeat until nothing changes.
    """
    for _ in range(10):
        changed = False
        for ln in lines:
            if ln.formula:
                total = 0
                for f in ln.formula:
                    total += (
                        -values.get(f[1:], 0)
                        if f.startswith("-")
                        else values.get(f, 0)
                    )
                if values.get(ln.code) != total:
                    values[ln.code] = total
                    changed = True
        if not changed:
            return


# ========================================================= B01a-DNN

B01A_LINES: list[Line] = [
    Line("A", "TÀI SẢN", 0),
    Line("110", "I. Tiền và các khoản tương đương tiền", 1),
    Line("120", "II. Đầu tư tài chính", 1, ("121", "122", "123", "124")),
    Line("121", "1. Chứng khoán kinh doanh", 2),
    Line("122", "2. Đầu tư nắm giữ đến ngày đáo hạn", 2),
    Line("123", "3. Đầu tư góp vốn vào đơn vị khác", 2),
    Line("124", "4. Dự phòng tổn thất đầu tư tài chính (*)", 2, negative=True),
    Line(
        "130",
        "III. Các khoản phải thu",
        1,
        ("131", "132", "133", "134", "135", "136"),
    ),
    Line("131", "1. Phải thu của khách hàng", 2),
    Line("132", "2. Trả trước cho người bán", 2),
    Line("133", "3. Vốn kinh doanh ở đơn vị trực thuộc", 2),
    Line("134", "4. Phải thu khác", 2),
    Line("135", "5. Tài sản thiếu chờ xử lý", 2),
    Line("136", "6. Dự phòng phải thu khó đòi (*)", 2, negative=True),
    Line("140", "IV. Hàng tồn kho", 1, ("141", "142")),
    Line("141", "1. Hàng tồn kho", 2),
    Line("142", "2. Dự phòng giảm giá hàng tồn kho (*)", 2, negative=True),
    Line("150", "V. Tài sản cố định", 1, ("151", "152")),
    Line("151", "- Nguyên giá", 2),
    Line("152", "- Giá trị hao mòn lũy kế (*)", 2, negative=True),
    Line("160", "VI. Bất động sản đầu tư", 1, ("161", "162")),
    Line("161", "- Nguyên giá", 2),
    Line("162", "- Giá trị hao mòn lũy kế (*)", 2, negative=True),
    Line("170", "VII. XDCB dở dang", 1),
    Line("180", "VIII. Tài sản khác", 1, ("181", "182")),
    Line("181", "1. Thuế GTGT được khấu trừ", 2),
    Line("182", "2. Tài sản khác", 2),
    Line(
        "200",
        "TỔNG CỘNG TÀI SẢN "
        "(200 = 110 + 120 + 130 + 140 + 150 + 160 + 170 + 180)",
        0,
        ("110", "120", "130", "140", "150", "160", "170", "180"),
    ),
    Line("B", "NGUỒN VỐN", 0),
    Line(
        "300",
        "I. Nợ phải trả",
        1,
        ("311", "312", "313", "314", "315", "316", "317", "318", "319", "320"),
    ),
    Line("311", "1. Phải trả người bán", 2),
    Line("312", "2. Người mua trả tiền trước", 2),
    Line("313", "3. Thuế và các khoản phải nộp Nhà nước", 2),
    Line("314", "4. Phải trả người lao động", 2),
    Line("315", "5. Phải trả khác", 2),
    Line("316", "6. Vay và nợ thuê tài chính", 2),
    Line("317", "7. Phải trả nội bộ về vốn kinh doanh", 2),
    Line("318", "8. Dự phòng phải trả", 2),
    Line("319", "9. Quỹ khen thưởng, phúc lợi", 2),
    Line("320", "10. Quỹ phát triển khoa học và công nghệ", 2),
    Line(
        "400",
        "II. Vốn chủ sở hữu",
        1,
        ("411", "412", "413", "414", "415", "416", "417"),
    ),
    Line("411", "1. Vốn góp của chủ sở hữu", 2),
    Line("412", "2. Thặng dư vốn cổ phần", 2),
    Line("413", "3. Vốn khác của chủ sở hữu", 2),
    Line("414", "4. Cổ phiếu quỹ (*)", 2, negative=True),
    Line("415", "5. Chênh lệch tỷ giá hối đoái", 2),
    Line("416", "6. Các quỹ thuộc vốn chủ sở hữu", 2),
    Line("417", "7. Lợi nhuận sau thuế chưa phân phối", 2),
    Line("500", "TỔNG CỘNG NGUỒN VỐN (500 = 300 + 400)", 0, ("300", "400")),
]
# Liabilities and equity line items (value = credit balance).
B01A_SOURCE_LINES = {
    ln.code
    for ln in B01A_LINES
    if ln.code.isdigit() and 300 <= int(ln.code) < 500
}

# (account prefix, balance side D|C|*) → line item code.
# The longest matching prefix wins.
B01A_RULES: list[tuple[str, str, str]] = [
    ("111", "*", "110"),
    ("112", "*", "110"),
    ("121", "*", "121"),
    ("128", "*", "122"),
    ("228", "*", "123"),
    ("2291", "*", "124"),
    ("2292", "*", "124"),
    ("131", "D", "131"),
    ("131", "C", "312"),
    ("331", "D", "132"),
    ("331", "C", "311"),
    ("1361", "D", "133"),
    ("1361", "C", "315"),
    ("1368", "D", "134"),
    ("1368", "C", "315"),
    ("1386", "D", "134"),
    ("1386", "C", "315"),
    ("1388", "D", "134"),
    ("1388", "C", "315"),
    ("1381", "D", "135"),
    ("1381", "C", "315"),
    ("141", "D", "134"),
    ("141", "C", "315"),
    ("334", "D", "134"),
    ("334", "C", "314"),
    ("338", "D", "134"),
    ("338", "C", "315"),
    ("2293", "*", "136"),
    ("15", "*", "141"),
    ("2294", "*", "142"),
    ("211", "*", "151"),
    ("2141", "*", "152"),
    ("2142", "*", "152"),
    ("2143", "*", "152"),
    ("217", "*", "161"),
    ("2147", "*", "162"),
    ("241", "*", "170"),
    ("133", "*", "181"),
    ("242", "*", "182"),
    ("333", "D", "182"),
    ("333", "C", "313"),
    ("335", "D", "182"),
    ("335", "C", "315"),
    ("3361", "D", "134"),
    ("3361", "C", "317"),
    ("3368", "D", "134"),
    ("3368", "C", "315"),
    ("341", "*", "316"),
    ("352", "*", "318"),
    ("353", "*", "319"),
    ("356", "*", "320"),
    ("4111", "*", "411"),
    ("4112", "*", "412"),
    ("4118", "*", "413"),
    ("419", "*", "414"),
    ("413", "*", "415"),
    ("418", "*", "416"),
    ("421", "*", "417"),
    # Unclosed class 5–9 balances belong to undistributed profit.
    ("5", "*", "417"),
    ("6", "*", "417"),
    ("7", "*", "417"),
    ("8", "*", "417"),
    ("9", "*", "417"),
]


def _rule_for(account: str, side: str) -> str | None:
    best, best_len = None, -1
    for prefix, s, code in B01A_RULES:
        if (
            (s == "*" or s == side)
            and account.startswith(prefix)
            and len(prefix) > best_len
        ):
            best, best_len = code, len(prefix)
    return best


def _grouped_balances(
    db: Session, company_id: int, d: date
) -> dict[tuple[str, int | None], int]:
    """Return balances at day `d` keyed by (account, partner).

    Partners are split out only for accounts tracked by partner.
    """
    partner_accounts = set(
        db.scalars(
            select(Account.code).where(
                Account.company_id == company_id,
                Account.partner_mode != "none",
            )
        )
    )
    raw = books.balances_at(db, company_id, d, by_partner=True)
    out: dict[tuple[str, int | None], int] = {}
    for (acc, pid), v in raw.items():
        key = (acc, pid if acc in partner_accounts else None)
        out[key] = out.get(key, 0) + v
    return {k: v for k, v in out.items() if v}


def _b01a_values(
    db: Session, company_id: int, d: date
) -> tuple[dict[str, int], dict[str, int], int]:
    values: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    unclosed = 0
    for (acc, _pid), net in _grouped_balances(db, company_id, d).items():
        side = "D" if net > 0 else "C"
        code = _rule_for(acc, side)
        if code is None:
            unmapped[acc] = unmapped.get(acc, 0) + net
            continue
        if acc[:1] in "56789":
            unclosed += net
        values[code] = values.get(code, 0) + (
            -net if code in B01A_SOURCE_LINES else net
        )
    _calc_formulas(B01A_LINES, values)
    return values, unmapped, unclosed


def b01a(db: Session, company: Company, period: ReportPeriod) -> Statement:
    """Build the statement of financial position at the period end.

    The comparative column shows balances at the start of the year.
    """
    cur, unmapped, unclosed = _b01a_values(db, company.id, period.end)
    year_start = date(period.end.year, 1, 1)
    prev_date = day_before(max(year_start, company.start_date))
    prev, _u, _x = _b01a_values(db, company.id, prev_date)
    rows = [
        StatementRow(ln, cur.get(ln.code, 0), prev.get(ln.code, 0))
        for ln in B01A_LINES
    ]
    st = Statement(
        "B01a-DNN",
        "BÁO CÁO TÌNH HÌNH TÀI CHÍNH",
        rows,
        "Số cuối kỳ" if period.mode != "year" else "Số cuối năm",
        "Số đầu năm",
        unmapped=unmapped,
    )
    if unclosed:
        st.warnings.append(
            "Còn số dư tài khoản doanh thu, chi phí chưa kết chuyển "
            "(đã tính vào chỉ tiêu 417). "
            "Nên chạy Kết chuyển cuối kỳ trước khi lập báo cáo."
        )
    if unmapped:
        st.warnings.append(
            "Có số dư tài khoản chưa được xếp vào chỉ tiêu nào: "
            + ", ".join(sorted(unmapped))
        )
    if st.value("200") != st.value("500"):
        st.warnings.append(
            "Tổng tài sản khác tổng nguồn vốn – kiểm tra số dư đầu kỳ "
            "và Bảng cân đối tài khoản."
        )
    return st


# ========================================================== B02-DNN

B02_LINES: list[Line] = [
    Line("01", "1. Doanh thu bán hàng và cung cấp dịch vụ"),
    Line("02", "2. Các khoản giảm trừ doanh thu"),
    Line(
        "10",
        "3. Doanh thu thuần về bán hàng và cung cấp dịch vụ (10 = 01 - 02)",
        0,
        ("01", "-02"),
    ),
    Line("11", "4. Giá vốn hàng bán"),
    Line(
        "20",
        "5. Lợi nhuận gộp về bán hàng và cung cấp dịch vụ (20 = 10 - 11)",
        0,
        ("10", "-11"),
    ),
    Line("21", "6. Doanh thu hoạt động tài chính"),
    Line("22", "7. Chi phí tài chính"),
    Line("23", "- Trong đó: Chi phí lãi vay", 2),
    Line("24", "8. Chi phí quản lý kinh doanh"),
    Line(
        "30",
        "9. Lợi nhuận thuần từ hoạt động kinh doanh (30 = 20 + 21 - 22 - 24)",
        0,
        ("20", "21", "-22", "-24"),
    ),
    Line("31", "10. Thu nhập khác"),
    Line("32", "11. Chi phí khác"),
    Line("40", "12. Lợi nhuận khác (40 = 31 - 32)", 0, ("31", "-32")),
    Line(
        "50",
        "13. Tổng lợi nhuận kế toán trước thuế (50 = 30 + 40)",
        0,
        ("30", "40"),
    ),
    Line("51", "14. Chi phí thuế TNDN"),
    Line(
        "60",
        "15. Lợi nhuận sau thuế thu nhập doanh nghiệp (60 = 50 - 51)",
        0,
        ("50", "-51"),
    ),
]


def _sum_flow(f: dict[str, tuple[int, int]], prefixes, kind: str) -> int:
    """kind: debit | credit | net_debit | net_credit."""
    if isinstance(prefixes, str):
        prefixes = (prefixes,)
    total = 0
    for acc, (d, c) in f.items():
        if any(acc.startswith(p) for p in prefixes):
            total += {
                "debit": d,
                "credit": c,
                "net_debit": d - c,
                "net_credit": c - d,
            }[kind]
    return total


def _interest_accounts(db: Session, company_id: int) -> tuple[str, ...]:
    """Return 635 sub-accounts whose name contains "lãi vay".

    Used for line 23 (interest expense).
    """
    from app.core.db import ulower

    return tuple(
        db.scalars(
            select(Account.code).where(
                Account.company_id == company_id,
                Account.code.like("635%"),
                Account.code != "635",
                ulower(Account.name).like("%lãi vay%"),
            )
        )
    )


def _b02_values(
    db: Session, company_id: int, start: date, end: date
) -> dict[str, int]:
    f = books.flows(db, company_id, start, end, exclude_closing=True)
    interest = _interest_accounts(db, company_id)
    v = {
        "01": _sum_flow(f, "511", "credit"),
        "02": _sum_flow(f, "511", "debit"),
        "11": _sum_flow(f, "632", "net_debit"),
        "21": _sum_flow(f, "515", "net_credit"),
        "22": _sum_flow(f, "635", "net_debit"),
        "23": _sum_flow(f, interest, "net_debit") if interest else 0,
        "24": _sum_flow(f, "642", "net_debit"),
        "31": _sum_flow(f, "711", "net_credit"),
        "32": _sum_flow(f, "811", "net_debit"),
        "51": _sum_flow(f, "821", "net_debit"),
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
    rows = [
        StatementRow(ln, cur.get(ln.code, 0), prev.get(ln.code, 0))
        for ln in B02_LINES
    ]
    label = "Năm nay" if period.mode == "year" else "Kỳ này"
    st = Statement(
        "B02-DNN",
        "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
        rows,
        label,
        "Năm trước" if period.mode == "year" else "Cùng kỳ năm trước",
    )
    if not _interest_accounts(db, company.id) and cur.get("22"):
        st.warnings.append(
            "Chỉ tiêu 23 (chi phí lãi vay) lấy từ TK chi tiết của 635 "
            'có tên chứa "lãi vay" – '
            "chưa có TK này nên để trống."
        )
    return st


# ============================================ B03-DNN (direct method)

B03_LINES: list[Line] = [
    Line("I", "I. Lưu chuyển tiền từ hoạt động kinh doanh", 1),
    Line(
        "01", "1. Tiền thu từ bán hàng, cung cấp dịch vụ và doanh thu khác", 2
    ),
    Line("02", "2. Tiền chi trả cho người cung cấp hàng hóa và dịch vụ", 2),
    Line("03", "3. Tiền chi trả cho người lao động", 2),
    Line("04", "4. Tiền lãi vay đã trả", 2),
    Line("05", "5. Thuế thu nhập doanh nghiệp đã nộp", 2),
    Line("06", "6. Tiền thu khác từ hoạt động kinh doanh", 2),
    Line("07", "7. Tiền chi khác cho hoạt động kinh doanh", 2),
    Line(
        "20",
        "Lưu chuyển tiền thuần từ hoạt động kinh doanh",
        0,
        ("01", "02", "03", "04", "05", "06", "07"),
    ),
    Line("II", "II. Lưu chuyển tiền từ hoạt động đầu tư", 1),
    Line(
        "21",
        "1. Tiền chi để mua sắm, xây dựng TSCĐ, BĐSĐT "
        "và các tài sản dài hạn khác",
        2,
    ),
    Line(
        "22",
        "2. Tiền thu từ thanh lý, nhượng bán TSCĐ, BĐSĐT "
        "và các tài sản dài hạn khác",
        2,
    ),
    Line("23", "3. Tiền chi cho vay, đầu tư góp vốn vào đơn vị khác", 2),
    Line("24", "4. Tiền thu hồi cho vay, đầu tư góp vốn vào đơn vị khác", 2),
    Line("25", "5. Tiền thu lãi cho vay, cổ tức và lợi nhuận được chia", 2),
    Line(
        "30",
        "Lưu chuyển tiền thuần từ hoạt động đầu tư",
        0,
        ("21", "22", "23", "24", "25"),
    ),
    Line("III", "III. Lưu chuyển tiền từ hoạt động tài chính", 1),
    Line(
        "31",
        "1. Tiền thu từ phát hành cổ phiếu, nhận vốn góp của chủ sở hữu",
        2,
    ),
    Line(
        "32",
        "2. Tiền trả lại vốn góp cho các chủ sở hữu, "
        "mua lại cổ phiếu của doanh nghiệp đã phát hành",
        2,
    ),
    Line("33", "3. Tiền thu từ đi vay", 2),
    Line("34", "4. Tiền trả nợ gốc vay và nợ thuê tài chính", 2),
    Line("35", "5. Cổ tức, lợi nhuận đã trả cho chủ sở hữu", 2),
    Line(
        "40",
        "Lưu chuyển tiền thuần từ hoạt động tài chính",
        0,
        ("31", "32", "33", "34", "35"),
    ),
    Line(
        "50",
        "Lưu chuyển tiền thuần trong kỳ (50 = 20 + 30 + 40)",
        0,
        ("20", "30", "40"),
    ),
    Line("60", "Tiền và tương đương tiền đầu kỳ", 0),
    Line("61", "Ảnh hưởng của thay đổi tỷ giá hối đoái quy đổi ngoại tệ", 0),
    Line(
        "70",
        "Tiền và tương đương tiền cuối kỳ (70 = 50 + 60 + 61)",
        0,
        ("50", "60", "61"),
    ),
]
B03_CODES = {ln.code for ln in B03_LINES}
CASH_PREFIXES = ("111", "112")

# Cash inflows (debit to a cash account): contra account → line item.
# Defaults to 06.
B03_IN: list[tuple[str, str]] = [
    ("511", "01"),
    ("131", "01"),
    ("3331", "01"),
    ("3387", "01"),
    ("121", "01"),
    ("515", "25"),
    ("711", "06"),
    ("411", "31"),
    ("341", "33"),
    ("128", "24"),
    ("228", "24"),
]
# Cash outflows (credit to a cash account): contra account → line
# item. Defaults to 07.
B03_OUT: list[tuple[str, str]] = [
    ("331", "02"),
    ("15", "02"),
    ("133", "02"),
    ("641", "02"),
    ("642", "02"),
    ("632", "02"),
    ("242", "02"),
    ("611", "02"),
    ("131", "02"),
    ("334", "03"),
    ("635", "04"),
    ("3334", "05"),
    ("211", "21"),
    ("213", "21"),
    ("217", "21"),
    ("241", "21"),
    ("128", "23"),
    ("228", "23"),
    ("411", "32"),
    ("419", "32"),
    ("341", "34"),
    ("421", "35"),
]


def classify_cash(contra: str | None, inflow: bool) -> str:
    table = B03_IN if inflow else B03_OUT
    best, best_len = ("06" if inflow else "07"), -1
    for prefix, code in table:
        if contra and contra.startswith(prefix) and len(prefix) > best_len:
            best, best_len = code, len(prefix)
    return best


def _b03_values(
    db: Session, company_id: int, start: date, end: date
) -> dict[str, int]:
    lr = ledger_source(db, company_id, include_opening=False)
    rows = db.execute(
        select(
            lr.c.account, lr.c.contra, lr.c.debit, lr.c.credit, lr.c.cash_flow
        ).where(lr.c.date >= start, lr.c.date <= end)
    ).all()
    v: dict[str, int] = {}
    for acc, contra, debit, credit, override in rows:
        if not acc.startswith(CASH_PREFIXES):
            continue
        if contra and contra.startswith(CASH_PREFIXES):
            continue  # internal transfer between cash on hand and bank
        inflow = bool(debit)
        amount = int(debit) if inflow else -int(credit)
        code = (
            override
            if override in B03_CODES
            else classify_cash(contra, inflow)
        )
        v[code] = v.get(code, 0) + amount
    opening = sum(
        net
        for (acc, _p), net in books.balances_at(
            db, company_id, day_before(start)
        ).items()
        if acc.startswith(CASH_PREFIXES)
    )
    v["60"] = opening
    v["61"] = 0
    _calc_formulas(B03_LINES, v)
    return v


def b03(db: Session, company: Company, period: ReportPeriod) -> Statement:
    cur = _b03_values(db, company.id, period.start, period.end)
    prev_p = period.previous_year()
    prev = (
        _b03_values(db, company.id, prev_p.start, prev_p.end)
        if prev_p.end >= company.start_date
        else {}
    )
    rows = [
        StatementRow(ln, cur.get(ln.code, 0), prev.get(ln.code, 0))
        for ln in B03_LINES
    ]
    st = Statement(
        "B03-DNN",
        "BÁO CÁO LƯU CHUYỂN TIỀN TỆ",
        rows,
        "Năm nay" if period.mode == "year" else "Kỳ này",
        "Năm trước" if period.mode == "year" else "Cùng kỳ năm trước",
    )
    closing = sum(
        net
        for (acc, _p), net in books.balances_at(
            db, company.id, period.end
        ).items()
        if acc.startswith(CASH_PREFIXES)
    )
    if closing != cur.get("70"):
        st.warnings.append(
            "Tiền cuối kỳ trên báo cáo khác số dư TK 111 + 112."
        )
    return st


STATEMENTS = {"b01a": b01a, "b02": b02, "b03": b03}


@dataclass(frozen=True)
class StatementSet:
    """The statements of one accounting regime.

    Keys of `builders`/`titles` are the URL names (b01a, b02, b03);
    `lines` names the line codes the dashboard and checks read.
    """

    builders: dict
    titles: dict  # kind -> (title, form code)
    lines: dict  # assets, sources, equity, revenue, profit
    notes_code: str | None  # None = no notes form
    trial_balance: tuple[str, str]  # (title, form code; "" = internal)


TT133_SET = StatementSet(
    STATEMENTS,
    {
        "b01a": ("Báo cáo tình hình tài chính", "B01a-DNN"),
        "b02": ("Báo cáo kết quả hoạt động kinh doanh", "B02-DNN"),
        "b03": ("Báo cáo lưu chuyển tiền tệ", "B03-DNN"),
    },
    {
        "assets": "200",
        "sources": "500",
        "equity": "400",
        "revenue": "10",
        "profit": "60",
    },
    "B09-DNN",
    ("Bảng cân đối tài khoản", "F01-DNN"),
)


def statement_set(company) -> StatementSet:
    from app.core.regimes import regime_of

    kind = regime_of(company).statements
    if kind == "tt99":
        from app.features.gl import statements_tt99 as m

        return StatementSet(
            m.STATEMENTS,
            {
                "b01a": ("Báo cáo tình hình tài chính", "B01-DN"),
                "b02": ("Báo cáo kết quả hoạt động kinh doanh", "B02-DN"),
                "b03": ("Báo cáo lưu chuyển tiền tệ", "B03-DN"),
            },
            {
                "assets": "280",
                "sources": "440",
                "equity": "400",
                "revenue": "10",
                "profit": "60",
            },
            "B09-DN",
            ("Bảng cân đối số phát sinh", "S06-DN"),
        )
    if kind == "tt58":
        from app.features.gl import statements_tt58 as m

        return StatementSet(
            m.STATEMENTS,
            {
                "b01a": ("Báo cáo tình hình tài chính", "B01-DNSN"),
                "b02": ("Báo cáo kết quả hoạt động kinh doanh", "B02-DNSN"),
            },
            {
                "assets": "200",
                "sources": "500",
                "equity": "400",
                "revenue": "01",
                "profit": "20",
            },
            None,
            ("Bảng cân đối tài khoản (sổ nội bộ)", ""),
        )
    return TT133_SET
