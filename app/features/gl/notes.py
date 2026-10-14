"""Notes to the financial statements (B09-DNN under TT133, B09-DN
under TT99).

The layout follows TT133; under TT99 the account groups and the
section numbers of the income items (VII) follow the B09-DN form. The
main figure tables are filled in automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.core.regimes import regime_of
from app.features.gl import books
from app.features.gl.periods import ReportPeriod, day_before
from app.features.org.models import Company

BUSINESS = {
    "service": "Dịch vụ",
    "trade": "Thương mại",
    "production": "Sản xuất",
    "construction": "Xây lắp",
}


@dataclass
class NoteTable:
    code: str
    title: str
    columns: tuple[str, str] = ("Cuối kỳ", "Đầu năm")
    # (label, column 1, column 2)
    rows: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def total(self) -> tuple[int, int]:
        return sum(r[1] for r in self.rows), sum(r[2] for r in self.rows)


@dataclass
class Notes:
    period: ReportPeriod
    general: list[tuple[str, str]]
    policies: list[tuple[str, str]]
    balance_tables: list[NoteTable]
    income_tables: list[NoteTable]


def _debit(bal: dict, prefixes) -> int:
    return sum(
        v for (a, _p), v in bal.items() if a.startswith(prefixes) and v > 0
    )


def _credit(bal: dict, prefixes) -> int:
    return sum(
        -v for (a, _p), v in bal.items() if a.startswith(prefixes) and v < 0
    )


def _net_credit(bal: dict, prefixes) -> int:
    return -sum(v for (a, _p), v in bal.items() if a.startswith(prefixes))


def build(db: Session, company: Company, period: ReportPeriod) -> Notes:
    regime = regime_of(company)
    tt99 = regime.code == "tt99"
    deposits = regime.account("deposits_paid")
    accs = books.accounts_map(db, company.id)
    end = period.end
    year_start = day_before(max(date(end.year, 1, 1), company.start_date))
    cur = books.balances_at(db, company.id, end, by_partner=True)
    prev = books.balances_at(db, company.id, year_start, by_partner=True)

    def tbl(code, title, items, fn):
        t = NoteTable(code, title)
        for label, prefixes in items:
            a, b = fn(cur, prefixes), fn(prev, prefixes)
            if a or b:
                t.rows.append((label, a, b))
        return t

    balance_tables = [
        tbl(
            "V.01",
            "Tiền",
            [
                ("Tiền mặt", ("111",)),
                ("Tiền gửi ngân hàng không kỳ hạn", ("112",)),
                ("Tiền đang chuyển", ("113",)),
            ],
            _debit,
        ),
        tbl(
            "V.02",
            "Các khoản phải thu",
            [
                ("Phải thu của khách hàng", ("131",)),
                ("Trả trước cho người bán", ("331",)),
                ("Tạm ứng", ("141",)),
                ("Phải thu khác", (deposits, "1388", "334", "338")),
            ],
            _debit,
        ),
        tbl(
            "V.03",
            "Hàng tồn kho",
            [
                (f"{c} – {accs[c].name}" if c in accs else c, (c,))
                for c in ("151", "152", "153", "154", "155", "156", "157")
            ],
            _debit,
        ),
        tbl(
            "V.05",
            "Thuế và các khoản phải nộp Nhà nước",
            [
                (f"{c} – {accs[c].name}" if c in accs else c, (c,))
                for c in (
                    "3331",
                    "3332",
                    "3333",
                    "3334",
                    "3335",
                    "3336",
                    "3337",
                    "3338",
                    "3339",
                )
            ],
            _credit,
        ),
        tbl(
            "V.06",
            "Phải trả người bán và người mua trả tiền trước",
            [
                ("Phải trả người bán", ("331",)),
                ("Người mua trả tiền trước", ("131",)),
            ],
            _credit,
        ),
        tbl(
            "V.07",
            "Vay và nợ thuê tài chính",
            [("Vay và nợ thuê tài chính", ("341",))],
            _credit,
        ),
        tbl(
            "V.08",
            "Vốn chủ sở hữu",
            [
                ("Vốn góp của chủ sở hữu", ("4111",)),
                ("Thặng dư vốn cổ phần", ("4112",)),
                ("Vốn khác của chủ sở hữu", ("4118",)),
                ("Lợi nhuận sau thuế chưa phân phối", ("421",)),
            ],
            _net_credit,
        ),
    ]
    # Fixed assets: cost and accumulated depreciation
    fa = NoteTable(
        "V.04", "Tài sản cố định", ("Nguyên giá", "Giá trị hao mòn lũy kế")
    )
    for label, cost_p, dep_p, bal in (
        ("Số dư đầu năm", ("211", "212", "213"), "214", prev),
        ("Số dư cuối kỳ", ("211", "212", "213"), "214", cur),
    ):
        fa.rows.append((label, _debit(bal, cost_p), _credit(bal, (dep_p,))))
    if any(r[1] or r[2] for r in fa.rows):
        balance_tables.insert(3, fa)

    f = books.flows(
        db, company.id, period.start, period.end, exclude_closing=True
    )
    prev_p = period.previous_year()
    fp = books.flows(
        db, company.id, prev_p.start, prev_p.end, exclude_closing=True
    )

    income = "VII" if tt99 else "VI"

    def flow_tbl(code, title, items, kind):
        code = code.replace("VI.", f"{income}.", 1)
        t = NoteTable(code, title, ("Kỳ này", "Cùng kỳ năm trước"))
        for label, prefixes in items:

            def val(fl):
                tot = 0
                for acc, (d, c) in fl.items():
                    if acc.startswith(prefixes):
                        tot += (c - d) if kind == "credit" else (d - c)
                return tot

            a, b = val(f), val(fp)
            if a or b:
                t.rows.append((label, a, b))
        return t

    income_tables = [
        flow_tbl(
            "VI.01",
            "Doanh thu bán hàng và cung cấp dịch vụ",
            [
                ("Doanh thu bán hàng hóa", ("5111",)),
                ("Doanh thu bán thành phẩm", ("5112",)),
                ("Doanh thu cung cấp dịch vụ", ("5113",)),
                ("Doanh thu khác", ("5118",)),
            ],
            "credit",
        ),
        flow_tbl(
            "VI.02",
            "Giá vốn hàng bán",
            [("Giá vốn hàng bán", ("632",))],
            "debit",
        ),
        flow_tbl(
            "VI.03",
            "Doanh thu hoạt động tài chính và chi phí tài chính",
            [("Doanh thu hoạt động tài chính", ("515",))],
            "credit",
        ),
        flow_tbl(
            "VI.04",
            "Chi phí tài chính",
            [("Chi phí tài chính", ("635",))],
            "debit",
        ),
        flow_tbl(
            "VI.05",
            "Chi phí bán hàng và chi phí quản lý doanh nghiệp"
            if tt99
            else "Chi phí quản lý kinh doanh",
            [
                ("Chi phí bán hàng", ("641",) if tt99 else ("6421",)),
                (
                    "Chi phí quản lý doanh nghiệp",
                    ("642",) if tt99 else ("6422",),
                ),
            ],
            "debit",
        ),
        flow_tbl(
            "VI.06",
            "Thu nhập khác, chi phí khác",
            [("Thu nhập khác", ("711",)), ("Chi phí khác", ("811",))],
            "credit",
        ),
    ]
    # Show other expenses as a positive amount
    other = income_tables[-1]
    other.rows = [
        (lbl, -a, -b) if lbl == "Chi phí khác" else (lbl, a, b)
        for lbl, a, b in other.rows
    ]

    general = [
        (
            "Hình thức sở hữu vốn",
            "Công ty trách nhiệm hữu hạn / cổ phần (điều chỉnh theo thực tế)",
        ),
        (
            "Lĩnh vực kinh doanh",
            BUSINESS.get(company.business_type, company.business_type or ""),
        ),
        ("Mã số thuế", company.tax_code or ""),
        ("Địa chỉ", company.address or ""),
        (
            "Kỳ kế toán năm",
            "Bắt đầu từ ngày 01/01 và kết thúc vào ngày 31/12 hằng năm",
        ),
        ("Đơn vị tiền tệ sử dụng trong kế toán", "Đồng Việt Nam (VND)"),
        (
            "Chế độ kế toán áp dụng",
            "Chế độ kế toán doanh nghiệp ban hành kèm theo Thông tư số "
            "99/2025/TT-BTC ngày 27/10/2025 của Bộ Tài chính"
            if tt99
            else "Chế độ kế toán doanh nghiệp nhỏ và vừa ban hành theo "
            "Thông tư số 133/2016/TT-BTC ngày 26/8/2016 của Bộ Tài chính",
        ),
        (
            "Hình thức kế toán",
            "Nhật ký chung, thực hiện trên phần mềm kế toán",
        ),
        (
            "Tuyên bố về việc tuân thủ",
            "Báo cáo tài chính được lập và trình bày phù hợp với "
            + (
                "chế độ kế toán doanh nghiệp"
                if tt99
                else "chế độ kế toán doanh nghiệp nhỏ và vừa"
            )
            + " và các quy định pháp lý có liên quan",
        ),
    ]
    policies = [
        (
            "Tiền và các khoản tương đương tiền",
            "Gồm tiền mặt và tiền gửi ngân hàng không kỳ hạn.",
        ),
        (
            "Các khoản phải thu",
            "Ghi nhận theo giá trị ghi sổ, "
            "theo dõi chi tiết theo từng đối tượng.",
        ),
        (
            "Hàng tồn kho",
            "Ghi nhận theo giá gốc; giá xuất kho tính theo phương pháp "
            "bình quân gia quyền cả kỳ; "
            "hạch toán theo phương pháp kê khai thường xuyên.",
        ),
        (
            "Tài sản cố định",
            "Ghi nhận theo nguyên giá; khấu hao theo phương pháp đường thẳng.",
        ),
        (
            "Doanh thu",
            "Ghi nhận khi chuyển giao phần lớn rủi ro và lợi ích, "
            "dịch vụ đã hoàn thành và xác định được "
            "doanh thu một cách đáng tin cậy.",
        ),
        (
            "Chi phí thuế thu nhập doanh nghiệp",
            "Xác định trên thu nhập chịu thuế và thuế suất hiện hành.",
        ),
    ]
    return Notes(
        period,
        general,
        policies,
        [t for t in balance_tables if t.rows],
        [t for t in income_tables if t.rows],
    )
