"""Receipt/payment purposes.

A list the cashier picks from instead of account numbers, stored in
`company.settings["cash_purposes"]`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import BusinessError
from app.features.accounts.models import Account
from app.features.org.models import Company


@dataclass
class Purpose:
    label: str
    account: str
    # suggested partner type: customer | supplier | employee | ""
    role: str = ""


DEFAULT_IN = [
    Purpose("Thu tiền khách hàng trả nợ", "131", "customer"),
    Purpose("Thu tiền bán hàng hóa", "5111", "customer"),
    Purpose("Thu tiền cung cấp dịch vụ", "5113", "customer"),
    Purpose("Rút tiền gửi ngân hàng về quỹ", "1121"),
    Purpose("Nhận vốn góp của chủ sở hữu", "4111"),
    Purpose("Thu hồi tạm ứng", "141", "employee"),
    Purpose("Vay tiền", "3411"),
    Purpose("Thu lãi tiền gửi, tiền cho vay", "515"),
    Purpose("Thu nhập khác", "711"),
    Purpose("Thu khác", "1388"),
]
DEFAULT_OUT = [
    Purpose("Trả tiền nhà cung cấp", "331", "supplier"),
    Purpose("Chi phí quản lý (văn phòng phẩm, tiếp khách…)", "6422"),
    Purpose("Chi phí bán hàng", "6421"),
    Purpose("Chi tạm ứng cho nhân viên", "141", "employee"),
    Purpose("Trả lương nhân viên", "334", "employee"),
    Purpose("Nộp tiền mặt vào ngân hàng", "1121"),
    Purpose("Nộp thuế GTGT", "33311"),
    Purpose("Nộp thuế TNDN", "3334"),
    Purpose("Nộp thuế TNCN", "3335"),
    Purpose("Nộp lệ phí, thuế khác", "3339"),
    Purpose("Nộp bảo hiểm xã hội", "3383"),
    Purpose("Mua công cụ dụng cụ", "153"),
    Purpose("Trả lãi vay", "635"),
    Purpose("Trả nợ gốc vay", "3411"),
    Purpose("Chi phí khác", "811"),
]


def _load(raw: list | None, default: list[Purpose]) -> list[Purpose]:
    if not raw:
        return list(default)
    out = []
    for p in raw:
        if isinstance(p, dict) and p.get("label") and p.get("account"):
            out.append(
                Purpose(
                    str(p["label"]),
                    str(p["account"]),
                    str(p.get("role") or ""),
                )
            )
    return out or list(default)


def purposes(company: Company) -> dict[str, list[Purpose]]:
    raw = (company.settings or {}).get("cash_purposes") or {}
    return {
        "in": _load(raw.get("in"), DEFAULT_IN),
        "out": _load(raw.get("out"), DEFAULT_OUT),
    }


def for_kind(company: Company, kind: str) -> list[Purpose]:
    return purposes(company)["in" if kind in ("PT", "BC") else "out"]


def options(db: Session, company: Company, kind: str) -> list[dict]:
    """Return purposes with account details.

    Only active, postable accounts are included.
    """
    accounts = {
        a.code: a
        for a in db.scalars(
            select(Account).where(Account.company_id == company.id)
        )
    }
    out = []
    for p in for_kind(company, kind):
        acc = accounts.get(p.account)
        if acc is None or not acc.is_postable or not acc.is_active:
            continue
        out.append(
            {
                "label": p.label,
                "account": p.account,
                "role": p.role,
                "account_name": acc.name,
                "partner_mode": acc.partner_mode,
                "needs_bank": acc.needs_bank,
            }
        )
    return out


def save(db: Session, ctx, data: dict[str, list[dict]]) -> None:
    from app.features.org.service import update_company_settings

    accounts = {
        a.code: a
        for a in db.scalars(
            select(Account).where(Account.company_id == ctx.company_id)
        )
    }
    clean: dict[str, list[dict]] = {}
    for key in ("in", "out"):
        rows = []
        for p in data.get(key, []):
            label = (p.get("label") or "").strip()[:120]
            account = (p.get("account") or "").strip()
            if not label and not account:
                continue
            acc = accounts.get(account)
            if not label or acc is None or not acc.is_postable:
                raise BusinessError(
                    f"Mục đích '{label or account}': "
                    f"tài khoản {account or '(trống)'} không hợp lệ "
                    "(phải là tài khoản chi tiết)"
                )
            rows.append(
                asdict(Purpose(label, account, (p.get("role") or "").strip()))
            )
        clean[key] = rows
    update_company_settings(db, ctx, "cash_purposes", clean)
