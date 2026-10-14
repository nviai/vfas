"""Effective-dated payroll, insurance and PIT parameters.

The default values (DEFAULTS) are copied into each company on first
use; users edit them or add new versions in the UI. Sources and open
questions: docs/requests/payroll.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BusinessError, NotFoundError
from app.features.payroll.models import PayrollParam

PIT_7 = [
    [5_000_000, "5"],
    [10_000_000, "10"],
    [18_000_000, "15"],
    [32_000_000, "20"],
    [52_000_000, "25"],
    [80_000_000, "30"],
    [None, "35"],
]
PIT_5 = [
    [10_000_000, "5"],
    [30_000_000, "10"],
    [60_000_000, "20"],
    [100_000_000, "30"],
    [None, "35"],
]
INSURANCE = {
    "emp_bhxh": "8",
    "emp_bhyt": "1.5",
    "emp_bhtn": "1",
    "er_bhxh": "17.5",
    "er_bhyt": "3",
    "er_bhtn": "1",
    "er_kpcd": "2",
    "cap_multiple": 20,
}

# (key, effective from, value, legal basis)
DEFAULTS: list[tuple[str, date, dict, str]] = [
    (
        "base_salary",
        date(2024, 7, 1),
        {"amount": 2_340_000},
        "Nghị định 73/2024/NĐ-CP",
    ),
    (
        "base_salary",
        date(2026, 7, 1),
        {"amount": 2_530_000},
        "Nghị định 161/2026/NĐ-CP",
    ),
    (
        "regional_min_wage",
        date(2024, 7, 1),
        {"I": 4_960_000, "II": 4_410_000, "III": 3_860_000, "IV": 3_450_000},
        "Nghị định 74/2024/NĐ-CP",
    ),
    (
        "regional_min_wage",
        date(2026, 1, 1),
        {"I": 5_310_000, "II": 4_730_000, "III": 4_140_000, "IV": 3_700_000},
        "Nghị định 293/2025/NĐ-CP",
    ),
    (
        "insurance_rates",
        date(2020, 1, 1),
        INSURANCE,
        (
            "Luật BHXH 2024, Luật BHYT, Luật Việc làm; "
            "KPCĐ 2% theo Nghị định 191/2013/NĐ-CP"
        ),
    ),
    (
        "family_deduction",
        date(2020, 7, 1),
        {"self": 11_000_000, "dependent": 4_400_000},
        "Nghị quyết 954/2020/UBTVQH14",
    ),
    (
        "family_deduction",
        date(2026, 1, 1),
        {"self": 15_500_000, "dependent": 6_200_000},
        "Nghị quyết 110/2025/UBTVQH15",
    ),
    (
        "pit_brackets",
        date(2009, 1, 1),
        {"brackets": PIT_7},
        "Luật Thuế TNCN 2007 (sửa đổi 2012) – biểu 7 bậc",
    ),
    (
        "pit_brackets",
        date(2026, 1, 1),
        {"brackets": PIT_5},
        (
            "Luật Thuế TNCN 109/2025/QH15 – biểu 5 bậc, "
            "tiền lương áp dụng từ kỳ tính thuế 2026"
        ),
    ),
    (
        "flat_rates",
        date(2014, 1, 1),
        {
            "short_term_rate": "10",
            "short_term_threshold": 2_000_000,
            "non_resident_rate": "20",
        },
        "Thông tư 111/2013/TT-BTC",
    ),
    (
        "flat_rates",
        date(2026, 7, 1),
        {
            "short_term_rate": "10",
            "short_term_threshold": 5_000_000,
            "non_resident_rate": "20",
        },
        "Nghị định 253/2026/NĐ-CP (khoản 2 Điều 50)",
    ),
]

KEY_LABELS = {
    "base_salary": "Lương cơ sở (mức tham chiếu)",
    "regional_min_wage": "Lương tối thiểu vùng",
    "insurance_rates": "Tỷ lệ bảo hiểm, KPCĐ",
    "family_deduction": "Giảm trừ gia cảnh",
    "pit_brackets": "Biểu thuế TNCN lũy tiến (tháng)",
    "flat_rates": "Thuế suất khấu trừ cố định",
}


@dataclass
class Params:
    on: date
    base_salary: int
    regional_min_wage: dict[str, int]
    rates: dict[str, Decimal]
    cap_multiple: int
    self_deduction: int
    dependent_deduction: int
    brackets: list[tuple[int | None, Decimal]]
    short_term_rate: Decimal
    short_term_threshold: int
    non_resident_rate: Decimal

    def min_wage(self, region: str) -> int:
        return int(
            self.regional_min_wage.get(region)
            or self.regional_min_wage.get("I")
            or 0
        )


def _pick(rows: list[tuple[date, dict]], on: date) -> dict:
    best = None
    for eff, value in rows:
        if eff <= on and (best is None or eff > best[0]):
            best = (eff, value)
    if best is None:  # before every version: use the earliest one
        best = min(rows, key=lambda r: r[0])
    return best[1]


def _build(on: date, by_key: dict[str, list[tuple[date, dict]]]) -> Params:
    rates = _pick(by_key["insurance_rates"], on)
    fam = _pick(by_key["family_deduction"], on)
    flat = _pick(by_key["flat_rates"], on)
    brackets = [
        (None if b[0] is None else int(b[0]), Decimal(str(b[1])))
        for b in _pick(by_key["pit_brackets"], on)["brackets"]
    ]
    return Params(
        on=on,
        base_salary=int(_pick(by_key["base_salary"], on)["amount"]),
        regional_min_wage={
            k: int(v)
            for k, v in _pick(by_key["regional_min_wage"], on).items()
        },
        rates={
            k: Decimal(str(v)) for k, v in rates.items() if k != "cap_multiple"
        },
        cap_multiple=int(rates.get("cap_multiple", 20)),
        self_deduction=int(fam["self"]),
        dependent_deduction=int(fam["dependent"]),
        brackets=brackets,
        short_term_rate=Decimal(str(flat["short_term_rate"])),
        short_term_threshold=int(flat["short_term_threshold"]),
        non_resident_rate=Decimal(str(flat["non_resident_rate"])),
    )


def _default_rows() -> dict[str, list[tuple[date, dict]]]:
    out: dict[str, list[tuple[date, dict]]] = {}
    for key, eff, value, _src in DEFAULTS:
        out.setdefault(key, []).append((eff, value))
    return out


def default_params(on: date) -> Params:
    """Default parameters (no DB needed) for AI estimates and tests."""
    return _build(on, _default_rows())


def ensure_params(db: Session, company_id: int) -> None:
    """Copy the default parameters into the company for missing keys."""
    have = set(
        db.scalars(
            select(PayrollParam.key).where(
                PayrollParam.company_id == company_id
            )
        )
    )
    added = False
    for key, eff, value, src in DEFAULTS:
        if key in have:
            continue
        db.add(
            PayrollParam(
                company_id=company_id,
                key=key,
                effective_from=eff,
                value=value,
                source=src,
                updated_at=datetime.now(timezone.utc),
            )
        )
        added = True
    if added:
        db.flush()


def list_params(db: Session, company_id: int) -> dict[str, list[PayrollParam]]:
    ensure_params(db, company_id)
    rows = db.scalars(
        select(PayrollParam)
        .where(PayrollParam.company_id == company_id)
        .order_by(PayrollParam.key, PayrollParam.effective_from)
    )
    out: dict[str, list[PayrollParam]] = {k: [] for k in KEY_LABELS}
    for r in rows:
        out.setdefault(r.key, []).append(r)
    return out


def resolve(db: Session, company_id: int, on: date) -> Params:
    by_key: dict[str, list[tuple[date, dict]]] = {}
    for key, rows in list_params(db, company_id).items():
        by_key[key] = [(r.effective_from, r.value) for r in rows]
    defaults = _default_rows()
    for key in KEY_LABELS:
        if not by_key.get(key):
            by_key[key] = defaults[key]
    return _build(on, by_key)


# ------------------------------------------------------ edit parameters


def parse_value(key: str, form: dict) -> dict:
    """Read one parameter version's value from the form.

    Amounts may be entered as "1.000.000" or "15,5tr".
    """
    from app.core.money import MoneyError, parse_vnd

    def money(name: str) -> int:
        try:
            v = parse_vnd(form.get(name) or "")
        except MoneyError as e:
            raise BusinessError(f"{name}: {e}") from e
        if v < 0:
            raise BusinessError("Số tiền không được âm")
        return v

    def pct(name: str) -> str:
        raw = (form.get(name) or "").strip().replace(",", ".")
        try:
            d = Decimal(raw)
        except Exception as e:  # noqa: BLE001 - any parse error is invalid input
            raise BusinessError(f"Tỷ lệ không hợp lệ: {raw or name}") from e
        if d < 0 or d > 100:
            raise BusinessError("Tỷ lệ phải từ 0 đến 100")
        return format(d.normalize(), "f")

    if key == "base_salary":
        return {"amount": money("amount")}
    if key == "regional_min_wage":
        return {r: money(f"region_{r}") for r in ("I", "II", "III", "IV")}
    if key == "insurance_rates":
        value: dict = {
            k: pct(k)
            for k in (
                "emp_bhxh",
                "emp_bhyt",
                "emp_bhtn",
                "er_bhxh",
                "er_bhyt",
                "er_bhtn",
                "er_kpcd",
            )
        }
        try:
            value["cap_multiple"] = int(form.get("cap_multiple") or 20)
        except ValueError as e:
            raise BusinessError("Số lần trần đóng không hợp lệ") from e
        return value
    if key == "family_deduction":
        return {"self": money("self"), "dependent": money("dependent")}
    if key == "flat_rates":
        return {
            "short_term_rate": pct("short_term_rate"),
            "short_term_threshold": money("short_term_threshold"),
            "non_resident_rate": pct("non_resident_rate"),
        }
    if key == "pit_brackets":
        # One line per bracket: "upper bound; rate". The last line
        # leaves the upper bound empty.
        brackets = []
        lines = [
            ln.strip()
            for ln in (form.get("brackets") or "").splitlines()
            if ln.strip()
        ]
        for ln in lines:
            parts = [p.strip() for p in ln.replace("\t", ";").split(";")]
            if len(parts) != 2:
                raise BusinessError(f"Dòng biểu thuế không hợp lệ: {ln}")
            upper = (
                None if parts[0] in ("", "-", "…") else money_from(parts[0])
            )
            brackets.append([upper, pct_from(parts[1])])
        if not brackets or brackets[-1][0] is not None:
            raise BusinessError(
                "Biểu thuế phải có bậc cuối không giới hạn (để trống ngưỡng)"
            )
        uppers = [b[0] for b in brackets[:-1]]
        if (
            any(u is None for u in uppers)
            or uppers != sorted(uppers)
            or len(set(uppers)) != len(uppers)
        ):
            raise BusinessError("Ngưỡng các bậc phải tăng dần")
        return {"brackets": brackets}
    raise BusinessError("Tham số không hợp lệ")


def money_from(text: str) -> int:
    from app.core.money import MoneyError, parse_vnd

    try:
        return parse_vnd(text)
    except MoneyError as e:
        raise BusinessError(str(e)) from e


def pct_from(text: str) -> str:
    try:
        d = Decimal(text.replace("%", "").replace(",", ".").strip())
    except Exception as e:  # noqa: BLE001 - any parse error is invalid input
        raise BusinessError(f"Thuế suất không hợp lệ: {text}") from e
    if d < 0 or d > 100:
        raise BusinessError("Thuế suất phải từ 0 đến 100")
    return format(d.normalize(), "f")


def save_version(
    db: Session,
    ctx,
    key: str,
    effective_from: date,
    value: dict,
    source: str = "",
) -> PayrollParam:
    if key not in KEY_LABELS:
        raise BusinessError("Tham số không hợp lệ")
    ensure_params(db, ctx.company_id)
    row = db.scalar(
        select(PayrollParam).where(
            PayrollParam.company_id == ctx.company_id,
            PayrollParam.key == key,
            PayrollParam.effective_from == effective_from,
        )
    )
    before = dict(row.value) if row else None
    if row is None:
        row = PayrollParam(
            company_id=ctx.company_id,
            key=key,
            effective_from=effective_from,
            value=value,
            source=source[:300],
        )
        db.add(row)
    else:
        row.value = value
        row.source = source[:300] or row.source
    row.updated_at = datetime.now(timezone.utc)
    db.flush()
    write_audit(
        db,
        ctx,
        action="payroll.param",
        entity_type="payroll_param",
        entity_id=row.id,
        summary=(
            f"Cập nhật {KEY_LABELS[key]} hiệu lực từ {effective_from:%d/%m/%Y}"
        ),
        before=before,
        after=value,
    )
    db.commit()
    return row


def delete_version(db: Session, ctx, param_id: int) -> None:
    row = db.get(PayrollParam, param_id)
    if row is None or row.company_id != ctx.company_id:
        raise NotFoundError()
    others = db.scalars(
        select(PayrollParam.id).where(
            PayrollParam.company_id == ctx.company_id,
            PayrollParam.key == row.key,
            PayrollParam.id != row.id,
        )
    ).all()
    if not others:
        raise BusinessError("Phải giữ ít nhất một mức cho mỗi tham số")
    write_audit(
        db,
        ctx,
        action="payroll.param_delete",
        entity_type="payroll_param",
        entity_id=row.id,
        summary=(
            f"Xóa mức {KEY_LABELS.get(row.key, row.key)} từ "
            f"{row.effective_from:%d/%m/%Y}"
        ),
        before=dict(row.value),
    )
    db.delete(row)
    db.commit()
