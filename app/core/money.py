"""VND money: formatting, parsing and Vietnamese amount-in-words.

Money is always an integer (in dong). Every function here rejects
floats.
"""

from __future__ import annotations

import re

DIGITS = [
    "không",
    "một",
    "hai",
    "ba",
    "bốn",
    "năm",
    "sáu",
    "bảy",
    "tám",
    "chín",
]
BILLION = 1_000_000_000
# Money limit (10^15 dong): rejects huge numbers that would overflow
# BIGINT or waste resources.
MAX_MONEY = 10**15


class MoneyError(ValueError):
    pass


def format_vnd(amount: int | None, blank_zero: bool = False) -> str:
    """Format 10000000 as '10.000.000'.

    Negative numbers get a leading '-'.
    """
    if amount is None:
        return ""
    if isinstance(amount, float):
        raise MoneyError("Không dùng float cho tiền")
    amount = int(amount)
    if amount == 0 and blank_zero:
        return ""
    sign = "-" if amount < 0 else ""
    return sign + f"{abs(amount):,}".replace(",", ".")


_SUFFIX = re.compile(
    r"^(?P<num>[\d.,\s]+?)\s*(?P<unit>k|nghìn|ngàn|tr|triệu|m|tỷ|ty)?$",
    re.IGNORECASE,
)
_UNIT_MULT = {
    "k": 1_000,
    "nghìn": 1_000,
    "ngàn": 1_000,
    "tr": 1_000_000,
    "triệu": 1_000_000,
    "m": 1_000_000,
    "tỷ": BILLION,
    "ty": BILLION,
}


def parse_vnd(text: str | int | None) -> int:
    """Parse an amount typed by the user.

    Accepts '10.000.000', '10 000 000', '10000000', '10tr', '1,5tr',
    '500k'. Dots/spaces are thousands separators; a comma is a decimal
    separator only when a unit is present (1,5tr).
    """
    if text is None:
        raise MoneyError("Chưa nhập số tiền")
    if isinstance(text, bool) or isinstance(text, float):
        raise MoneyError("Không dùng float cho tiền")
    if isinstance(text, int):
        if abs(text) > MAX_MONEY:
            raise MoneyError("Số tiền quá lớn")
        return text
    s = str(text).strip().replace("đ", "").replace("₫", "").strip()
    if not s:
        raise MoneyError("Chưa nhập số tiền")
    if len(s) > 40:
        raise MoneyError("Số tiền quá lớn")
    return _bounded(_parse_vnd_str(s, text))


def _bounded(value: int) -> int:
    if abs(value) > MAX_MONEY:
        raise MoneyError("Số tiền quá lớn")
    return value


def _parse_vnd_str(s: str, text) -> int:
    m = _SUFFIX.match(s)
    if not m:
        raise MoneyError(f"Số tiền không hợp lệ: {text}")
    num, unit = (
        m.group("num").replace(" ", ""),
        (m.group("unit") or "").lower(),
    )
    if unit:
        # '1,5tr' or '1.5tr' => decimal part. Computed with integers to
        # avoid floats.
        num = num.replace(",", ".")
        if num.count(".") > 1:
            raise MoneyError(f"Số tiền không hợp lệ: {text}")
        whole, _, frac = num.partition(".")
        mult = _UNIT_MULT[unit]
        if not whole.isdigit() and whole != "":
            raise MoneyError(f"Số tiền không hợp lệ: {text}")
        value = int(whole or "0") * mult
        if frac:
            if not frac.isdigit():
                raise MoneyError(f"Số tiền không hợp lệ: {text}")
            scale = 10 ** len(frac)
            if (int(frac) * mult) % scale:
                raise MoneyError("Số tiền phải là số nguyên đồng")
            value += int(frac) * mult // scale
        return value
    if "," in num:
        # 10,000,000 style (English thousands separator) – accepted only
        # with proper groups of 3 digits.
        if not re.fullmatch(r"\d{1,3}(,\d{3})+", num):
            raise MoneyError("Số tiền phải là số nguyên đồng")
        num = num.replace(",", "")
    if "." in num and not re.fullmatch(r"\d{1,3}(\.\d{3})+", num):
        raise MoneyError("Số tiền phải là số nguyên đồng")
    num = num.replace(".", "")
    if not num.isdigit():
        raise MoneyError(f"Số tiền không hợp lệ: {text}")
    return int(num)


def _read_triple(n: int, full: bool, zero_tens: str) -> str:
    """Read one group of 3 digits (0..999) in Vietnamese words.

    full=True: the group is not the leading one, so 'không trăm' and
    'linh' must be spelled out.
    """
    hundreds, tens, units = n // 100, (n // 10) % 10, n % 10
    words: list[str] = []
    if hundreds or full:
        words += [DIGITS[hundreds], "trăm"]
    if tens == 0:
        if units:
            if hundreds or full:
                words.append(zero_tens)
            words.append(DIGITS[units])
    elif tens == 1:
        words.append("mười")
        if units == 5:
            words.append("lăm")
        elif units:
            words.append(DIGITS[units])
    else:
        words += [DIGITS[tens], "mươi"]
        if units == 1:
            words.append("mốt")
        elif units == 5:
            words.append("lăm")
        elif units:
            words.append(DIGITS[units])
    return " ".join(words)


def _read_below_billion(n: int, leading: bool, zero_tens: str) -> list[str]:
    """Read a number 0..999.999.999 in Vietnamese words.

    leading=False means it follows a larger block.
    """
    # Millions, thousands, units
    groups = [
        (n // 1_000_000) % 1000,
        (n // 1000) % 1000,
        n % 1000,
    ]
    units = ["triệu", "nghìn", ""]
    words: list[str] = []
    started = not leading
    for value, unit in zip(groups, units):
        if value == 0:
            continue
        words.append(_read_triple(value, full=started, zero_tens=zero_tens))
        if unit:
            words.append(unit)
        started = True
    return words


def _read_number(n: int, leading: bool, zero_tens: str) -> list[str]:
    if n >= BILLION:
        high, low = divmod(n, BILLION)
        words = _read_number(high, leading, zero_tens) + ["tỷ"]
        if low:
            words += _read_below_billion(
                low, leading=False, zero_tens=zero_tens
            )
        return words
    return _read_below_billion(n, leading, zero_tens)


def number_to_words(n: int, zero_tens: str = "linh") -> str:
    """Spell an integer in lowercase Vietnamese words.

    Example: 1005 -> 'một nghìn không trăm linh năm'.
    """
    if isinstance(n, float):
        raise MoneyError("Không dùng float cho tiền")
    n = int(n)
    if n == 0:
        return "không"
    if n < 0:
        return "âm " + number_to_words(-n, zero_tens)
    return " ".join(_read_number(n, leading=True, zero_tens=zero_tens))


def amount_in_words(amount: int, zero_tens: str = "linh") -> str:
    """Amount in words for cash receipts/payment vouchers.

    10.000.000 -> 'Mười triệu đồng chẵn';
    1.005.000 -> 'Một triệu không trăm linh năm nghìn đồng'.
    'chẵn' is added only when the amount is an exact multiple of
    1 million (see docs/DECISIONS.md).
    """
    text = number_to_words(amount, zero_tens) + " đồng"
    if amount > 0 and amount % 1_000_000 == 0:
        text += " chẵn"
    return text[0].upper() + text[1:]
