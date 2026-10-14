"""Fake data and a Jinja environment for the /ui-kit pages.

Also used by tools/render_kit.py. Does not depend on the database or
on core services (the core may still be changing); only the templates
are shared.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
    select_autoescape,
)

APP_DIR = Path(__file__).resolve().parents[2]

try:  # pure modules, no database access
    from app.core.money import amount_in_words as _words
    from app.core.money import format_vnd as _format_vnd
except Exception:  # pragma: no cover - fallback while core is edited

    def _format_vnd(amount, blank_zero=False):
        if amount is None:
            return ""
        amount = int(amount)
        if amount == 0 and blank_zero:
            return ""
        return ("-" if amount < 0 else "") + f"{abs(amount):,}".replace(
            ",", "."
        )

    def _words(amount):
        return f"{_format_vnd(amount)} đồng"


# ---------------------------------------- helpers


class Obj(dict):
    """A dict with attribute access (like the core's objects)."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


def _fmt_date(d):
    if d is None:
        return ""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return d.strftime("%d/%m/%Y")


def _fmt_datetime(d):
    return d.strftime("%d/%m/%Y %H:%M") if d else ""


def _fmt_qty(v, decimals=3):
    if v is None or v == "":
        return ""
    s = f"{float(v):,.{decimals}f}".rstrip("0").rstrip(".")
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _fmt_pct(v, decimals=1):
    if v is None:
        return ""
    return f"{float(v):.{decimals}f}".replace(".", ",") + "%"


def translator(text, **kwargs):
    return text.format(**kwargs) if kwargs else text


def build_env() -> Environment:
    features = APP_DIR / "features"
    prefix = {
        p.name: FileSystemLoader(p / "templates")
        for p in sorted(features.iterdir())
        if (p / "templates").is_dir()
    }
    env = Environment(
        loader=ChoiceLoader(
            [
                FileSystemLoader(APP_DIR / "core" / "templates"),
                PrefixLoader(prefix),
            ]
        ),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
        extensions=[
            "jinja2.ext.do",
            "jinja2.ext.loopcontrols",
        ],  # same as app/core/web.py
    )
    env.filters["money"] = _format_vnd
    env.filters["money0"] = lambda v: _format_vnd(v, blank_zero=True)
    env.filters["qty"] = _fmt_qty
    env.filters["pct"] = _fmt_pct
    env.filters["date"] = _fmt_date
    env.filters["datetime"] = _fmt_datetime
    env.filters["words"] = _words
    env.filters["tojson_attr"] = lambda v: json.dumps(v, ensure_ascii=False)
    return env


# ---------------------------------------- pages

PAGES = [
    (
        "components",
        "Thành phần",
        "Nút, form, bảng, badge, hộp thoại, biểu đồ…",
        "layers",
    ),
    (
        "list",
        "a · Danh sách chứng từ",
        "Bộ lọc, bảng, phân trang, thẻ trên điện thoại",
        "list",
    ),
    (
        "form-entry",
        "b · Phiếu kế toán",
        "Form chứng từ với lưới dòng Nợ/Có",
        "notebook-pen",
    ),
    (
        "form-invoice",
        "b · Hóa đơn bán hàng",
        "Lưới hàng hóa, thuế suất, tổng thanh toán",
        "receipt-text",
    ),
    (
        "detail",
        "c · Chi tiết chứng từ",
        "Trạng thái, định khoản, đính kèm, lịch sử",
        "file-text",
    ),
    (
        "report",
        "d · Bảng cân đối tài khoản",
        "Khuôn báo cáo F01-DNN, xuất PDF/Excel",
        "scale",
    ),
    (
        "dashboard",
        "e · Tổng quan",
        "Thẻ số liệu, biểu đồ, việc cần làm, hạn nộp thuế",
        "layout-dashboard",
    ),
    ("settings", "g · Cài đặt", "Form dạng tab, chia mục", "settings"),
    (
        "ai",
        "h · Trợ lý AI",
        "Drawer chat, chip trạng thái công cụ",
        "sparkles",
    ),
    ("login", "f · Đăng nhập", "Khung auth hai cột", "log-in"),
    ("signup", "f · Đăng ký", "Tạo tài khoản + doanh nghiệp", "user-plus"),
    (
        "setup",
        "f · Thiết lập lần đầu",
        "Tự cài: tài khoản quản trị + doanh nghiệp",
        "settings-2",
    ),
    ("invite", "f · Lời mời", "Chấp nhận lời mời tham gia công ty", "mail"),
    (
        "select-company",
        "f · Chọn công ty",
        "Sau đăng nhập khi có nhiều công ty",
        "building-2",
    ),
    ("error-404", "Lỗi 404", "Trang không tồn tại", "search"),
    ("error-403", "Lỗi 403", "Không có quyền", "lock"),
    ("error-500", "Lỗi 500", "Lỗi hệ thống", "circle-alert"),
]
PAGE_KEYS = {p[0] for p in PAGES}
AUTH_PAGES = {"login", "signup", "setup", "invite", "select-company"}

COMPANY = Obj(
    id=1,
    name="CÔNG TY TNHH CÔNG NGHỆ THÔNG MINH NVIAI",
    short_name="NVIAI",
    tax_code="0000000001",
    address="123 Đường Demo, Phường 1, TP. Hồ Chí Minh",
)
USER = Obj(
    id=1,
    full_name="Nguyễn Thị Minh Anh",
    email="minhanh@nviai.vn",
    initials="MA",
    role="chief_accountant",
    role_label="Kế toán trưởng",
    is_superadmin=False,
)
USER["can"] = lambda perm: True

NAV_SPEC = [
    (
        "overview",
        "",
        [
            (
                "Tổng quan",
                "/ui-kit/dashboard",
                "layout-dashboard",
                "dashboard",
                None,
            )
        ],
    ),
    (
        "money",
        "Tiền",
        [
            ("Phiếu thu, chi", "/ui-kit/list", "wallet", "list", None),
            ("Ngân hàng", "#", "landmark", None, None),
            ("Đối chiếu sao kê", "#", "git-compare", None, "12"),
        ],
    ),
    (
        "sales",
        "Bán hàng",
        [
            (
                "Hóa đơn bán hàng",
                "/ui-kit/form-invoice",
                "receipt-text",
                "form-invoice",
                None,
            ),
            ("Công nợ phải thu", "#", "hand-coins", None, None),
        ],
    ),
    (
        "purchases",
        "Mua hàng",
        [
            ("Chứng từ mua hàng", "#", "shopping-cart", None, None),
            ("Hóa đơn đầu vào XML", "#", "file-check", None, "3"),
            ("Công nợ phải trả", "#", "coins", None, None),
        ],
    ),
    (
        "inventory",
        "Kho",
        [
            ("Nhập, xuất kho", "#", "package", None, None),
            ("Tồn kho", "#", "warehouse", None, None),
        ],
    ),
    (
        "assets",
        "Tài sản",
        [
            ("Tài sản cố định", "#", "building", None, None),
            ("Công cụ dụng cụ", "#", "boxes", None, None),
        ],
    ),
    (
        "payroll",
        "Lương",
        [
            ("Bảng lương", "#", "banknote", None, None),
            ("Nhân viên", "#", "users", None, None),
        ],
    ),
    (
        "tax",
        "Thuế",
        [
            ("Tờ khai GTGT", "#", "percent", None, None),
            ("Quyết toán TNDN", "#", "scroll-text", None, None),
        ],
    ),
    (
        "books",
        "Sổ sách & báo cáo",
        [
            (
                "Phiếu kế toán",
                "/ui-kit/form-entry",
                "notebook-pen",
                "form-entry",
                None,
            ),
            ("Chứng từ", "/ui-kit/detail", "file-text", "detail", None),
            (
                "Bảng cân đối tài khoản",
                "/ui-kit/report",
                "scale",
                "report",
                None,
            ),
            ("Báo cáo tài chính", "#", "chart-column", None, None),
            ("Khóa sổ", "#", "lock", None, None),
        ],
    ),
    (
        "catalog",
        "Danh mục",
        [
            ("Hệ thống tài khoản", "#", "list", None, None),
            ("Khách hàng, nhà cung cấp", "#", "users", None, None),
            ("Hàng hóa, dịch vụ", "#", "tags", None, None),
        ],
    ),
    (
        "system",
        "Hệ thống",
        [
            (
                "Thành phần giao diện",
                "/ui-kit/components",
                "layers",
                "components",
                None,
            ),
            ("Cài đặt", "/ui-kit/settings", "settings", "settings", None),
            ("Nhật ký thao tác", "#", "history", None, None),
        ],
    ),
]

QUICK_ACTIONS = [
    Obj(
        label="Phiếu thu",
        href="/ui-kit/form-entry",
        icon="circle-arrow-down",
        shortcut="Alt+T",
        group="Tiền",
    ),
    Obj(
        label="Phiếu chi",
        href="/ui-kit/form-entry",
        icon="circle-arrow-up",
        shortcut="Alt+C",
        group="Tiền",
    ),
    Obj(
        label="Hóa đơn bán hàng",
        href="/ui-kit/form-invoice",
        icon="receipt-text",
        shortcut="Alt+B",
        group="Bán hàng",
    ),
    Obj(
        label="Chứng từ mua hàng",
        href="/ui-kit/form-invoice",
        icon="shopping-cart",
        shortcut="Alt+M",
        group="Mua hàng",
    ),
    Obj(
        label="Phiếu kế toán",
        href="/ui-kit/form-entry",
        icon="notebook-pen",
        shortcut="Alt+K",
        group="Sổ sách",
    ),
    Obj(
        label="Nhập hóa đơn XML",
        href="#",
        icon="file-up",
        shortcut=None,
        group="Mua hàng",
    ),
]


def _nav(active: str | None):
    out = []
    for key, label, items in NAV_SPEC:
        out.append(
            Obj(
                key=key,
                label=label,
                icon=None,
                items=[
                    Obj(
                        label=lb,
                        href=href,
                        icon=ic,
                        active=(page == active),
                        badge=badge,
                    )
                    for lb, href, ic, page, badge in items
                ],
            )
        )
    return out


def base_context(
    page: str, theme: str = "light", lang: str = "vi", user: bool = True
) -> dict:
    return {
        "user": USER if user else None,
        "company": COMPANY if user else None,
        "companies": [
            {
                "id": 1,
                "name": COMPANY.name,
                "role_label": "Kế toán trưởng",
                "current": True,
                "tax_code": COMPANY.tax_code,
            },
            {
                "id": 2,
                "name": "Công ty CP Thương mại Sao Việt",
                "role_label": "Kế toán viên",
                "current": False,
                "tax_code": "0109876543",
            },
            {
                "id": 3,
                "name": "Hộ kinh doanh Bếp Nhà Lan",
                "role_label": "Chủ doanh nghiệp",
                "current": False,
                "tax_code": None,
            },
        ],
        "nav": _nav(page) if user else [],
        "quick_actions": QUICK_ACTIONS if user else [],
        "product_name": "VFAS",
        "ai_enabled": True,
        "ai_panel_url": "/ui-kit/ai-panel",
        "search_url": "/ui-kit/search",
        "csrf_token": "ui-kit",
        "lang": lang,
        "theme": theme,
        "_": translator,
        "get_flashes": lambda: [],
        "can": lambda perm: True,
        "is_htmx": False,
        "path": f"/ui-kit/{page}",
        "today": date(2026, 10, 14),
        "allow_signup": True,
    }


# ---------------------------------------- lookups (/ui-kit/lookup/*)

LOOKUPS = {
    "accounts": [
        {"value": c, "label": f"{c} – {n}", "hint": h}
        for c, n, h in [
            ("1111", "Tiền Việt Nam", "Tiền mặt"),
            ("1121", "Tiền gửi ngân hàng – VND", "Tiền gửi"),
            ("131", "Phải thu của khách hàng", "Lưỡng tính · theo đối tượng"),
            ("1331", "Thuế GTGT được khấu trừ của HHDV", ""),
            ("1388", "Phải thu khác", ""),
            ("141", "Tạm ứng", "Theo nhân viên"),
            ("152", "Nguyên liệu, vật liệu", ""),
            ("153", "Công cụ, dụng cụ", ""),
            ("156", "Hàng hóa", ""),
            ("2111", "Nhà cửa, vật kiến trúc", ""),
            ("242", "Chi phí trả trước", ""),
            ("331", "Phải trả cho người bán", "Lưỡng tính · theo đối tượng"),
            ("33311", "Thuế GTGT đầu ra", ""),
            ("3334", "Thuế thu nhập doanh nghiệp", ""),
            ("3335", "Thuế thu nhập cá nhân", ""),
            ("334", "Phải trả người lao động", ""),
            ("3383", "Bảo hiểm xã hội", ""),
            ("4111", "Vốn góp của chủ sở hữu", ""),
            ("5111", "Doanh thu bán hàng hóa", ""),
            ("5113", "Doanh thu cung cấp dịch vụ", ""),
            ("515", "Doanh thu hoạt động tài chính", ""),
            ("632", "Giá vốn hàng bán", ""),
            ("6421", "Chi phí bán hàng", ""),
            ("6422", "Chi phí quản lý doanh nghiệp", ""),
            ("711", "Thu nhập khác", ""),
            ("811", "Chi phí khác", ""),
            ("911", "Xác định kết quả kinh doanh", ""),
        ]
    ],
    "partners": [
        {
            "value": i,
            "label": f"{code} – {name}",
            "hint": hint,
            "extra": {"address": addr, "tax_code": tax},
        }
        for i, (code, name, hint, addr, tax) in enumerate(
            [
                (
                    "KH001",
                    "Công ty CP Giải pháp Số Hà Nội",
                    "MST 0108123456",
                    "25 Láng Hạ, Đống Đa, Hà Nội",
                    "0108123456",
                ),
                (
                    "KH002",
                    "Công ty TNHH Thực phẩm Xanh",
                    "MST 0312987654",
                    "88 Lê Lợi, Quận 1, TP. HCM",
                    "0312987654",
                ),
                (
                    "NCC001",
                    "Tổng công ty Điện lực TP. HCM",
                    "MST 0300951119",
                    "35 Tôn Đức Thắng, Quận 1, TP. HCM",
                    "0300951119",
                ),
                (
                    "NCC002",
                    "Công ty TNHH Văn phòng phẩm Hồng Hà",
                    "MST 0101234567",
                    "25 Lý Thường Kiệt, Hà Nội",
                    "0101234567",
                ),
                (
                    "NV001",
                    "Trần Văn Bình",
                    "Nhân viên · Phòng kinh doanh",
                    "",
                    "",
                ),
                ("NV002", "Lê Thị Hoa", "Nhân viên · Kế toán", "", ""),
            ],
            start=1,
        )
    ],
    "items": [
        {
            "value": i,
            "label": f"{code} – {name}",
            "hint": f"{unit} · VAT {vat}%",
            "extra": {
                "name": name,
                "unit": unit,
                "vat_rate": vat,
                "sale_price": price,
                "purchase_price": int(price * 0.7),
            },
        }
        for i, (code, name, unit, vat, price) in enumerate(
            [
                (
                    "DV-TV",
                    "Dịch vụ tư vấn chuyển đổi số",
                    "Gói",
                    "10",
                    25_000_000,
                ),
                (
                    "DV-PM",
                    "Thuê bao phần mềm kế toán (12 tháng)",
                    "Năm",
                    "10",
                    4_800_000,
                ),
                (
                    "HH-LAP",
                    "Máy tính xách tay Dell Latitude 5450",
                    "Chiếc",
                    "10",
                    28_500_000,
                ),
                (
                    "HH-MOU",
                    "Chuột không dây Logitech M331",
                    "Chiếc",
                    "8",
                    390_000,
                ),
                ("DV-DT", "Đào tạo sử dụng phần mềm", "Buổi", "8", 3_500_000),
            ],
            start=1,
        )
    ],
    "bank-accounts": [
        {
            "value": 1,
            "label": "Vietcombank – 0071 0012 34567",
            "hint": "TK 1121",
            "extra": {"account_code": "1121"},
        },
        {
            "value": 2,
            "label": "Techcombank – 1903 4567 8910 11",
            "hint": "TK 1121",
            "extra": {"account_code": "1121"},
        },
    ],
}

SEARCH_RESULTS = {
    "results": [
        {
            "label": "PC2026-0042 · Thanh toán tiền điện tháng 9",
            "hint": "02/10/2026 · 2.200.000",
            "href": "/ui-kit/detail",
            "icon": "file-text",
            "group": "Chứng từ",
        },
        {
            "label": "BH2026-0118 · Công ty CP Giải pháp Số Hà Nội",
            "hint": "30/09/2026 · 27.500.000",
            "href": "/ui-kit/detail",
            "icon": "receipt-text",
            "group": "Chứng từ",
        },
        {
            "label": "KH001 – Công ty CP Giải pháp Số Hà Nội",
            "hint": "Khách hàng · MST 0108123456",
            "href": "#",
            "icon": "users",
            "group": "Đối tượng",
        },
        {
            "label": "6422 – Chi phí quản lý doanh nghiệp",
            "hint": "Tài khoản",
            "href": "#",
            "icon": "list",
            "group": "Tài khoản",
        },
    ]
}


# ---------------------------------------- page data

MONTHS = [
    "T11/25",
    "T12/25",
    "T1",
    "T2",
    "T3",
    "T4",
    "T5",
    "T6",
    "T7",
    "T8",
    "T9",
    "T10",
]
INFLOW = [182, 240, 156, 128, 205, 231, 198, 264, 247, 289, 312, 176]
OUTFLOW = [151, 198, 172, 140, 167, 188, 214, 203, 226, 241, 259, 148]


def _entries():
    rows = [
        (
            "PC2026-0042",
            "2026-10-02",
            "PC",
            "Thanh toán tiền điện tháng 9/2026",
            "Tổng công ty Điện lực TP. HCM",
            2_200_000,
            "posted",
        ),
        (
            "PT2026-0031",
            "2026-10-02",
            "PT",
            "Thu tiền dịch vụ tư vấn theo HĐ 18/2026",
            "Công ty CP Giải pháp Số Hà Nội",
            27_500_000,
            "posted",
        ),
        (
            "BN2026-0087",
            "2026-10-01",
            "BN",
            "Chuyển khoản trả tiền văn phòng phẩm",
            "Công ty TNHH Văn phòng phẩm Hồng Hà",
            4_185_000,
            "posted",
        ),
        (
            "BH2026-0118",
            "2026-09-30",
            "BH",
            "Bán thuê bao phần mềm 12 tháng",
            "Công ty TNHH Thực phẩm Xanh",
            5_280_000,
            "posted",
        ),
        (
            "LG2026-0009",
            "2026-09-30",
            "LG",
            "Hạch toán lương tháng 9/2026",
            None,
            186_420_000,
            "posted",
        ),
        (
            "KH2026-0009",
            "2026-09-30",
            "KH",
            "Khấu hao TSCĐ tháng 9/2026",
            None,
            12_833_333,
            "posted",
        ),
        (
            None,
            "2026-09-29",
            "PC",
            "Tạm ứng công tác phí – Trần Văn Bình",
            "Trần Văn Bình",
            5_000_000,
            "draft",
        ),
        (
            "MH2026-0064",
            "2026-09-28",
            "MH",
            "Mua máy tính xách tay Dell Latitude 5450",
            "Công ty TNHH Văn phòng phẩm Hồng Hà",
            31_350_000,
            "posted",
        ),
        (
            "PK2026-0015",
            "2026-09-27",
            "PK",
            "Phân loại lại chi phí tiếp khách",
            None,
            1_650_000,
            "cancelled",
        ),
        (
            "BC2026-0102",
            "2026-09-26",
            "BC",
            "Khách hàng chuyển khoản thanh toán HĐ 0000231",
            "Công ty CP Giải pháp Số Hà Nội",
            55_000_000,
            "posted",
        ),
        (
            None,
            "2026-09-25",
            "PK",
            "Trích trước chi phí kiểm toán năm 2026",
            None,
            15_000_000,
            "draft",
        ),
        (
            "PT2026-0030",
            "2026-09-24",
            "PT",
            "Rút tiền gửi ngân hàng nhập quỹ",
            None,
            50_000_000,
            "posted",
        ),
    ]
    return [
        Obj(
            id=i,
            number=n,
            date=date.fromisoformat(d),
            doc_type=t,
            description=desc,
            partner=p,
            amount=a,
            status=s,
        )
        for i, (n, d, t, desc, p, a, s) in enumerate(rows, start=1)
    ]


def _trial_balance():
    rows = [
        ("1111", "Tiền Việt Nam", 85_420_000, 0, 412_300_000, 398_750_000),
        (
            "1121",
            "Tiền gửi ngân hàng – VND",
            1_120_000_000,
            0,
            1_865_000_000,
            1_702_660_000,
        ),
        (
            "131",
            "Phải thu của khách hàng",
            248_500_000,
            0,
            652_800_000,
            590_300_000,
        ),
        (
            "1331",
            "Thuế GTGT được khấu trừ của HHDV",
            0,
            0,
            38_215_000,
            38_215_000,
        ),
        ("141", "Tạm ứng", 12_000_000, 0, 15_000_000, 22_000_000),
        ("153", "Công cụ, dụng cụ", 0, 0, 18_400_000, 18_400_000),
        ("2111", "Nhà cửa, vật kiến trúc", 0, 0, 0, 0),
        ("2112", "Máy móc, thiết bị", 462_000_000, 0, 28_500_000, 0),
        ("2141", "Hao mòn TSCĐ hữu hình", 0, 96_250_000, 0, 38_500_000),
        ("242", "Chi phí trả trước", 36_000_000, 0, 18_400_000, 9_200_000),
        (
            "331",
            "Phải trả cho người bán",
            0,
            184_300_000,
            402_150_000,
            421_600_000,
        ),
        ("33311", "Thuế GTGT đầu ra", 0, 41_200_000, 41_200_000, 54_380_000),
        ("3334", "Thuế thu nhập doanh nghiệp", 0, 18_500_000, 18_500_000, 0),
        ("3335", "Thuế thu nhập cá nhân", 0, 6_840_000, 6_840_000, 7_120_000),
        (
            "334",
            "Phải trả người lao động",
            0,
            158_200_000,
            486_900_000,
            492_150_000,
        ),
        ("3383", "Bảo hiểm xã hội", 0, 42_150_000, 126_450_000, 128_100_000),
        ("4111", "Vốn góp của chủ sở hữu", 0, 1_000_000_000, 0, 0),
        (
            "4212",
            "Lợi nhuận sau thuế chưa phân phối năm nay",
            0,
            416_480_000,
            0,
            0,
        ),
        ("5113", "Doanh thu cung cấp dịch vụ", 0, 0, 543_800_000, 543_800_000),
        (
            "6422",
            "Chi phí quản lý doanh nghiệp",
            0,
            0,
            512_340_000,
            512_340_000,
        ),
        ("911", "Xác định kết quả kinh doanh", 0, 0, 543_800_000, 543_800_000),
    ]
    # Balance the fake data: the opening difference goes to 4211 and
    # the period difference to 3388.
    d_open = sum(r[2] - r[3] for r in rows)
    d_per = sum(r[4] - r[5] for r in rows)
    rows.append(
        (
            "3388",
            "Phải trả, phải nộp khác",
            0,
            0,
            -d_per if d_per < 0 else 0,
            d_per if d_per > 0 else 0,
        )
    )
    rows.append(
        (
            "4211",
            "Lợi nhuận sau thuế chưa phân phối năm trước",
            -d_open if d_open < 0 else 0,
            d_open if d_open > 0 else 0,
            0,
            0,
        )
    )
    rows.sort(key=lambda r: r[0])
    out = []
    for code, name, od, oc, pd, pc in rows:
        bal = od - oc + pd - pc
        out.append(
            Obj(
                code=code,
                name=name,
                open_d=od,
                open_c=oc,
                per_d=pd,
                per_c=pc,
                close_d=bal if bal > 0 else 0,
                close_c=-bal if bal < 0 else 0,
                level=1 if len(code) == 3 else 2,
            )
        )
    tot = Obj(
        code="",
        name="Cộng",
        **{
            k: sum(r[k] for r in out)
            for k in (
                "open_d",
                "open_c",
                "per_d",
                "per_c",
                "close_d",
                "close_c",
            )
        },
    )
    return out, tot


def page_context(page: str) -> dict:
    ctx: dict = {"kit_pages": PAGES, "lookup_base": "/ui-kit/lookup"}
    if page == "list":
        ctx.update(
            entries=_entries(),
            page=1,
            pages=16,
            total=184,
            filters=Obj(
                q="",
                doc_type="",
                status="",
                start=date(2026, 9, 1),
                end=date(2026, 10, 14),
            ),
        )
    elif page == "form-entry":
        ctx.update(
            entry=Obj(
                date=date(2026, 10, 14),
                doc_date=date(2026, 10, 14),
                description="Hạch toán chi phí thuê văn phòng tháng 10/2026",
                ref_no="HĐ 0001428",
                attachments_note="01 hóa đơn GTGT",
            ),
            lines=[
                {
                    "account": "6422",
                    "account_label": "6422 – Chi phí quản lý doanh nghiệp",
                    "debit": 18_000_000,
                    "credit": None,
                    "partner_id": "",
                    "partner_label": "",
                    "description": "Thuê văn phòng tháng 10/2026",
                },
                {
                    "account": "1331",
                    "account_label": "1331 – Thuế GTGT được khấu trừ của HHDV",
                    "debit": 1_800_000,
                    "credit": None,
                    "partner_id": "",
                    "partner_label": "",
                    "description": "Thuế GTGT thuê văn phòng",
                },
                {
                    "account": "331",
                    "account_label": "331 – Phải trả cho người bán",
                    "debit": None,
                    "credit": 19_000_000,
                    "partner_id": 4,
                    "partner_label": (
                        "NCC002 – Công ty TNHH Văn phòng phẩm Hồng Hà"
                    ),
                    "description": "Phải trả tiền thuê văn phòng",
                },
            ],
            errors={"lines": "Tổng Nợ và tổng Có chưa bằng nhau."},
        )
    elif page == "form-invoice":
        ctx.update(
            invoice=Obj(
                date=date(2026, 10, 14),
                series="1C26TNV",
                number="0000245",
                payment="credit",
                due_date=date(2026, 11, 13),
            ),
            lines=[
                {
                    "item_id": 1,
                    "item_label": "DV-TV – Dịch vụ tư vấn chuyển đổi số",
                    "description": (
                        "Dịch vụ tư vấn chuyển đổi số – giai đoạn 2"
                    ),
                    "unit": "Gói",
                    "quantity": 1,
                    "unit_price": 25_000_000,
                    "amount": 25_000_000,
                    "vat_rate": "10",
                    "vat_amount": 2_500_000,
                },
                {
                    "item_id": 5,
                    "item_label": "DV-DT – Đào tạo sử dụng phần mềm",
                    "description": "Đào tạo sử dụng phần mềm",
                    "unit": "Buổi",
                    "quantity": 2,
                    "unit_price": 3_500_000,
                    "amount": 7_000_000,
                    "vat_rate": "8",
                    "vat_amount": 560_000,
                },
            ],
        )
    elif page == "detail":
        ctx.update(
            entry=Obj(
                number="PC2026-0042",
                doc_type="PC",
                status="posted",
                date=date(2026, 10, 2),
                doc_date=date(2026, 10, 2),
                description="Thanh toán tiền điện tháng 9/2026",
                contact_name="Nguyễn Văn Tâm",
                contact_address="35 Tôn Đức Thắng, Quận 1, TP. HCM",
                partner="NCC001 – Tổng công ty Điện lực TP. HCM",
                ref_no="HĐ 00012873",
                attachments_note="01 hóa đơn GTGT",
                amount=2_200_000,
                created_by="Lê Thị Hoa",
                posted_by="Nguyễn Thị Minh Anh",
                posted_at=datetime(2026, 10, 2, 15, 42),
            ),
            lines=[
                Obj(
                    account="6422",
                    account_name="Chi phí quản lý doanh nghiệp",
                    debit=2_000_000,
                    credit=0,
                    partner=None,
                    description="Tiền điện tháng 9/2026",
                ),
                Obj(
                    account="1331",
                    account_name="Thuế GTGT được khấu trừ",
                    debit=200_000,
                    credit=0,
                    partner=None,
                    description="Thuế GTGT tiền điện",
                ),
                Obj(
                    account="1111",
                    account_name="Tiền Việt Nam",
                    debit=0,
                    credit=2_200_000,
                    partner=None,
                    description="Chi tiền mặt",
                ),
            ],
            vat=Obj(
                series="1C26TEV",
                number="00012873",
                date=date(2026, 9, 30),
                seller="Tổng công ty Điện lực TP. HCM",
                tax_code="0300951119",
                amount=2_000_000,
                rate="10%",
                vat=200_000,
            ),
            attachments=[
                Obj(
                    name="HD_00012873_EVNHCMC.pdf",
                    size="182 KB",
                    href="#",
                    kind="pdf",
                ),
                Obj(
                    name="HD_00012873_EVNHCMC.xml",
                    size="14 KB",
                    href="#",
                    kind="xml",
                ),
            ],
            history=[
                Obj(
                    at="02/10/2026 15:42",
                    actor="Nguyễn Thị Minh Anh",
                    action="đã ghi sổ chứng từ",
                    detail="Số PC2026-0042 được cấp khi ghi sổ.",
                    icon="check",
                    tone="green",
                ),
                Obj(
                    at="02/10/2026 11:05",
                    actor="Lê Thị Hoa",
                    action="đã sửa nháp",
                    detail="Số tiền: 2.000.000 → 2.200.000",
                    icon="pencil",
                ),
                Obj(
                    at="02/10/2026 10:58",
                    actor="Trợ lý AI",
                    action="đã lập nháp theo yêu cầu của Lê Thị Hoa",
                    detail="Từ hóa đơn XML 00012873",
                    icon="sparkles",
                ),
            ],
        )
    elif page == "report":
        rows, tot = _trial_balance()
        ctx.update(
            rows=rows,
            total=tot,
            period=Obj(
                mode="quarter",
                start=date(2026, 7, 1),
                end=date(2026, 9, 30),
                quarter=3,
                year=2026,
            ),
        )
    elif page == "dashboard":
        ctx.update(
            months=MONTHS,
            inflow=[v * 1_000_000 for v in INFLOW],
            outflow=[v * 1_000_000 for v in OUTFLOW],
            balance=[
                v * 1_000_000
                for v in [
                    812,
                    854,
                    838,
                    826,
                    864,
                    907,
                    891,
                    952,
                    973,
                    1021,
                    1074,
                    1102,
                ]
            ],
            expenses=[
                Obj(
                    label="642 – Chi phí quản lý doanh nghiệp",
                    value=512_340_000,
                ),
                Obj(label="632 – Giá vốn hàng bán", value=286_150_000),
                Obj(label="6421 – Chi phí bán hàng", value=148_900_000),
                Obj(label="635 – Chi phí tài chính", value=22_400_000),
                Obj(label="811 – Chi phí khác", value=6_200_000),
            ],
            revenue_mix=[
                Obj(label="Dịch vụ tư vấn", value=1_240_000_000, slot=1),
                Obj(label="Thuê bao phần mềm", value=684_000_000, slot=2),
                Obj(label="Bán thiết bị", value=318_000_000, slot=3),
            ],
            tasks=[
                Obj(
                    icon="file-text",
                    label="Chứng từ nháp chờ ghi sổ",
                    count=5,
                    href="/ui-kit/list",
                    tone="amber",
                ),
                Obj(
                    icon="git-compare",
                    label="Dòng sao kê chưa đối chiếu",
                    count=12,
                    href="#",
                    tone="amber",
                ),
                Obj(
                    icon="file-check",
                    label="Hóa đơn đầu vào chưa hạch toán",
                    count=3,
                    href="#",
                    tone="blue",
                ),
                Obj(
                    icon="lock",
                    label="Tháng 9/2026 chưa khóa sổ",
                    count=None,
                    href="#",
                    tone="gray",
                ),
            ],
            deadlines=[
                Obj(
                    label="Tờ khai thuế GTGT tháng 9/2026 (01/GTGT)",
                    due=date(2026, 10, 20),
                    days=6,
                    status="pending",
                ),
                Obj(
                    label="Nộp thuế TNCN tháng 9/2026",
                    due=date(2026, 10, 20),
                    days=6,
                    status="pending",
                ),
                Obj(
                    label="Tạm nộp thuế TNDN quý 3/2026",
                    due=date(2026, 10, 30),
                    days=16,
                    status="pending",
                ),
                Obj(
                    label="Tờ khai thuế GTGT tháng 8/2026",
                    due=date(2026, 9, 20),
                    days=-24,
                    status="submitted",
                ),
            ],
            recent=_entries()[:6],
        )
    elif page == "settings":
        ctx.update(
            company=COMPANY,
            settings=Obj(
                vat_method="khau_tru",
                vat_period="month",
                region="I",
                number_format="{prefix}{year}-{seq:04d}",
                legal_rep="Phạm Quốc Hùng",
                chief_accountant="Nguyễn Thị Minh Anh",
                preparer="Lê Thị Hoa",
                cashier="Đỗ Thu Trang",
                start_date=date(2023, 1, 12),
            ),
        )
    return ctx


AI_CHAT = {
    "conversation_id": 7,
    "model_label": "claude-opus-5-5 · Anthropic (tương thích OpenAI)",
    "conversations": [
        {
            "id": 7,
            "title": "Tồn quỹ và công nợ tháng 10",
            "updated_at": "14/10 09:12",
            "active": True,
        },
        {
            "id": 6,
            "title": "Lập phiếu chi tiền điện",
            "updated_at": "02/10 10:58",
            "active": False,
        },
        {
            "id": 5,
            "title": "So sánh chi phí quý 2 và quý 3",
            "updated_at": "28/09 16:20",
            "active": False,
        },
    ],
    "messages": [
        {
            "role": "user",
            "text": (
                "Tồn quỹ tiền mặt và tiền gửi hôm nay là bao nhiêu? "
                "Khách nào đang nợ quá hạn?"
            ),
        },
        {
            "role": "assistant",
            "tools": [
                {
                    "name": "get_account_balance",
                    "label": "Xem số dư 111, 112",
                    "status": "done",
                    "summary": "2 tài khoản",
                },
                {
                    "name": "receivables_aging",
                    "label": "Tuổi nợ phải thu",
                    "status": "done",
                    "summary": "3 khách quá hạn",
                    "link": "#",
                },
            ],
            "html": (
                "<p>Đến hết ngày <strong>14/10/2026</strong>:</p>"
                "<ul><li>Tiền mặt (1111): <strong>99.000.000 đ</strong></li>"
                "<li>Tiền gửi ngân hàng (1121): "
                "<strong>1.282.340.000 đ</strong></li></ul>"
                "<p>Khách hàng nợ quá hạn trên 30 ngày:</p>"
                "<table><thead><tr><th>Khách hàng</th><th>Quá hạn</th>"
                "<th>Số tiền</th></tr></thead><tbody>"
                "<tr><td>Công ty TNHH Thực phẩm Xanh</td><td>45 ngày</td>"
                "<td>38.500.000</td></tr>"
                "<tr><td>Công ty CP Giải pháp Số Hà Nội</td><td>32 ngày</td>"
                "<td>27.500.000</td></tr></tbody></table>"
            ),
        },
        {
            "role": "user",
            "text": (
                "Lập phiếu thu 27,5tr của Giải pháp Số Hà Nội theo HĐ 18/2026."
            ),
        },
        {
            "role": "assistant",
            "tools": [
                {
                    "name": "create_draft_cash_voucher",
                    "label": "Lập nháp phiếu thu",
                    "status": "done",
                    "summary": "27.500.000 đ",
                    "link": "/ui-kit/detail",
                },
            ],
            "html": (
                "<p>Đã lập <strong>phiếu thu nháp</strong> 27.500.000 đ: "
                "Nợ 1111 / Có 131 – Công ty CP Giải pháp Số Hà Nội, "
                "diễn giải “Thu tiền dịch vụ tư vấn theo HĐ 18/2026”.</p>"
                "<p>Bạn kiểm tra rồi bấm <strong>Ghi sổ</strong> "
                "để hoàn tất.</p>"
            ),
        },
    ],
    "suggestions": [],
    "post_url": "/ui-kit/ai-chat",
}


def render(
    page: str,
    theme: str = "light",
    lang: str = "vi",
    env: Environment | None = None,
) -> str:
    env = env or build_env()
    if page in ("", "index"):
        ctx = base_context("index", theme, lang) | {"kit_pages": PAGES}
        return env.get_template("ui_kit/index.html").render(ctx)
    if page.startswith("error-"):
        code = int(page.split("-")[1])
        titles = {
            404: (
                "Không tìm thấy trang",
                "Trang bạn tìm không tồn tại hoặc đã bị chuyển đi.",
            ),
            403: (
                "Bạn không có quyền truy cập",
                (
                    "Vai trò Thủ quỹ không được xem chức năng này. "
                    "Liên hệ Kế toán trưởng để được cấp quyền."
                ),
            ),
            500: (
                "Hệ thống gặp sự cố",
                (
                    "Đã ghi nhận lỗi. Dữ liệu của bạn không bị ảnh hưởng – "
                    "vui lòng thử lại sau ít phút."
                ),
            ),
        }
        title, msg = titles.get(code, titles[500])
        ctx = base_context(page, theme, lang, user=code != 500) | {
            "status": code,
            "title": title,
            "message": msg,
        }
        return env.get_template("errors/error.html").render(ctx)
    if page in AUTH_PAGES:
        ctx = base_context(page, theme, lang, user=False) | {
            "errors": {},
            "form": {},
        }
        if page == "login":
            ctx.update(
                error="Email hoặc mật khẩu không đúng.",
                form={"email": "minhanh@nviai.vn"},
                next="/",
            )
        elif page == "signup":
            ctx.update(
                errors={"email": "Email này đã được dùng."},
                form={"full_name": "Phạm Quốc Hùng", "email": "hung@nviai.vn"},
            )
        elif page == "invite":
            ctx.update(
                invitation=Obj(
                    email="hoa@nviai.vn",
                    company_name=COMPANY.name,
                    role_label="Kế toán viên",
                ),
                has_account=False,
            )
        elif page == "select-company":
            ctx["companies"] = base_context(page)["companies"]
        tpl = {"select-company": "auth/select_company.html"}.get(
            page, f"auth/{page}.html"
        )
        return env.get_template(tpl).render(ctx)
    if page == "ai-panel":
        ctx = base_context("ai", theme, lang) | {"chat": AI_CHAT}
        return env.get_template("partials/ai_chat.html").render(ctx)
    if page not in PAGE_KEYS:
        raise KeyError(page)
    ctx = base_context(page, theme, lang) | page_context(page)
    return env.get_template(f"ui_kit/{page.replace('-', '_')}.html").render(
        ctx
    )
