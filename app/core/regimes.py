"""Accounting regimes: chart of accounts, form codes and the
"Ban hành theo / Kèm theo …" line printed on forms.

A company picks one via `companies.regime`:

- tt133 – Circular 133/2016/TT-BTC (SMEs);
- tt99  – Circular 99/2025/TT-BTC (all enterprises from 2026; SMEs may
  choose it). Different chart and statements (B01-DN with short/long
  term split, B02-DN, B03-DN), ledgers coded "-DN";
- tt58  – Circular 58/2026/TT-BTC (micro enterprises, from fiscal years
  starting on or after 01/7/2026). TT58 prescribes no chart of accounts:
  the books are kept double-entry on the TT133 chart (an SME regime,
  which TT58 Art. 4.3 allows) and the statements B01-DNSN/B02-DNSN are
  produced from them.

Modules never hard-code regime-specific account codes: they ask
`account(company, role)` for defaults (e.g. the expense account of a
salary or of depreciation) and `form(company, code)` for the printed
form code. Codes are written with their TT133 name as the key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.core.charts import tt99, tt133

TT58_ISSUED = (
    "(Kèm theo Thông tư số 58/2026/TT-BTC ngày 25 tháng 5 năm 2026 "
    "của Bộ trưởng Bộ Tài chính)"
)

# Default accounts by role (TT133 value first).
TT133_ACCOUNTS = {
    "expense_general": "6422",  # other administrative expenses
    "expense_services": "6422",  # purchased services
    "expense_selling": "6421",
    "expense_staff": "6422",  # salaries of office staff
    "expense_depreciation": "6422",
    "expense_tools": "6422",  # tools and prepaid expenses allocated
    "unemployment_insurance": "3385",
    "deposits_paid": "1386",
    "deposits_received": "3386",
    "fixed_asset": "2111",
    "cit_expense": "821",
}
TT99_ACCOUNTS = TT133_ACCOUNTS | {
    "expense_general": "6428",
    "expense_services": "6427",
    "expense_selling": "6418",
    "expense_staff": "6421",
    "expense_depreciation": "6424",
    "expense_tools": "6423",
    "unemployment_insurance": "3386",
    "deposits_paid": "244",
    "deposits_received": "344",
    "fixed_asset": "211",
    "cit_expense": "82111",
}
# Expense accounts a module may offer as a default (payroll, assets...).
TT133_EXPENSE_PREFIXES = ("154", "241", "632", "642", "811")
TT99_EXPENSE_PREFIXES = (
    "154", "241", "621", "622", "623", "627", "632", "641", "642", "811",
)  # fmt: skip

# TT133 form code -> TT99 form code. Ledgers follow TT99 Appendix III
# (TT200 numbering); statements Appendix IV.
TT99_FORMS = {
    "B01a-DNN": "B01-DN",
    "B02-DNN": "B02-DN",
    "B03-DNN": "B03-DN",
    "B09-DNN": "B09-DN",
    "F01-DNN": "S06-DN",
    "S03a-DNN": "S03a-DN",
    "S03b-DNN": "S03b-DN",
    "S04a-DNN": "S07-DN",
    "S04b-DNN": "S07a-DN",
    "S05-DNN": "S08-DN",
    "S06-DNN": "S10-DN",
    "S07-DNN": "S11-DN",
    "S08-DNN": "S12-DN",
    "S09-DNN": "S21-DN",
    "S11-DNN": "S23-DN",
}
# TT58 prescribes only vouchers (01-TT, 02-TT, 01-VT, 02-VT), its own
# ledgers and the statements B01-DNSN/B02-DNSN: the TT133 books are kept
# as internal books without a form code.
TT58_FORMS = {
    "B01a-DNN": "B01-DNSN",
    "B02-DNN": "B02-DNSN",
    "B03-DNN": "",
    "B09-DNN": "",
    "F01-DNN": "",
    "S03a-DNN": "",
    "S03b-DNN": "",
    "S04a-DNN": "",
    "S04b-DNN": "",
    "S05-DNN": "",
    "S06-DNN": "",
    "S07-DNN": "",
    "S08-DNN": "",
    "S09-DNN": "",
    "S11-DNN": "",
}


@dataclass(frozen=True)
class Regime:
    code: str
    name: str
    short: str
    issued: str
    chart: Callable[[], list]
    accounts: dict = field(default_factory=dict)
    expense_prefixes: tuple[str, ...] = TT133_EXPENSE_PREFIXES
    forms: dict = field(default_factory=dict)
    # Statement set built by app.features.gl: tt133 | tt99 | tt58
    statements: str = "tt133"
    description: str = ""
    chart_note: str = ""
    available: bool = True

    def form(self, code: str) -> str:
        return self.forms.get(code, code)

    def account(self, role: str) -> str:
        return self.accounts[role]


REGIMES: dict[str, Regime] = {
    "tt133": Regime(
        "tt133",
        "Thông tư 133/2016/TT-BTC (doanh nghiệp nhỏ và vừa)",
        "TT133",
        tt133.ISSUED,
        tt133.chart_accounts,
        TT133_ACCOUNTS,
        TT133_EXPENSE_PREFIXES,
        {},
        "tt133",
        "Chế độ kế toán doanh nghiệp nhỏ và vừa, Báo cáo tình hình tài "
        "chính B01a-DNN.",
        "Cấp 1, cấp 2 theo Phụ lục 1 TT133; thêm được tài khoản chi tiết",
    ),
    "tt99": Regime(
        "tt99",
        "Thông tư 99/2025/TT-BTC (chế độ kế toán doanh nghiệp)",
        "TT99",
        tt99.ISSUED,
        tt99.chart_accounts,
        TT99_ACCOUNTS,
        TT99_EXPENSE_PREFIXES,
        TT99_FORMS,
        "tt99",
        "Áp dụng cho mọi doanh nghiệp từ năm tài chính 2026; doanh "
        "nghiệp nhỏ và vừa được chọn áp dụng. Hệ thống tài khoản đầy đủ, "
        "BCTC B01-DN tách ngắn hạn/dài hạn.",
        "Theo Phụ lục II TT99; thêm được tài khoản chi tiết",
    ),
    "tt58": Regime(
        "tt58",
        "Thông tư 58/2026/TT-BTC (doanh nghiệp siêu nhỏ)",
        "TT58",
        TT58_ISSUED,
        tt133.chart_accounts,
        TT133_ACCOUNTS,
        TT133_EXPENSE_PREFIXES,
        TT58_FORMS,
        "tt58",
        "Doanh nghiệp siêu nhỏ, từ năm tài chính bắt đầu từ 01/7/2026. "
        "Sổ kép nội bộ theo hệ thống tài khoản TT133; BCTC B01-DNSN, "
        "B02-DNSN.",
        "TT58 không quy định hệ thống tài khoản: sổ kép nội bộ dùng hệ "
        "thống tài khoản TT133",
    ),
}


def get_regime(code: str | None) -> Regime:
    return REGIMES.get((code or "tt133").lower(), REGIMES["tt133"])


def regime_of(company) -> Regime:
    return get_regime(getattr(company, "regime", None) if company else None)


def form_for(company, code: str | None) -> str:
    """Printed form code of `code` (TT133 name) under the company's
    regime; "" when the regime has no such form.
    """
    if not code:
        return ""
    return regime_of(company).form(code)


def issued_for(company, code: str | None = None) -> str:
    """The "(Ban hành theo / Kèm theo …)" line, "" without a form."""
    if code is not None and not form_for(company, code):
        return ""
    return regime_of(company).issued


def account(company, role: str) -> str:
    return regime_of(company).account(role)
