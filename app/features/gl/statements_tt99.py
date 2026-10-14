"""Financial statements under Circular 99/2025/TT-BTC.

B01-DN (statement of financial position, going concern), B02-DN and
B03-DN (direct method). Line codes and computations follow Appendix IV
of TT99 (docs/DECISIONS.md D-v2-31).

Short-term / long-term split: TT99 classifies receivables, payables,
loans, prepaid expenses... by their remaining term (12 months or one
operating cycle). The books do not carry a maturity date, so each
balance goes to the column given by the rule below; accounts listed in
the company setting `b01.long_term` (account code prefixes) are moved
to the long-term line, those in `b01.short_term` to the short-term
line. Balances that need such a judgement are listed as a report
warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.gl import books
from app.features.gl.periods import ReportPeriod, day_before
from app.features.gl.statements import (
    Line,
    Statement,
    StatementRow,
    _calc_formulas,
    _grouped_balances,
    _interest_accounts,
    _sum_flow,
)
from app.features.journal.queries import ledger_source
from app.features.org.models import Company

# ============================================================ B01-DN

B01_LINES: list[Line] = [
    Line("A", "A - TÀI SẢN NGẮN HẠN", 0),
    Line(
        "100",
        "TÀI SẢN NGẮN HẠN",
        0,
        ("110", "120", "130", "140", "150", "160"),
    ),
    Line("110", "I. Tiền và các khoản tương đương tiền", 1, ("111", "112")),
    Line("111", "1. Tiền", 2),
    Line("112", "2. Các khoản tương đương tiền", 2),
    Line(
        "120",
        "II. Đầu tư tài chính ngắn hạn",
        1,
        ("121", "122", "123", "124", "125", "126"),
    ),
    Line("121", "1. Chứng khoán kinh doanh", 2),
    Line(
        "122",
        "2. Dự phòng giảm giá chứng khoán kinh doanh (*)",
        2,
        negative=True,
    ),
    Line("123", "3. Đầu tư nắm giữ đến ngày đáo hạn ngắn hạn", 2),
    Line(
        "124",
        "4. Dự phòng đầu tư nắm giữ đến ngày đáo hạn ngắn hạn (*)",
        2,
        negative=True,
    ),
    Line("125", "5. Đầu tư ngắn hạn khác", 2),
    Line(
        "126",
        "6. Dự phòng tổn thất các khoản đầu tư ngắn hạn khác (*)",
        2,
        negative=True,
    ),
    Line(
        "130",
        "III. Các khoản phải thu ngắn hạn",
        1,
        ("131", "132", "133", "134", "135", "136", "137"),
    ),
    Line("131", "1. Phải thu ngắn hạn của khách hàng", 2),
    Line("132", "2. Trả trước cho người bán ngắn hạn", 2),
    Line("133", "3. Phải thu nội bộ ngắn hạn", 2),
    Line("134", "4. Phải thu theo tiến độ hợp đồng xây dựng", 2),
    Line("135", "5. Phải thu ngắn hạn khác", 2),
    Line("136", "6. Dự phòng phải thu ngắn hạn khó đòi (*)", 2, negative=True),
    Line("137", "7. Tài sản thiếu chờ xử lý", 2),
    Line("140", "IV. Hàng tồn kho", 1, ("141", "142")),
    Line("141", "1. Hàng tồn kho", 2),
    Line("142", "2. Dự phòng giảm giá hàng tồn kho (*)", 2, negative=True),
    Line("150", "V. Tài sản sinh học ngắn hạn", 1, ("151", "152", "153")),
    Line("151", "1. Súc vật nuôi lấy sản phẩm một lần ngắn hạn", 2),
    Line(
        "152", "2. Cây trồng theo mùa vụ hoặc lấy sản phẩm một lần ngắn hạn", 2
    ),
    Line(
        "153",
        "3. Dự phòng tổn thất tài sản sinh học ngắn hạn (*)",
        2,
        negative=True,
    ),
    Line(
        "160",
        "VI. Tài sản ngắn hạn khác",
        1,
        ("161", "162", "163", "164", "165"),
    ),
    Line("161", "1. Chi phí chờ phân bổ ngắn hạn", 2),
    Line("162", "2. Thuế GTGT được khấu trừ", 2),
    Line("163", "3. Thuế và các khoản khác phải thu Nhà nước", 2),
    Line("164", "4. Giao dịch mua bán lại trái phiếu Chính phủ", 2),
    Line("165", "5. Tài sản ngắn hạn khác", 2),
    Line("B", "B - TÀI SẢN DÀI HẠN", 0),
    Line(
        "200",
        "TÀI SẢN DÀI HẠN",
        0,
        ("210", "220", "230", "240", "250", "260", "270"),
    ),
    Line(
        "210",
        "I. Các khoản phải thu dài hạn",
        1,
        ("211", "212", "213", "214", "215", "216"),
    ),
    Line("211", "1. Phải thu dài hạn của khách hàng", 2),
    Line("212", "2. Trả trước cho người bán dài hạn", 2),
    Line("213", "3. Vốn kinh doanh ở đơn vị trực thuộc", 2),
    Line("214", "4. Phải thu nội bộ dài hạn", 2),
    Line("215", "5. Phải thu dài hạn khác", 2),
    Line("216", "6. Dự phòng phải thu dài hạn khó đòi (*)", 2, negative=True),
    Line("220", "II. Tài sản cố định", 1, ("221", "224", "227")),
    Line("221", "1. Tài sản cố định hữu hình", 2, ("222", "223")),
    Line("222", "- Nguyên giá", 2),
    Line("223", "- Giá trị hao mòn lũy kế (*)", 2, negative=True),
    Line("224", "2. Tài sản cố định thuê tài chính", 2, ("225", "226")),
    Line("225", "- Nguyên giá", 2),
    Line("226", "- Giá trị hao mòn lũy kế (*)", 2, negative=True),
    Line("227", "3. Tài sản cố định vô hình", 2, ("228", "229")),
    Line("228", "- Nguyên giá", 2),
    Line("229", "- Giá trị hao mòn lũy kế (*)", 2, negative=True),
    Line(
        "230", "III. Tài sản sinh học dài hạn", 1, ("231", "236", "237", "238")
    ),
    Line("231", "1. Súc vật nuôi cho sản phẩm định kỳ", 2, ("232", "233")),
    Line(
        "232",
        "a) Súc vật nuôi cho sản phẩm định kỳ chưa đến giai đoạn trưởng thành",
        2,
    ),
    Line(
        "233",
        "b) Súc vật nuôi cho sản phẩm định kỳ đến giai đoạn trưởng thành",
        2,
        ("234", "235"),
    ),
    Line("234", "- Nguyên giá", 2),
    Line("235", "- Giá trị khấu hao lũy kế (*)", 2, negative=True),
    Line("236", "2. Súc vật nuôi lấy sản phẩm một lần dài hạn", 2),
    Line(
        "237", "3. Cây trồng theo mùa vụ hoặc lấy sản phẩm một lần dài hạn", 2
    ),
    Line(
        "238",
        "4. Dự phòng tổn thất tài sản sinh học dài hạn (*)",
        2,
        negative=True,
    ),
    Line("240", "IV. Bất động sản đầu tư", 1, ("241", "242")),
    Line("241", "- Nguyên giá", 2),
    Line("242", "- Giá trị hao mòn lũy kế (*)", 2, negative=True),
    Line("250", "V. Tài sản dở dang dài hạn", 1, ("251", "252")),
    Line("251", "1. Chi phí sản xuất, kinh doanh dở dang dài hạn", 2),
    Line("252", "2. Chi phí xây dựng cơ bản dở dang", 2),
    Line(
        "260",
        "VI. Đầu tư tài chính dài hạn",
        1,
        ("261", "262", "263", "264", "265", "266"),
    ),
    Line("261", "1. Đầu tư vào công ty con", 2),
    Line("262", "2. Đầu tư vào công ty liên doanh, liên kết", 2),
    Line("263", "3. Đầu tư góp vốn vào đơn vị khác", 2),
    Line(
        "264",
        "4. Dự phòng tổn thất đầu tư vào đơn vị khác dài hạn (*)",
        2,
        negative=True,
    ),
    Line("265", "5. Đầu tư nắm giữ đến ngày đáo hạn dài hạn", 2),
    Line(
        "266",
        "6. Dự phòng đầu tư nắm giữ đến ngày đáo hạn dài hạn (*)",
        2,
        negative=True,
    ),
    Line("270", "VII. Tài sản dài hạn khác", 1, ("271", "272", "273", "274")),
    Line("271", "1. Chi phí chờ phân bổ dài hạn", 2),
    Line("272", "2. Tài sản thuế thu nhập hoãn lại", 2),
    Line("273", "3. Thiết bị, vật tư, phụ tùng thay thế dài hạn", 2),
    Line("274", "4. Tài sản dài hạn khác", 2),
    Line("280", "TỔNG CỘNG TÀI SẢN (280 = 100 + 200)", 0, ("100", "200")),
    Line("C", "C - NỢ PHẢI TRẢ", 0),
    Line("300", "NỢ PHẢI TRẢ (300 = 310 + 330)", 0, ("310", "330")),
    Line("310", "I. Nợ ngắn hạn", 1, tuple(str(c) for c in range(311, 326))),
    Line("311", "1. Phải trả người bán ngắn hạn", 2),
    Line("312", "2. Người mua trả tiền trước ngắn hạn", 2),
    Line("313", "3. Phải trả cổ tức, lợi nhuận", 2),
    Line("314", "4. Thuế và các khoản phải nộp Nhà nước ngắn hạn", 2),
    Line("315", "5. Phải trả người lao động", 2),
    Line("316", "6. Chi phí phải trả ngắn hạn", 2),
    Line("317", "7. Phải trả nội bộ ngắn hạn", 2),
    Line("318", "8. Phải trả theo tiến độ hợp đồng xây dựng ngắn hạn", 2),
    Line("319", "9. Doanh thu chờ phân bổ ngắn hạn", 2),
    Line("320", "10. Phải trả ngắn hạn khác", 2),
    Line("321", "11. Vay và nợ thuê tài chính ngắn hạn", 2),
    Line("322", "12. Dự phòng phải trả ngắn hạn", 2),
    Line("323", "13. Quỹ khen thưởng, phúc lợi", 2),
    Line("324", "14. Quỹ bình ổn giá", 2),
    Line("325", "15. Giao dịch mua bán lại trái phiếu Chính phủ", 2),
    Line("330", "II. Nợ dài hạn", 1, tuple(str(c) for c in range(331, 345))),
    Line("331", "1. Phải trả người bán dài hạn", 2),
    Line("332", "2. Người mua trả tiền trước dài hạn", 2),
    Line("333", "3. Thuế và các khoản phải nộp Nhà nước dài hạn", 2),
    Line("334", "4. Chi phí phải trả dài hạn", 2),
    Line("335", "5. Phải trả nội bộ về vốn kinh doanh", 2),
    Line("336", "6. Phải trả nội bộ dài hạn", 2),
    Line("337", "7. Doanh thu chờ phân bổ dài hạn", 2),
    Line("338", "8. Phải trả dài hạn khác", 2),
    Line("339", "9. Vay và nợ thuê tài chính dài hạn", 2),
    Line("340", "10. Trái phiếu chuyển đổi", 2),
    Line("341", "11. Cổ phiếu ưu đãi", 2),
    Line("342", "12. Thuế thu nhập hoãn lại phải trả", 2),
    Line("343", "13. Dự phòng phải trả dài hạn", 2),
    Line("344", "14. Quỹ phát triển khoa học và công nghệ", 2),
    Line("D", "D - VỐN CHỦ SỞ HỮU", 0),
    Line(
        "400",
        "VỐN CHỦ SỞ HỮU",
        0,
        ("411", "412", "413", "414", "415", "416", "417", "418", "419", "420"),
    ),
    Line("411", "1. Vốn góp của chủ sở hữu", 2, ("411a", "411b")),
    Line("411a", "- Cổ phiếu phổ thông có quyền biểu quyết", 2),
    Line("411b", "- Cổ phiếu ưu đãi", 2),
    Line("412", "2. Thặng dư vốn", 2),
    Line("413", "3. Quyền chọn chuyển đổi trái phiếu", 2),
    Line("414", "4. Vốn khác của chủ sở hữu", 2),
    Line("415", "5. Cổ phiếu mua lại của chính mình (*)", 2, negative=True),
    Line("416", "6. Chênh lệch đánh giá lại tài sản", 2),
    Line("417", "7. Chênh lệch tỷ giá hối đoái", 2),
    Line("418", "8. Quỹ đầu tư phát triển", 2),
    Line("419", "9. Quỹ khác thuộc vốn chủ sở hữu", 2),
    Line("420", "10. Lợi nhuận sau thuế chưa phân phối", 2, ("420a", "420b")),
    Line("420a", "- LNST chưa phân phối lũy kế đến cuối kỳ trước", 2),
    Line("420b", "- LNST chưa phân phối kỳ này", 2),
    Line("440", "TỔNG CỘNG NGUỒN VỐN (440 = 300 + 400)", 0, ("300", "400")),
]
# Liability and equity lines show credit balances.
SOURCE_LINES = {
    ln.code for ln in B01_LINES if ln.code[:1] in ("3", "4") and ln.code != "4"
}


@dataclass(frozen=True)
class Rule:
    prefix: str
    side: str  # D | C | * (balance side)
    short: str | None  # line when classified short-term
    long: str | None  # line when classified long-term
    default: str = "S"  # S | L
    # The split depends on the remaining term: warn when the balance
    # was classified by default.
    judgement: bool = False


R = Rule
B01_RULES: list[Rule] = [
    R("111", "*", "111", None),
    R("112", "*", "111", None),
    R("113", "*", "111", None),
    R("121", "*", "121", None),
    R("2291", "*", "122", None),
    R("128", "*", "123", "265", judgement=True),
    R("2292", "*", "126", "264", "L"),
    R("2281", "*", "125", "263", "L", judgement=True),
    R("2288", "*", "165", "274", judgement=True),
    R("221", "*", None, "261", "L"),
    R("222", "*", None, "262", "L"),
    R("131", "D", "131", "211", judgement=True),
    R("131", "C", "312", "332", judgement=True),
    R("331", "D", "132", "212", judgement=True),
    R("331", "C", "311", "331", judgement=True),
    R("1361", "*", None, "213", "L"),
    R("136", "D", "133", "214", judgement=True),
    R("136", "C", "317", "336", judgement=True),
    R("337", "D", "134", None),
    R("337", "C", "318", None),
    R("138", "D", "135", "215", judgement=True),
    R("138", "C", "320", "338", judgement=True),
    R("1381", "*", "137", None),
    R("1383", "*", "163", None),
    R("141", "D", "135", "215"),
    R("141", "C", "320", None),
    R("244", "*", "135", "215", judgement=True),
    R("334", "D", "135", "215"),
    R("334", "C", "315", None),
    R("338", "D", "135", "215"),
    R("338", "C", "320", "338", judgement=True),
    R("3387", "*", "319", "337", judgement=True),
    R("2293", "*", "136", "216", judgement=True),
    R("15", "*", "141", None),
    R("154", "*", "141", "251"),
    R("153", "*", "141", "273"),
    R("2294", "*", "142", None),
    R("2152", "*", "151", "236", judgement=True),
    R("2153", "*", "152", "237", judgement=True),
    R("2295", "*", "153", "238"),
    R("21511", "*", None, "232", "L"),
    R("215121", "*", None, "234", "L"),
    R("215122", "*", None, "235", "L"),
    R("242", "*", "161", "271", judgement=True),
    R("133", "*", "162", None),
    R("333", "D", "163", None),
    R("333", "C", "314", "333"),
    R("171", "D", "164", None),
    R("171", "C", "325", None),
    R("211", "*", None, "222", "L"),
    R("2141", "*", None, "223", "L"),
    R("212", "*", None, "225", "L"),
    R("2142", "*", None, "226", "L"),
    R("213", "*", None, "228", "L"),
    R("2143", "*", None, "229", "L"),
    R("217", "*", None, "241", "L"),
    R("2147", "*", None, "242", "L"),
    R("241", "*", None, "252", "L"),
    R("243", "*", None, "272", "L"),
    R("332", "*", "313", None),
    R("335", "*", "316", "334", judgement=True),
    R("3361", "*", None, "335", "L"),
    R("336", "*", "317", "336", judgement=True),
    R("344", "*", "320", "338", judgement=True),
    R("341", "*", "321", "339", judgement=True),
    R("3431", "*", "321", "339", "L"),
    R("3432", "*", None, "340", "L"),
    R("347", "*", None, "342", "L"),
    R("352", "*", "322", "343", judgement=True),
    R("353", "*", "323", None),
    R("357", "*", "324", None),
    R("356", "*", None, "344", "L"),
    R("4111", "*", "411a", None),
    R("41112", "*", "411b", None),
    R("4112", "*", "412", None),
    R("4113", "*", "413", None),
    R("4118", "*", "414", None),
    R("419", "*", "415", None),
    R("412", "*", "416", None),
    R("413", "*", "417", None),
    R("414", "*", "418", None),
    R("418", "*", "419", None),
    R("4211", "*", "420a", None),
    R("4212", "*", "420b", None),
    # Unclosed class 5–9 balances belong to this year's profit.
    R("5", "*", "420b", None),
    R("6", "*", "420b", None),
    R("7", "*", "420b", None),
    R("8", "*", "420b", None),
    R("9", "*", "420b", None),
]


def _rule_for(account: str, side: str) -> Rule | None:
    best, best_len = None, -1
    for r in B01_RULES:
        if (
            (r.side == "*" or r.side == side)
            and account.startswith(r.prefix)
            and len(r.prefix) > best_len
        ):
            best, best_len = r, len(r.prefix)
    return best


def term_settings(company: Company) -> tuple[tuple, tuple]:
    cfg = company.setting("b01", {}) or {}
    return (
        tuple(str(x) for x in cfg.get("long_term", []) if x),
        tuple(str(x) for x in cfg.get("short_term", []) if x),
    )


def _b01_values(
    db: Session, company: Company, d: date
) -> tuple[dict[str, int], dict[str, int], int, set[str]]:
    long_set, short_set = term_settings(company)
    values: dict[str, int] = {}
    unmapped: dict[str, int] = {}
    unclosed = 0
    judged: set[str] = set()
    for (acc, _pid), net in _grouped_balances(db, company.id, d).items():
        side = "D" if net > 0 else "C"
        rule = _rule_for(acc, side)
        if rule is None:
            unmapped[acc] = unmapped.get(acc, 0) + net
            continue
        term = rule.default
        if acc.startswith(long_set):
            term = "L"
        elif acc.startswith(short_set):
            term = "S"
        elif rule.judgement:
            judged.add(rule.prefix)
        code = (
            (rule.long or rule.short)
            if term == "L"
            else (rule.short or rule.long)
        )
        if acc[:1] in "56789":
            unclosed += net
        values[code] = values.get(code, 0) + (
            -net if code in SOURCE_LINES else net
        )
    _calc_formulas(B01_LINES, values)
    return values, unmapped, unclosed, judged


def b01(db: Session, company: Company, period: ReportPeriod) -> Statement:
    cur, unmapped, unclosed, judged = _b01_values(db, company, period.end)
    year_start = date(period.end.year, 1, 1)
    prev_date = day_before(max(year_start, company.start_date))
    prev, _u, _x, _j = _b01_values(db, company, prev_date)
    rows = [
        StatementRow(ln, cur.get(ln.code, 0), prev.get(ln.code, 0))
        for ln in B01_LINES
    ]
    st = Statement(
        "B01-DN",
        "BÁO CÁO TÌNH HÌNH TÀI CHÍNH",
        rows,
        "Số cuối năm" if period.mode == "year" else "Số cuối kỳ",
        "Số đầu năm",
        unmapped=unmapped,
    )
    if judged:
        st.warnings.append(
            "Số dư các tài khoản "
            + ", ".join(sorted(judged))
            + " được trình bày theo mặc định (ngắn hạn, trừ đầu tư góp vốn). "
            "Tách phần dài hạn (kỳ hạn còn lại trên 12 tháng) bằng thiết "
            "lập “Tài khoản trình bày dài hạn”."
        )
    if unclosed:
        st.warnings.append(
            "Còn số dư tài khoản doanh thu, chi phí chưa kết chuyển "
            "(đã tính vào chỉ tiêu 420b). "
            "Nên chạy Kết chuyển cuối kỳ trước khi lập báo cáo."
        )
    if unmapped:
        st.warnings.append(
            "Có số dư tài khoản chưa được xếp vào chỉ tiêu nào: "
            + ", ".join(sorted(unmapped))
        )
    if st.value("280") != st.value("440"):
        st.warnings.append(
            "Tổng tài sản khác tổng nguồn vốn – kiểm tra số dư đầu kỳ "
            "và Bảng cân đối số phát sinh."
        )
    return st


# ============================================================ B02-DN

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
    Line("21", "6. Lãi/lỗ của hoạt động bán, thanh lý bất động sản đầu tư"),
    Line("22", "7. Doanh thu hoạt động tài chính"),
    Line("23", "8. Chi phí tài chính"),
    Line("24", "- Trong đó: Chi phí đi vay", 2),
    Line("25", "9. Chi phí bán hàng"),
    Line("26", "10. Chi phí quản lý doanh nghiệp"),
    Line(
        "30",
        "11. Lợi nhuận thuần từ hoạt động kinh doanh "
        "(30 = 20 + 21 + 22 - (23 + 25 + 26))",
        0,
        ("20", "21", "22", "-23", "-25", "-26"),
    ),
    Line("31", "12. Thu nhập khác"),
    Line("32", "13. Chi phí khác"),
    Line("40", "14. Lợi nhuận khác (40 = 31 - 32)", 0, ("31", "-32")),
    Line(
        "50",
        "15. Tổng lợi nhuận kế toán trước thuế (50 = 30 + 40)",
        0,
        ("30", "40"),
    ),
    Line("51", "16. Chi phí thuế TNDN hiện hành"),
    Line("52", "17. Chi phí thuế TNDN hoãn lại"),
    Line(
        "60",
        "18. Lợi nhuận sau thuế thu nhập doanh nghiệp (60 = 50 - (51 + 52))",
        0,
        ("50", "-51", "-52"),
    ),
    Line("70", "19. Lãi cơ bản trên cổ phiếu (*)"),
    Line("71", "20. Lãi suy giảm trên cổ phiếu (*)"),
]


def _b02_values(
    db: Session, company_id: int, start: date, end: date
) -> dict[str, int]:
    f = books.flows(db, company_id, start, end, exclude_closing=True)
    interest = _interest_accounts(db, company_id)
    v = {
        "01": _sum_flow(f, "511", "credit"),
        # Returns and allowances: 521 (TT99) or directly on 511.
        "02": _sum_flow(f, "511", "debit") + _sum_flow(f, "521", "net_debit"),
        "11": _sum_flow(f, "632", "net_debit"),
        "21": 0,
        "22": _sum_flow(f, "515", "net_credit"),
        "23": _sum_flow(f, "635", "net_debit"),
        "24": _sum_flow(f, interest, "net_debit") if interest else 0,
        "25": _sum_flow(f, "641", "net_debit"),
        "26": _sum_flow(f, "642", "net_debit"),
        "31": _sum_flow(f, "711", "net_credit"),
        "32": _sum_flow(f, "811", "net_debit"),
        # Current tax: 8211 and its details (and 821 itself if the
        # company posts there without detail).
        "51": _sum_flow(f, "821", "net_debit")
        - _sum_flow(f, "8212", "net_debit"),
        "52": _sum_flow(f, "8212", "net_debit"),
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
    return Statement(
        "B02-DN",
        "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
        rows,
        "Năm nay" if period.mode == "year" else "Kỳ này",
        "Năm trước" if period.mode == "year" else "Cùng kỳ năm trước",
    )


# ============================================ B03-DN (direct method)

B03_LINES: list[Line] = [
    Line("I", "I. Lưu chuyển tiền từ hoạt động kinh doanh", 1),
    Line(
        "01", "1. Tiền thu từ bán hàng, cung cấp dịch vụ và doanh thu khác", 2
    ),
    Line("02", "2. Tiền chi trả cho người cung cấp hàng hóa và dịch vụ", 2),
    Line("03", "3. Tiền chi trả cho người lao động", 2),
    Line("04", "4. Chi phí đi vay đã trả", 2),
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
        "1. Tiền chi để mua sắm, xây dựng TSCĐ và các tài sản dài hạn khác",
        2,
    ),
    Line(
        "22",
        "2. Tiền thu từ thanh lý, nhượng bán TSCĐ và các tài sản dài hạn khác",
        2,
    ),
    Line("23", "3. Tiền chi cho vay, mua các công cụ nợ của đơn vị khác", 2),
    Line(
        "24",
        "4. Tiền thu hồi cho vay, bán lại các công cụ nợ của đơn vị khác",
        2,
    ),
    Line("25", "5. Tiền chi đầu tư góp vốn vào đơn vị khác", 2),
    Line("26", "6. Tiền thu hồi đầu tư góp vốn vào đơn vị khác", 2),
    Line("27", "7. Tiền thu lãi cho vay, cổ tức và lợi nhuận được chia", 2),
    Line(
        "30",
        "Lưu chuyển tiền thuần từ hoạt động đầu tư",
        0,
        ("21", "22", "23", "24", "25", "26", "27"),
    ),
    Line("III", "III. Lưu chuyển tiền từ hoạt động tài chính", 1),
    Line(
        "31",
        "1. Tiền thu từ phát hành cổ phiếu, nhận vốn góp của chủ sở hữu",
        2,
    ),
    Line(
        "32",
        "2. Tiền trả lại vốn góp cho các chủ sở hữu, mua lại cổ phiếu "
        "của doanh nghiệp đã phát hành",
        2,
    ),
    Line("33", "3. Tiền thu từ đi vay", 2),
    Line("34", "4. Tiền trả nợ gốc vay", 2),
    Line("35", "5. Tiền trả nợ gốc thuê tài chính", 2),
    Line("36", "6. Cổ tức, lợi nhuận đã trả cho chủ sở hữu", 2),
    Line(
        "40",
        "Lưu chuyển tiền thuần từ hoạt động tài chính",
        0,
        ("31", "32", "33", "34", "35", "36"),
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
CASH_PREFIXES = ("111", "112", "113")
B03_IN: list[tuple[str, str]] = [
    ("511", "01"),
    ("131", "01"),
    ("3331", "01"),
    ("3387", "01"),
    ("121", "01"),
    ("515", "27"),
    ("711", "06"),
    ("128", "24"),
    ("171", "24"),
    ("221", "26"),
    ("222", "26"),
    ("228", "26"),
    ("411", "31"),
    ("3411", "33"),
    ("343", "33"),
    ("41112", "33"),
]
B03_OUT: list[tuple[str, str]] = [
    ("331", "02"),
    ("15", "02"),
    ("133", "02"),
    ("62", "02"),
    ("632", "02"),
    ("641", "02"),
    ("642", "02"),
    ("242", "02"),
    ("131", "02"),
    ("334", "03"),
    ("635", "04"),
    ("335", "04"),
    ("3334", "05"),
    ("211", "21"),
    ("212", "21"),
    ("213", "21"),
    ("217", "21"),
    ("241", "21"),
    ("128", "23"),
    ("171", "23"),
    ("221", "25"),
    ("222", "25"),
    ("228", "25"),
    ("411", "32"),
    ("419", "32"),
    ("3411", "34"),
    ("343", "34"),
    ("3412", "35"),
    ("421", "36"),
    ("332", "36"),
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
            continue
        inflow = bool(debit)
        amount = int(debit) if inflow else -int(credit)
        code = (
            override
            if override in B03_CODES
            else classify_cash(contra, inflow)
        )
        v[code] = v.get(code, 0) + amount
    v["60"] = sum(
        net
        for (acc, _p), net in books.balances_at(
            db, company_id, day_before(start)
        ).items()
        if acc.startswith(CASH_PREFIXES)
    )
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
        "B03-DN",
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
            "Tiền cuối kỳ trên báo cáo khác số dư TK 111 + 112 + 113."
        )
    return st


STATEMENTS = {"b01a": b01, "b02": b02, "b03": b03}
