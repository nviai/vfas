"""Chart of accounts under Circular 133/2016/TT-BTC (Appendix 1).

Each row: (code, name, normal balance, flags)
  balance: D = debit, C = credit, B = both, N = no balance (TK 911)
  flags:   P = partner required, p = partner optional,
           K = bank account required,
           I = tracks inventory items (optional),
           A = tracks fixed assets (optional),
           X = must not go negative (checked retroactively)
Parent accounts (those with sub-accounts) cannot be posted to directly.
"""

from __future__ import annotations

from dataclasses import dataclass

ISSUED = (
    "(Ban hành theo Thông tư số 133/2016/TT-BTC "
    "ngày 26/8/2016 của Bộ Tài chính)"
)

CHART: tuple[tuple[str, str, str, str], ...] = (
    # Class 1 – Assets
    ("111", "Tiền mặt", "D", ""),
    ("1111", "Tiền Việt Nam", "D", "X"),
    ("1112", "Ngoại tệ", "D", "X"),
    ("112", "Tiền gửi ngân hàng", "D", ""),
    ("1121", "Tiền Việt Nam", "D", "KX"),
    ("1122", "Ngoại tệ", "D", "KX"),
    ("121", "Chứng khoán kinh doanh", "D", ""),
    ("128", "Đầu tư nắm giữ đến ngày đáo hạn", "D", ""),
    ("1281", "Tiền gửi có kỳ hạn", "D", ""),
    ("1288", "Các khoản đầu tư khác nắm giữ đến ngày đáo hạn", "D", ""),
    ("131", "Phải thu của khách hàng", "B", "P"),
    ("133", "Thuế GTGT được khấu trừ", "D", ""),
    ("1331", "Thuế GTGT được khấu trừ của hàng hóa, dịch vụ", "D", ""),
    ("1332", "Thuế GTGT được khấu trừ của TSCĐ", "D", ""),
    ("136", "Phải thu nội bộ", "B", ""),
    ("1361", "Vốn kinh doanh ở đơn vị trực thuộc", "D", "p"),
    ("1368", "Phải thu nội bộ khác", "B", "p"),
    ("138", "Phải thu khác", "B", ""),
    ("1381", "Tài sản thiếu chờ xử lý", "D", ""),
    ("1386", "Cầm cố, thế chấp, ký quỹ, ký cược", "D", "p"),
    ("1388", "Phải thu khác", "B", "p"),
    ("141", "Tạm ứng", "D", "P"),
    ("151", "Hàng mua đang đi đường", "D", "I"),
    ("152", "Nguyên liệu, vật liệu", "D", "I"),
    ("153", "Công cụ, dụng cụ", "D", "I"),
    ("154", "Chi phí sản xuất, kinh doanh dở dang", "D", ""),
    ("155", "Thành phẩm", "D", "I"),
    ("156", "Hàng hóa", "D", "I"),
    ("157", "Hàng gửi đi bán", "D", "I"),
    ("211", "Tài sản cố định", "D", ""),
    ("2111", "TSCĐ hữu hình", "D", "A"),
    ("2112", "TSCĐ thuê tài chính", "D", "A"),
    ("2113", "TSCĐ vô hình", "D", "A"),
    ("214", "Hao mòn tài sản cố định", "C", ""),
    ("2141", "Hao mòn TSCĐ hữu hình", "C", "A"),
    ("2142", "Hao mòn TSCĐ thuê tài chính", "C", "A"),
    ("2143", "Hao mòn TSCĐ vô hình", "C", "A"),
    ("2147", "Hao mòn bất động sản đầu tư", "C", ""),
    ("217", "Bất động sản đầu tư", "D", ""),
    ("228", "Đầu tư góp vốn vào đơn vị khác", "D", ""),
    ("2281", "Đầu tư vào công ty liên doanh, liên kết", "D", "p"),
    ("2288", "Đầu tư khác", "D", "p"),
    ("229", "Dự phòng tổn thất tài sản", "C", ""),
    ("2291", "Dự phòng giảm giá chứng khoán kinh doanh", "C", ""),
    ("2292", "Dự phòng tổn thất đầu tư vào đơn vị khác", "C", ""),
    ("2293", "Dự phòng phải thu khó đòi", "C", "p"),
    ("2294", "Dự phòng giảm giá hàng tồn kho", "C", ""),
    ("241", "Xây dựng cơ bản dở dang", "D", ""),
    ("2411", "Mua sắm TSCĐ", "D", ""),
    ("2412", "Xây dựng cơ bản", "D", ""),
    ("2413", "Sửa chữa lớn TSCĐ", "D", ""),
    ("242", "Chi phí trả trước", "D", ""),
    # Class 3 – Liabilities
    ("331", "Phải trả cho người bán", "B", "P"),
    ("333", "Thuế và các khoản phải nộp Nhà nước", "B", ""),
    ("3331", "Thuế giá trị gia tăng phải nộp", "B", ""),
    ("33311", "Thuế GTGT đầu ra", "B", ""),
    ("33312", "Thuế GTGT hàng nhập khẩu", "B", ""),
    ("3332", "Thuế tiêu thụ đặc biệt", "B", ""),
    ("3333", "Thuế xuất, nhập khẩu", "B", ""),
    ("3334", "Thuế thu nhập doanh nghiệp", "B", ""),
    ("3335", "Thuế thu nhập cá nhân", "B", ""),
    ("3336", "Thuế tài nguyên", "B", ""),
    ("3337", "Thuế nhà đất, tiền thuê đất", "B", ""),
    ("3338", "Thuế bảo vệ môi trường và các loại thuế khác", "B", ""),
    ("33381", "Thuế bảo vệ môi trường", "B", ""),
    ("33382", "Các loại thuế khác", "B", ""),
    ("3339", "Phí, lệ phí và các khoản phải nộp khác", "B", ""),
    ("334", "Phải trả người lao động", "B", "p"),
    ("335", "Chi phí phải trả", "C", "p"),
    ("336", "Phải trả nội bộ", "B", ""),
    ("3361", "Phải trả nội bộ về vốn kinh doanh", "C", "p"),
    ("3368", "Phải trả nội bộ khác", "B", "p"),
    ("338", "Phải trả, phải nộp khác", "B", ""),
    ("3381", "Tài sản thừa chờ giải quyết", "C", ""),
    ("3382", "Kinh phí công đoàn", "B", ""),
    ("3383", "Bảo hiểm xã hội", "B", ""),
    ("3384", "Bảo hiểm y tế", "B", ""),
    ("3385", "Bảo hiểm thất nghiệp", "B", ""),
    ("3386", "Nhận ký quỹ, ký cược", "C", "p"),
    ("3387", "Doanh thu chưa thực hiện", "C", "p"),
    ("3388", "Phải trả, phải nộp khác", "B", "p"),
    ("341", "Vay và nợ thuê tài chính", "C", ""),
    ("3411", "Các khoản đi vay", "C", "p"),
    ("3412", "Nợ thuê tài chính", "C", "p"),
    ("352", "Dự phòng phải trả", "C", ""),
    ("3521", "Dự phòng bảo hành sản phẩm hàng hóa", "C", ""),
    ("3522", "Dự phòng bảo hành công trình xây dựng", "C", ""),
    ("3524", "Dự phòng phải trả khác", "C", ""),
    ("353", "Quỹ khen thưởng phúc lợi", "C", ""),
    ("3531", "Quỹ khen thưởng", "C", ""),
    ("3532", "Quỹ phúc lợi", "C", ""),
    ("3533", "Quỹ phúc lợi đã hình thành TSCĐ", "C", ""),
    ("3534", "Quỹ thưởng ban quản lý điều hành công ty", "C", ""),
    ("356", "Quỹ phát triển khoa học và công nghệ", "C", ""),
    ("3561", "Quỹ phát triển khoa học và công nghệ", "C", ""),
    (
        "3562",
        "Quỹ phát triển khoa học và công nghệ đã hình thành TSCĐ",
        "C",
        "",
    ),
    # Class 4 – Owner's equity
    ("411", "Vốn đầu tư của chủ sở hữu", "C", ""),
    ("4111", "Vốn góp của chủ sở hữu", "C", "p"),
    ("4112", "Thặng dư vốn cổ phần", "B", ""),
    ("4118", "Vốn khác", "C", ""),
    ("413", "Chênh lệch tỷ giá hối đoái", "B", ""),
    ("418", "Các quỹ thuộc vốn chủ sở hữu", "C", ""),
    ("419", "Cổ phiếu quỹ", "D", ""),
    ("421", "Lợi nhuận sau thuế chưa phân phối", "B", ""),
    ("4211", "Lợi nhuận sau thuế chưa phân phối năm trước", "B", ""),
    ("4212", "Lợi nhuận sau thuế chưa phân phối năm nay", "B", ""),
    # Class 5 – Revenue
    ("511", "Doanh thu bán hàng và cung cấp dịch vụ", "C", ""),
    ("5111", "Doanh thu bán hàng hóa", "C", ""),
    ("5112", "Doanh thu bán thành phẩm", "C", ""),
    ("5113", "Doanh thu cung cấp dịch vụ", "C", ""),
    ("5118", "Doanh thu khác", "C", ""),
    ("515", "Doanh thu hoạt động tài chính", "C", ""),
    # Class 6 – Production and business costs
    ("611", "Mua hàng", "D", ""),
    ("631", "Giá thành sản xuất", "D", ""),
    ("632", "Giá vốn hàng bán", "D", ""),
    ("635", "Chi phí tài chính", "D", ""),
    ("642", "Chi phí quản lý kinh doanh", "D", ""),
    ("6421", "Chi phí bán hàng", "D", ""),
    ("6422", "Chi phí quản lý doanh nghiệp", "D", ""),
    # Classes 7, 8, 9
    ("711", "Thu nhập khác", "C", ""),
    ("811", "Chi phí khác", "D", ""),
    ("821", "Chi phí thuế thu nhập doanh nghiệp", "D", ""),
    ("911", "Xác định kết quả kinh doanh", "N", ""),
)

KIND_BY_CLASS = {
    "1": "asset",
    "2": "asset",
    "3": "liability",
    "4": "equity",
    "5": "revenue",
    "6": "expense",
    "7": "revenue",
    "8": "expense",
    "9": "result",
}
NORMAL = {"D": "debit", "C": "credit", "B": "both", "N": "none"}


@dataclass(frozen=True)
class ChartAccount:
    code: str
    name: str
    parent_code: str | None
    level: int
    kind: str
    normal: str
    is_postable: bool
    partner_mode: str  # none | optional | required
    needs_bank: bool
    tracks_item: bool
    tracks_asset: bool
    no_negative: bool


def _parent(code: str, codes: set[str]) -> str | None:
    for n in range(len(code) - 1, 2, -1):
        if code[:n] in codes:
            return code[:n]
    return None


def chart_accounts() -> list[ChartAccount]:
    codes = {c[0] for c in CHART}
    parents = {_parent(c, codes) for c in codes} - {None}
    out = []
    for code, name, normal, flags in CHART:
        parent = _parent(code, codes)
        level = 1
        p = parent
        while p:
            level += 1
            p = _parent(p, codes)
        out.append(
            ChartAccount(
                code=code,
                name=name,
                parent_code=parent,
                level=level,
                kind=KIND_BY_CLASS[code[0]],
                normal=NORMAL[normal],
                is_postable=code not in parents,
                partner_mode="required"
                if "P" in flags
                else "optional"
                if "p" in flags
                else "none",
                needs_bank="K" in flags,
                tracks_item="I" in flags,
                tracks_asset="A" in flags,
                no_negative="X" in flags,
            )
        )
    return out
