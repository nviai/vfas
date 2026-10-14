"""PDF printing (WeasyPrint).

Cash receipt/payment vouchers 01-TT/02-TT, books S04a/S04b/S05 and
the cash count sheet 08a-TT. Always in Vietnamese.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.money import amount_in_words
from app.core.pdf import render_pdf
from app.features.cash.books import MoneyBook
from app.features.cash.models import CashCount
from app.features.journal.models import CANCELLED, DRAFT, JournalEntry
from app.features.org.models import Company

VOUCHER_FORMS = {
    "PT": ("01 - TT", "PHIẾU THU"),
    "PC": ("02 - TT", "PHIẾU CHI"),
}


def _accounts(values: list[str]) -> str:
    seen = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return ", ".join(seen)


def voucher_pdf(
    db: Session, company: Company, e: JournalEntry, creator_name: str = ""
) -> bytes:
    """Render a cash receipt (01-TT) or payment (02-TT), A5 landscape.

    Drafts and cancelled vouchers get a watermark.
    """
    is_receipt = e.doc_type == "PT"
    code, title = VOUCHER_FORMS["PT" if is_receipt else "PC"]
    director = (
        company.legal_rep_title or "Giám đốc",
        "(Ký, họ tên, đóng dấu)",
        company.legal_rep,
    )
    chief = ("Kế toán trưởng", "(Ký, họ tên)", company.chief_accountant)
    partner = (
        "Người nộp tiền" if is_receipt else "Người nhận tiền",
        "(Ký, họ tên)",
        e.contact_name,
    )
    preparer = (
        "Người lập phiếu",
        "(Ký, họ tên)",
        creator_name or company.preparer,
    )
    cashier = ("Thủ quỹ", "(Ký, họ tên)", company.cashier)
    signers = (
        [director, chief, partner, preparer, cashier]
        if is_receipt
        else [director, chief, cashier, preparer, partner]
    )
    watermark = (
        "NHÁP – CHƯA GHI SỔ"
        if e.status == DRAFT
        else "ĐÃ HỦY"
        if e.status == CANCELLED
        else None
    )
    return render_pdf(
        "cash/print/voucher.html",
        {
            "company": company,
            "entry": e,
            "code": code,
            "title": title,
            "is_receipt": is_receipt,
            "number": e.number if e.status != DRAFT else None,
            "doc_date": e.doc_date or e.date,
            "book_no": company.setting("cash_book_no", ""),
            "debit_accounts": _accounts([ln.debit_account for ln in e.lines]),
            "credit_accounts": _accounts(
                [ln.credit_account for ln in e.lines]
            ),
            "amount_words": amount_in_words(e.total) + ".",
            "signers": signers,
            "watermark": watermark,
        },
    )


def book_pdf(
    company: Company, book: MoneyBook, period_label: str, subtitles: list[str]
) -> bytes:
    return render_pdf(
        "cash/print/book.html",
        {
            "company": company,
            "book": book,
            "period_label": period_label,
            "subtitles": subtitles,
            "detail": book.form != "s04a",
            "s05": book.form == "s05",
        },
    )


def cashcount_pdf(company: Company, cc: CashCount) -> bytes:
    hour, _, minute = (cc.count_time or "").partition(":")
    return render_pdf(
        "cash/print/cashcount.html",
        {
            "company": company,
            "cc": cc,
            "lines": [ln for ln in cc.lines if ln.quantity],
            "hour": hour or "....",
            "minute": minute or "....",
            "difference": cc.book_balance - cc.actual_total,
            "surplus": max(cc.actual_total - cc.book_balance, 0),
            "shortage": max(cc.book_balance - cc.actual_total, 0),
        },
    )
