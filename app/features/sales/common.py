"""Shared helpers for purchase and sales documents.

Reads the goods grid, rounds amounts and groups VAT by rate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dates import parse_date
from app.core.money import MAX_MONEY, MoneyError, parse_vnd
from app.features.accounts.models import Account
from app.features.items.models import VAT_RATES, Item

# Percentage used to compute the VAT amount for each VAT rate code.
VAT_PCT = {"0": 0, "5": 5, "8": 8, "10": 10, "KCT": 0, "KKKNT": 0, "KHAC": 0}
VAT_LABELS = {
    "0": "0%",
    "5": "5%",
    "8": "8%",
    "10": "10%",
    "KCT": "KCT",
    "KKKNT": "KKKNT",
    "KHAC": "Khác",
}
VAT_ORDER = ["10", "8", "5", "0", "KCT", "KKKNT", "KHAC"]
GRID_RATES = [
    {"value": "10", "label": "10%", "rate": 10},
    {"value": "8", "label": "8%", "rate": 8},
    {"value": "5", "label": "5%", "rate": 5},
    {"value": "0", "label": "0%", "rate": 0},
    {"value": "KCT", "label": "KCT", "rate": 0},
    {"value": "KKKNT", "label": "KKKNT", "rate": 0},
]
MAX_LINES = 500


def round_vnd(value: Decimal | int) -> int:
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def vat_of(base: int, rate: str) -> int:
    return round_vnd(Decimal(base) * VAT_PCT.get(rate, 0) / 100)


MAX_DECIMAL = Decimal(10) ** 12  # max quantity / unit price
_MAX_PLACES = Decimal(1).scaleb(-10)


def parse_decimal(
    value, *, limit: Decimal | int = MAX_DECIMAL
) -> Decimal | None:
    """Parse a quantity or unit price; empty input returns None.

    Accepts '1.5' (JS), '1,5' or '1.234,5' (Vietnamese style).
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return _check_decimal(value, value, limit)
    if isinstance(value, int):
        return _check_decimal(Decimal(value), value, limit)
    s = str(value).strip().replace(" ", "")
    if not s:
        return None
    if len(s) > 40:
        raise ValueError("Số quá dài")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3}){2,}", s):
        s = s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"Số không hợp lệ: {value}") from e
    return _check_decimal(d, value, limit)


def _check_decimal(
    d: Decimal, value, limit: Decimal | int = MAX_DECIMAL
) -> Decimal:
    """Reject NaN, infinity and huge numbers (1e999, 30 digits).

    This prevents Numeric/BIGINT overflow. Values with more than 10
    decimal places are rounded.
    """
    if not d.is_finite() or abs(d) > limit:
        raise ValueError(f"Số không hợp lệ hoặc quá lớn: {str(value)[:30]}")
    if d.as_tuple().exponent < -10:
        d = d.quantize(_MAX_PLACES)
    return d


def parse_vn_decimal(value) -> Decimal | None:
    """Parse a number typed Vietnamese style.

    '1.234.567' / '500.000' use a dot as thousands separator and '1,5'
    uses a comma as decimal separator.
    """
    if value is None or isinstance(value, (Decimal, int)):
        return parse_decimal(value)
    s = str(value).strip().replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})+(,\d+)?", s):
        s = s.replace(".", "")
    return parse_decimal(s)


def parse_money(value, *, allow_empty: bool = True) -> int:
    if value is None or str(value).strip() == "":
        if allow_empty:
            return 0
        raise MoneyError("Chưa nhập số tiền")
    s = str(value).strip()
    if re.fullmatch(r"-?\d+", s):
        return parse_vnd(int(s)) if len(s) <= 20 else parse_vnd(s)
    return parse_vnd(s)


def read_grid(form: dict, prefix: str = "lines") -> list[dict]:
    """Read grid cells `{prefix}-{i}-{field}` into a list of dicts.

    The cells come from the invoiceGrid component; empty rows are
    skipped.
    """
    try:
        count = min(int(form.get(f"{prefix}-count") or 0), MAX_LINES)
    except ValueError:
        count = 0
    pat = re.compile(rf"^{re.escape(prefix)}-(\d+)-(\w+)$")
    rows: dict[int, dict] = {}
    for k, v in form.items():
        m = pat.match(k)
        if m and int(m.group(1)) < max(count, MAX_LINES):
            rows.setdefault(int(m.group(1)), {})[m.group(2)] = (
                v if isinstance(v, str) else str(v)
            )
    out = []
    for i in sorted(rows):
        r = rows[i]
        if not any(
            str(r.get(f) or "").strip()
            for f in (
                "item_id",
                "description",
                "quantity",
                "unit_price",
                "amount",
            )
        ):
            continue
        out.append(r)
    return out


@dataclass
class TradeLine:
    """One goods/service line on a purchase or sales document."""

    description: str
    amount: int  # line amount before discount
    vat_rate: str = "10"
    vat_amount: int = 0
    item_id: int | None = None
    unit: str = ""
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    discount_amount: int = 0
    # Sales: revenue account; purchases: debit account (156/152/642...).
    account: str = ""
    deductible: bool = True
    warehouse_id: int | None = None
    item_label: str = ""
    account_label: str = ""

    @property
    def net(self) -> int:
        return self.amount - self.discount_amount

    def grid_dict(self) -> dict:
        """Return line data for the Alpine grid when re-rendering."""
        return {
            "item_id": self.item_id or "",
            "item_label": self.item_label,
            "description": self.description,
            "unit": self.unit,
            "quantity": float(self.quantity)
            if self.quantity is not None
            else None,
            "unit_price": float(self.unit_price)
            if self.unit_price is not None
            else None,
            "amount": self.amount,
            "discount": self.discount_amount or None,
            "vat_rate": self.vat_rate,
            "vat_amount": self.vat_amount,
            "account": self.account,
            "account_label": self.account_label or self.account,
            "deductible": self.deductible,
        }


def postable_accounts(db: Session, company_id: int) -> dict[str, Account]:
    return {
        a.code: a
        for a in db.scalars(
            select(Account).where(
                Account.company_id == company_id, Account.is_postable.is_(True)
            )
        )
    }


def _company_warehouse_ids(
    db: Session, company_id: int, rows: list[dict]
) -> set[int]:
    """Return the ids of the company's warehouses used in the grid.

    Warehouses belonging to another company are treated as invalid.
    """
    wanted = {
        int(r["warehouse_id"])
        for r in rows
        if str(r.get("warehouse_id") or "").isdigit()
    }
    if not wanted:
        return set()
    try:
        from app.features.inventory.models import Warehouse
    except ImportError:
        return set()
    return set(
        db.scalars(
            select(Warehouse.id).where(
                Warehouse.company_id == company_id, Warehouse.id.in_(wanted)
            )
        )
    )


def build_lines(
    db: Session,
    company_id: int,
    rows: list[dict],
    *,
    default_account,
    account_prefixes=None,
    use_discount: bool = True,
) -> tuple[list[TradeLine], list[str]]:
    """Validate grid data and build TradeLine objects.

    default_account(item | None) -> str returns the default account for
    a line when the user has not chosen one.
    """
    errors: list[str] = []
    accounts = postable_accounts(db, company_id)
    item_ids = set()
    for r in rows:
        try:
            if str(r.get("item_id") or "").strip():
                item_ids.add(int(r["item_id"]))
        except ValueError:
            pass
    items = (
        {
            it.id: it
            for it in db.scalars(
                select(Item).where(
                    Item.company_id == company_id, Item.id.in_(item_ids)
                )
            )
        }
        if item_ids
        else {}
    )
    warehouse_ids = _company_warehouse_ids(db, company_id, rows)
    lines: list[TradeLine] = []
    for n, r in enumerate(rows, start=1):
        item = None
        raw_item = str(r.get("item_id") or "").strip()
        if raw_item:
            try:
                item = items.get(int(raw_item))
            except ValueError:
                item = None
            if item is None:
                errors.append(f"Dòng {n}: hàng hóa không hợp lệ")
        desc = (r.get("description") or "").strip() or (
            item.name if item else ""
        )
        if not desc:
            errors.append(
                f"Dòng {n}: nhập tên hàng hóa, dịch vụ hoặc diễn giải"
            )
        try:
            qty = parse_decimal(r.get("quantity"))
            price = parse_decimal(r.get("unit_price"))
        except ValueError as e:
            errors.append(f"Dòng {n}: {e}")
            qty = price = None
        if qty is not None and qty < 0 or price is not None and price < 0:
            errors.append(f"Dòng {n}: số lượng, đơn giá không được âm")
        try:
            amount = parse_money(r.get("amount"))
            discount = parse_money(r.get("discount")) if use_discount else 0
            vat_amount_raw = r.get("vat_amount")
        except MoneyError as e:
            errors.append(f"Dòng {n}: {e}")
            continue
        if not amount and qty is not None and price is not None:
            amount = round_vnd(qty * price)
        if amount > MAX_MONEY:
            errors.append(f"Dòng {n}: thành tiền quá lớn")
            continue
        if amount <= 0:
            errors.append(f"Dòng {n}: thành tiền phải lớn hơn 0")
        if discount < 0 or discount > amount:
            errors.append(f"Dòng {n}: chiết khấu không hợp lệ")
        rate = (
            str(r.get("vat_rate") or (item.vat_rate if item else "10"))
            .strip()
            .upper()
        )
        if rate not in VAT_RATES:
            errors.append(f"Dòng {n}: thuế suất không hợp lệ")
            rate = "10"
        try:
            vat_amount = (
                parse_money(vat_amount_raw)
                if str(vat_amount_raw or "").strip()
                else vat_of(amount - discount, rate)
            )
        except MoneyError as e:
            errors.append(f"Dòng {n}: {e}")
            vat_amount = 0
        if vat_amount < 0:
            errors.append(f"Dòng {n}: tiền thuế không được âm")
        if VAT_PCT.get(rate, 0) == 0 and vat_amount:
            errors.append(
                f"Dòng {n}: thuế suất {VAT_LABELS[rate]} "
                "thì tiền thuế phải bằng 0"
            )
        account = (r.get("account") or "").strip() or default_account(item)
        acc = accounts.get(account)
        if acc is None:
            errors.append(
                f"Dòng {n}: TK {account or '(trống)'} không hợp lệ "
                "hoặc là TK tổng hợp"
            )
        elif account_prefixes and not account.startswith(
            tuple(account_prefixes)
        ):
            errors.append(
                f"Dòng {n}: TK {account} không phù hợp với loại chứng từ"
            )
        deductible = str(r.get("deductible", "true")).lower() not in (
            "false",
            "0",
            "",
            "off",
            "no",
        )
        wh = r.get("warehouse_id")
        wh_id = int(wh) if wh and str(wh).isdigit() else None
        if wh_id is not None and wh_id not in warehouse_ids:
            errors.append(f"Dòng {n}: kho không hợp lệ")
            wh_id = None
        lines.append(
            TradeLine(
                description=desc[:500],
                amount=amount,
                vat_rate=rate,
                vat_amount=vat_amount,
                item_id=item.id if item else None,
                unit=(
                    r.get("unit") or (item.unit if item else "") or ""
                ).strip()[:30],
                quantity=qty,
                unit_price=price,
                discount_amount=discount,
                account=account,
                deductible=deductible,
                warehouse_id=wh_id,
                item_label=item.label if item else "",
                account_label=f"{acc.code} – {acc.name}" if acc else account,
            )
        )
    if not lines:
        errors.append("Chứng từ phải có ít nhất một dòng hàng hóa, dịch vụ")
    return lines, errors


@dataclass
class VatGroup:
    rate: str
    base: int = 0
    vat: int = 0
    lines: list = field(default_factory=list)


def vat_groups(lines: list[TradeLine], *, key=lambda ln: ln.vat_rate) -> dict:
    groups: dict = {}
    for ln in lines:
        k = key(ln)
        g = groups.setdefault(k, VatGroup(rate=ln.vat_rate))
        g.base += ln.net
        g.vat += ln.vat_amount
        g.lines.append(ln)
    return groups


@dataclass
class TradeHeader:
    date: date
    doc_date: date | None
    partner_id: int | None
    partner_name: str
    address: str
    tax_code: str
    contact_name: str
    invoice_template: str
    invoice_series: str
    invoice_no: str
    invoice_date: date | None
    payment_method: str
    bank_account_id: int | None
    due_date: date | None
    description: str
    note: str
    original_entry_id: int | None = None


def parse_header(form: dict, *, errors: dict) -> TradeHeader | None:
    def _date(name, required=False):
        try:
            d = parse_date(form.get(name))
        except ValueError:
            errors[name] = "Ngày không hợp lệ"
            return None
        if required and d is None:
            errors[name] = "Nhập ngày"
        return d

    d = _date("date", True)
    inv_date = _date("invoice_date")
    due = _date("due_date")
    method = form.get("payment_method") or form.get("payment") or "credit"
    if method not in ("credit", "cash", "bank"):
        errors["payment_method"] = "Hình thức thanh toán không hợp lệ"
        method = "credit"
    bank_id = None
    if method == "bank":
        try:
            bank_id = int(form.get("bank_account_id") or 0) or None
        except ValueError:
            bank_id = None
        if bank_id is None:
            errors["bank_account_id"] = "Chọn tài khoản ngân hàng"
    try:
        pid = int(form.get("partner_id") or 0) or None
    except ValueError:
        pid = None
    try:
        orig = int(form.get("original_entry_id") or 0) or None
    except ValueError:
        orig = None
    if d is None:
        return None
    return TradeHeader(
        date=d,
        doc_date=inv_date or d,
        partner_id=pid,
        partner_name=(form.get("partner_name") or "").strip()[:255],
        address=(form.get("address") or "").strip()[:300],
        tax_code=(form.get("tax_code") or "").strip()[:20],
        contact_name=(form.get("contact_name") or "").strip()[:200],
        invoice_template=(form.get("invoice_template") or "").strip()[:20],
        invoice_series=(form.get("invoice_series") or "").strip().upper()[:20],
        invoice_no=(form.get("invoice_no") or "").strip()[:30],
        invoice_date=inv_date,
        payment_method=method,
        bank_account_id=bank_id,
        due_date=due,
        description=(form.get("description") or "").strip()[:500],
        note=(form.get("note") or "").strip(),
        original_entry_id=orig,
    )


def invoice_ref(series: str, number: str) -> str:
    if series and number:
        return f"{series}-{number}"
    return number or series or ""


# -------------------- report view period (month/quarter/year/custom)


@dataclass(frozen=True)
class ViewPeriod:
    mode: str
    start: date
    end: date
    label: str
    year: int | None = None
    quarter: int | None = None

    @property
    def query(self) -> str:
        return (
            f"mode=custom&start={self.start.isoformat()}"
            f"&end={self.end.isoformat()}"
        )


def resolve_view_period(params, default_mode: str = "month") -> ViewPeriod:
    """Resolve the view period from request params.

    Reads mode, month=YYYY-MM, quarter, year, start and end as sent by
    the UI's period_selector.
    """
    from app.core.dates import fmt_date, month_bounds, today

    t = today()
    mode = params.get("mode") or default_mode
    try:
        if mode == "quarter":
            y = int(params.get("year") or t.year)
            q = int(params.get("quarter") or (t.month - 1) // 3 + 1)
            q = min(max(q, 1), 4)
            s = date(y, (q - 1) * 3 + 1, 1)
            e = month_bounds(y, q * 3)[1]
            return ViewPeriod("quarter", s, e, f"Quý {q}/{y}", y, q)
        if mode == "year":
            y = int(params.get("year") or t.year)
            return ViewPeriod(
                "year", date(y, 1, 1), date(y, 12, 31), f"Năm {y}", y
            )
        if mode == "custom":
            s = parse_date(params.get("start")) or date(t.year, 1, 1)
            e = parse_date(params.get("end")) or t
            if e < s:
                s, e = e, s
            return ViewPeriod(
                "custom",
                s,
                e,
                f"Từ ngày {fmt_date(s)} đến ngày {fmt_date(e)}",
                s.year,
            )
        month = params.get("month")
        y, m = (
            (int(x) for x in month.split("-")[:2])
            if month
            else (t.year, t.month)
        )
        s, e = month_bounds(y, m)
        return ViewPeriod(
            "month", s, e, f"Tháng {m:02d}/{y}", y, (m - 1) // 3 + 1
        )
    except (ValueError, TypeError):
        s, e = month_bounds(t.year, t.month)
        return ViewPeriod(
            "month", s, e, f"Tháng {t.month:02d}/{t.year}", t.year
        )
