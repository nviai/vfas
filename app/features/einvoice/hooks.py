"""Keep issued e-invoices and the books consistent.

An invoice that has been sent to the provider locks its sales
document: no editing, deleting or cancelling. Corrections are made
with an adjustment invoice (a sales return/allowance document).
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.errors import BusinessError
from app.core.registry import on, posting_validator
from app.features.einvoice.models import EInvoiceIssue
from app.features.einvoice.providers.base import ACTIVE_STATES

LOCKED = (
    "Chứng từ đã phát hành hóa đơn điện tử {number} – không {action} được. "
    "Sai sót xử lý bằng hóa đơn điều chỉnh (lập chứng từ giảm giá/trả lại)."
)


def _issue_for_entry(db, entry_id: int) -> EInvoiceIssue | None:
    return db.scalar(
        select(EInvoiceIssue).where(
            EInvoiceIssue.entry_id == entry_id,
            EInvoiceIssue.status.in_(ACTIVE_STATES),
        )
    )


def _guard(db, entry_id: int, action: str) -> None:
    issue = _issue_for_entry(db, entry_id)
    if issue is not None:
        raise BusinessError(
            LOCKED.format(
                number=issue.display_number or "(đang xử lý)", action=action
            )
        )


@posting_validator
def block_cancel(db, ctx, entry, action: str) -> None:
    if action == "cancel" and entry.source == "sales":
        _guard(db, entry.id, "hủy")


@on("entry.deleted")
def block_delete(db, ctx, entry) -> None:
    if entry.source == "sales":
        _guard(db, entry.id, "xóa")


@on("sales.before_update")
def block_update(db, ctx, doc) -> None:
    if doc.entry_id:
        _guard(db, doc.entry_id, "sửa")
