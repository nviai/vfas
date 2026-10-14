"""Export reports to Excel (openpyxl) in the Ministry of Finance layout.

Shared by all simple ledgers/reports; ledgers that need special
formulas (running balance columns, ...) can use `ReportBook.sheet` to
write directly.

    book = ReportBook()
    ws = book.add_sheet(
        "S03a-DNN",
        company=company,
        form_code="S03a-DNN",
        title="SỔ NHẬT KÝ CHUNG",
        subtitle="Năm 2026",
        columns=[Col("Ngày", 12, "date"), Col("Số tiền", 16, "money")],
    )
    book.rows(ws, rows)  # list[list]
    book.total_row(ws, "Cộng", sum_cols=[1])
    book.signatures(ws, company)
    return book.to_bytes()
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from app.core.charts.tt133 import ISSUED

MONEY_FMT = '#,##0;-#,##0;""'
QTY_FMT = '#,##0.###;-#,##0.###;""'
DATE_FMT = "dd/mm/yyyy"
THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BOLD = Font(name="Times New Roman", bold=True, size=11)
NORMAL = Font(name="Times New Roman", size=11)
TITLE = Font(name="Times New Roman", bold=True, size=14)
ITALIC = Font(name="Times New Roman", italic=True, size=10)


@dataclass
class Col:
    title: str
    width: float = 14
    kind: str = "text"  # text | money | qty | date | int


class ReportBook:
    def __init__(self):
        self.wb = Workbook()
        self._first = True
        self.header_row: dict[int, int] = {}
        self.first_data_row: dict[int, int] = {}
        self.cols: dict[int, list[Col]] = {}

    def add_sheet(
        self,
        name: str,
        *,
        company=None,
        form_code: str | None = None,
        title: str = "",
        subtitle: str = "",
        columns: list[Col],
        issued: str = ISSUED,
        extra_lines: list[str] | None = None,
    ):
        if self._first:
            ws = self.wb.active
            ws.title = name[:31]
            self._first = False
        else:
            ws = self.wb.create_sheet(name[:31])
        n = len(columns)
        for i, c in enumerate(columns, start=1):
            ws.column_dimensions[get_column_letter(i)].width = c.width
        r = 1
        if company is not None:
            ws.cell(r, 1, f"Đơn vị: {company.name}").font = BOLD
            ws.cell(
                r + 1, 1, f"Địa chỉ: {company.address or ''}"
            ).font = NORMAL
            if company.tax_code:
                ws.cell(
                    r + 2, 1, f"Mã số thuế: {company.tax_code}"
                ).font = NORMAL
        if form_code:
            right = max(1, n - 2)
            ws.cell(r, right, f"Mẫu số {form_code}").font = BOLD
            ws.cell(r + 1, right, issued).font = ITALIC
        r = 5
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n)
        cell = ws.cell(r, 1, title)
        cell.font = TITLE
        cell.alignment = Alignment(horizontal="center")
        if subtitle:
            r += 1
            ws.merge_cells(
                start_row=r, start_column=1, end_row=r, end_column=n
            )
            c2 = ws.cell(r, 1, subtitle)
            c2.font = ITALIC
            c2.alignment = Alignment(horizontal="center")
        for line in extra_lines or []:
            r += 1
            ws.cell(r, 1, line).font = NORMAL
        r += 2
        for i, c in enumerate(columns, start=1):
            cell = ws.cell(r, i, c.title)
            cell.font = BOLD
            cell.border = BORDER
            cell.alignment = Alignment(
                horizontal="center", vertical="center", wrap_text=True
            )
        ws.row_dimensions[r].height = 32
        key = id(ws)
        self.header_row[key] = r
        self.first_data_row[key] = r + 1
        self.cols[key] = columns
        ws.freeze_panes = ws.cell(r + 1, 1)
        return ws

    def _write(
        self, ws, row_idx: int, values: list, *, bold: bool = False
    ) -> None:
        cols = self.cols[id(ws)]
        for i, v in enumerate(values, start=1):
            if isinstance(v, datetime):
                v = v.date()
            cell = ws.cell(row_idx, i, v)
            cell.border = BORDER
            cell.font = BOLD if bold else NORMAL
            kind = cols[i - 1].kind if i - 1 < len(cols) else "text"
            if kind == "money":
                cell.number_format = MONEY_FMT
            elif kind == "qty":
                cell.number_format = QTY_FMT
            elif kind == "date" or isinstance(v, date):
                cell.number_format = DATE_FMT
                cell.alignment = Alignment(horizontal="center")
            elif kind == "text":
                cell.alignment = Alignment(wrap_text=True, vertical="top")

    def rows(
        self, ws, rows: list[list], *, bold_rows: set[int] | None = None
    ) -> None:
        start = (
            ws.max_row + 1
            if ws.max_row >= self.first_data_row[id(ws)]
            else self.first_data_row[id(ws)]
        )
        for k, values in enumerate(rows):
            self._write(
                ws, start + k, values, bold=bool(bold_rows and k in bold_rows)
            )

    def row(self, ws, values: list, *, bold: bool = False) -> int:
        idx = (
            ws.max_row + 1
            if ws.max_row >= self.first_data_row[id(ws)]
            else self.first_data_row[id(ws)]
        )
        self._write(ws, idx, values, bold=bold)
        return idx

    def total_row(
        self,
        ws,
        label: str,
        *,
        sum_cols: list[int],
        label_col: int = 1,
        from_row: int | None = None,
    ) -> int:
        """Add a total row using SUM formulas (columns are 0-based)."""
        first = from_row or self.first_data_row[id(ws)]
        last = ws.max_row
        n = len(self.cols[id(ws)])
        values: list = [None] * n
        values[label_col - 1] = label
        idx = last + 1
        for c in sum_cols:
            letter = get_column_letter(c + 1)
            values[c] = (
                f"=SUM({letter}{first}:{letter}{last})" if last >= first else 0
            )
        self._write(ws, idx, values, bold=True)
        return idx

    def signatures(
        self,
        ws,
        company,
        *,
        left: str = "Người ghi sổ",
        middle: str = "Kế toán trưởng",
        right: str = "Người đại diện theo pháp luật",
        place_date: str | None = None,
    ) -> None:
        n = len(self.cols[id(ws)])
        r = ws.max_row + 2
        d = date.today()
        ws.cell(
            r,
            max(1, n - 2),
            place_date or f"Ngày {d.day:02d} tháng {d.month:02d} năm {d.year}",
        ).font = ITALIC
        r += 1
        positions = [1, max(2, n // 2), max(3, n - 2)]
        for col, label in zip(positions, (left, middle, right), strict=True):
            ws.cell(r, col, label).font = BOLD
            ws.cell(
                r + 1,
                col,
                "(Ký, họ tên)" if label != right else "(Ký, họ tên, đóng dấu)",
            ).font = ITALIC
        names = [
            getattr(company, "preparer", ""),
            getattr(company, "chief_accountant", ""),
            getattr(company, "legal_rep", ""),
        ]
        for col, name in zip(positions, names, strict=True):
            if name:
                ws.cell(r + 5, col, name).font = BOLD

    def to_bytes(self) -> bytes:
        return save_workbook(self.wb)


# --------------------------------------------- formula injection guard

# Formulas generated by the app contain only aggregate functions, cell
# references and arithmetic. Any other string starting with "=" (e.g. a
# partner name "=HYPERLINK(...)" taken from an XML invoice, a bank
# statement or the API) is stored as text, not as a formula.
_SAFE_FORMULA = re.compile(
    r"=(?:SUM|ROUND|MAX|MIN|ABS|IF|SUBTOTAL"
    r"|\$?[A-Z]{1,3}\$?\d+|[0-9.+\-*/(),:<> ])+"
)


def is_internal_formula(value) -> bool:
    return isinstance(value, str) and bool(_SAFE_FORMULA.fullmatch(value))


def safe_cell(cell) -> None:
    """Store a cell as text if it starts with "=" but is not internal.

    Only formulas generated by the app itself are kept as formulas.
    """
    v = cell.value
    if isinstance(v, str) and v[:1] == "=" and not is_internal_formula(v):
        cell.data_type = "s"


def sanitize_workbook(wb) -> None:
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                safe_cell(cell)


def save_workbook(wb) -> bytes:
    """Save the workbook to bytes, neutralizing foreign formulas."""
    sanitize_workbook(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


XLSX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def content_disposition(filename: str, inline: bool = False) -> str:
    """Build a safe Content-Disposition header.

    Uses an ASCII fallback name plus a UTF-8 filename*; drops
    control characters, quotes and backslashes.
    """
    from urllib.parse import quote

    name = (
        "".join(
            ch
            for ch in (filename or "file")
            if ch >= " " and ch not in '"\\\x7f'
        ).strip()
        or "file"
    )
    ascii_name = unicodedata.normalize(
        "NFKD", name.replace("đ", "d").replace("Đ", "D")
    )
    ascii_name = "".join(
        ch
        for ch in ascii_name
        if ord(ch) < 128 and not unicodedata.combining(ch)
    )
    ascii_name = (
        re.sub(r"[^A-Za-z0-9._ -]+", "-", ascii_name).strip() or "file"
    )
    disp = "inline" if inline else "attachment"
    return (
        f'{disp}; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(name, safe='')}"
    )


def xlsx_response(data: bytes, filename: str):
    from fastapi.responses import Response

    return Response(
        data,
        media_type=XLSX_MEDIA,
        headers={"Content-Disposition": content_disposition(filename)},
    )


def pdf_response(data: bytes, filename: str, inline: bool = True):
    from fastapi.responses import Response

    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(filename, inline=inline)
        },
    )
