"""PDF/Excel exports of stock ledgers and stock voucher printing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.core.dates import fmt_date
from app.core.pdf import render_pdf
from app.core.xlsx import Col, ReportBook
from app.features.inventory.models import ISSUE, RECEIPT, TRANSFER, StockDoc
from app.features.inventory.reports import FORMS, ItemLedger, Summary


@dataclass
class PeriodInfo:
    mode: str
    start: date
    end: date
    label: str
    year: int | None = None
    quarter: int | None = None


def period_label(start: date, end: date) -> str:
    return f"Từ ngày {fmt_date(start)} đến ngày {fmt_date(end)}"


def _num(v):
    return float(v) if v is not None else None


# ---------------------------------------------------------------- PDF


def ledger_pdf(company, led: ItemLedger, *, card: bool) -> bytes:
    code, title = FORMS["card" if card else "ledger"]
    return render_pdf(
        "inventory/print/card.html" if card else "inventory/print/ledger.html",
        {
            "company": company,
            "led": led,
            "form_code": code,
            "title": title,
            "period_label": period_label(led.start, led.end),
        },
    )


def summary_pdf(company, s: Summary, warehouse=None) -> bytes:
    code, title = FORMS["summary"]
    return render_pdf(
        "inventory/print/summary.html",
        {
            "company": company,
            "s": s,
            "form_code": code,
            "title": title,
            "warehouse": warehouse,
            "period_label": period_label(s.start, s.end),
        },
    )


def doc_pdf(company, doc: StockDoc, entry=None, moves=None) -> bytes:
    """Render a stock voucher as PDF.

    For costed issues/transfers, the unit price and amount come from
    the stock movements.
    """
    code = {RECEIPT: "01 - VT", ISSUE: "02 - VT", TRANSFER: None}[doc.kind]
    title = {
        RECEIPT: "PHIẾU NHẬP KHO",
        ISSUE: "PHIẾU XUẤT KHO",
        TRANSFER: "PHIẾU CHUYỂN KHO",
    }[doc.kind]
    debit = credit = None
    if doc.kind == RECEIPT:
        debit = (
            sorted({ln.debit_account for ln in entry.lines})
            if entry is not None
            else []
        )
        credit = [doc.counter_account] if doc.counter_account else []
    elif doc.kind == ISSUE:
        debit = [doc.counter_account] if doc.counter_account else []
        credit = sorted({ln.item.stock_account or "156" for ln in doc.lines})
    line_values = {}
    if doc.kind != RECEIPT:
        for m in moves or []:
            if m.qty_out and m.source_line_id and m.costed:
                line_values[m.source_line_id] = (m.unit_cost, int(m.amount))
    total = (
        sum(v[1] for v in line_values.values())
        if doc.kind != RECEIPT
        else doc.total
    )
    return render_pdf(
        "inventory/print/voucher.html",
        {
            "company": company,
            "doc": doc,
            "form_code": code,
            "title": title,
            "line_values": line_values,
            "total": total,
            "debit": debit,
            "credit": credit,
            "draft": (entry.status if entry is not None else doc.status)
            == "draft",
        },
    )


# ---------------------------------------------------------------- Excel

LEDGER_COLS = [
    Col("Số hiệu", 14),
    Col("Ngày tháng", 12, "date"),
    Col("Diễn giải", 36),
    Col("TK đối ứng", 10),
    Col("Đơn giá", 14, "money"),
    Col("Nhập SL", 11, "qty"),
    Col("Nhập thành tiền", 16, "money"),
    Col("Xuất SL", 11, "qty"),
    Col("Xuất thành tiền", 16, "money"),
    Col("Tồn SL", 11, "qty"),
    Col("Tồn thành tiền", 16, "money"),
    Col("Ghi chú", 14),
]
CARD_COLS = [
    Col("STT", 6, "int"),
    Col("Ngày tháng", 12, "date"),
    Col("Số hiệu nhập", 14),
    Col("Số hiệu xuất", 14),
    Col("Diễn giải", 40),
    Col("Ngày nhập, xuất", 12, "date"),
    Col("Nhập", 12, "qty"),
    Col("Xuất", 12, "qty"),
    Col("Tồn", 12, "qty"),
    Col("Ký xác nhận của kế toán", 16),
]
SUMMARY_COLS = [
    Col("STT", 6, "int"),
    Col("Mã", 12),
    Col("Tên vật liệu, dụng cụ, sản phẩm, hàng hóa", 36),
    Col("ĐVT", 8),
    Col("Tồn đầu SL", 11, "qty"),
    Col("Tồn đầu tiền", 15, "money"),
    Col("Nhập SL", 11, "qty"),
    Col("Nhập tiền", 15, "money"),
    Col("Xuất SL", 11, "qty"),
    Col("Xuất tiền", 15, "money"),
    Col("Tồn cuối SL", 11, "qty"),
    Col("Tồn cuối tiền", 15, "money"),
]


def ledger_xlsx(company, led: ItemLedger) -> bytes:
    code, title = FORMS["ledger"]
    book = ReportBook()
    wh = f" – Kho: {led.warehouse.name}" if led.warehouse else ""
    ws = book.add_sheet(
        "S06-DNN",
        company=company,
        form_code=code,
        title=title,
        subtitle=period_label(led.start, led.end),
        columns=LEDGER_COLS,
        extra_lines=[
            f"Tài khoản: {led.item.stock_account or ''}{wh}",
            "Tên vật liệu, dụng cụ (sản phẩm, hàng hóa): "
            f"{led.item.code} – {led.item.name}"
            f" · Đơn vị tính: {led.item.unit}",
        ],
    )
    first = book.row(
        ws,
        [
            "",
            None,
            "Số dư đầu kỳ",
            "",
            None,
            None,
            None,
            None,
            None,
            _num(led.opening_qty),
            led.opening_val,
            "",
        ],
        bold=True,
    )
    for r in led.rows:
        idx = ws.max_row + 1
        book.row(
            ws,
            [
                r.number,
                r.date,
                r.description,
                r.contra or "",
                _num(r.unit_cost),
                _num(r.qty_in) or None,
                r.amount_in or None,
                _num(r.qty_out) or None,
                r.amount_out or None,
                f"=J{idx - 1}+F{idx}-H{idx}",
                f"=K{idx - 1}+G{idx}-I{idx}",
                "Chưa tính giá" if r.pending else "",
            ],
        )
    last = ws.max_row
    book.row(
        ws,
        [
            "",
            None,
            "Cộng tháng / kỳ",
            "",
            None,
            f"=SUM(F{first + 1}:F{last})" if last > first else 0,
            f"=SUM(G{first + 1}:G{last})" if last > first else 0,
            f"=SUM(H{first + 1}:H{last})" if last > first else 0,
            f"=SUM(I{first + 1}:I{last})" if last > first else 0,
            f"=J{first}+F{ws.max_row + 1}-H{ws.max_row + 1}",
            f"=K{first}+G{ws.max_row + 1}-I{ws.max_row + 1}",
            "",
        ],
        bold=True,
    )
    book.signatures(ws, company)
    return book.to_bytes()


def card_xlsx(company, led: ItemLedger) -> bytes:
    code, title = FORMS["card"]
    book = ReportBook()
    ws = book.add_sheet(
        "S08-DNN",
        company=company,
        form_code=code,
        title=title,
        subtitle=period_label(led.start, led.end),
        columns=CARD_COLS,
        extra_lines=[
            f"Tên, nhãn hiệu, quy cách vật tư: {led.item.name}",
            f"Đơn vị tính: {led.item.unit} · Mã số: {led.item.code}"
            + (f" · Kho: {led.warehouse.name}" if led.warehouse else ""),
        ],
    )
    first = book.row(
        ws,
        [
            None,
            None,
            "",
            "",
            "Tồn đầu kỳ",
            None,
            None,
            None,
            _num(led.opening_qty),
            "",
        ],
        bold=True,
    )
    for n, r in enumerate(led.rows, start=1):
        idx = ws.max_row + 1
        book.row(
            ws,
            [
                n,
                r.date,
                r.number if r.qty_in else "",
                r.number if r.qty_out else "",
                r.description,
                r.date,
                _num(r.qty_in) or None,
                _num(r.qty_out) or None,
                f"=I{idx - 1}+G{idx}-H{idx}",
                "",
            ],
        )
    last = ws.max_row
    book.row(
        ws,
        [
            None,
            None,
            "",
            "",
            "Cộng cuối kỳ",
            None,
            f"=SUM(G{first + 1}:G{last})" if last > first else 0,
            f"=SUM(H{first + 1}:H{last})" if last > first else 0,
            f"=I{last}",
            "",
        ],
        bold=True,
    )
    book.signatures(ws, company, left="Thủ kho", middle="Kế toán trưởng")
    return book.to_bytes()


def summary_xlsx(company, s: Summary, warehouse=None) -> bytes:
    code, title = FORMS["summary"]
    book = ReportBook()
    ws = book.add_sheet(
        "S07-DNN",
        company=company,
        form_code=code,
        title=title,
        subtitle=period_label(s.start, s.end),
        columns=SUMMARY_COLS,
        extra_lines=[f"Kho: {warehouse.name}"] if warehouse else None,
    )
    for n, r in enumerate(s.rows, start=1):
        idx = (
            ws.max_row + 1
            if ws.max_row >= book.first_data_row[id(ws)]
            else book.first_data_row[id(ws)]
        )
        name = r.item.name + (f" ({r.warehouse.name})" if r.warehouse else "")
        book.row(
            ws,
            [
                n,
                r.item.code,
                name,
                r.item.unit,
                _num(r.opening_qty),
                r.opening_val,
                _num(r.in_qty),
                r.in_val,
                _num(r.out_qty),
                r.out_val,
                f"=E{idx}+G{idx}-I{idx}",
                f"=F{idx}+H{idx}-J{idx}",
            ],
        )
    book.total_row(ws, "Cộng", sum_cols=[5, 7, 9, 11], label_col=3)
    book.signatures(ws, company)
    return book.to_bytes()
