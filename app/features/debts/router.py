"""Receivables / payables pages.

Summary, aging, per-partner detail ledger and reconciliation report.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.dates import parse_date, today
from app.core.errors import PermissionDeniedError
from app.core.pdf import render_pdf
from app.core.security import CurrentUser, require
from app.core.web import get_tenant_db, render
from app.core.xlsx import Col, ReportBook, pdf_response, xlsx_response
from app.features.debts import service as ds
from app.features.org.models import Company
from app.features.partners.service import get_partner
from app.features.sales.common import resolve_view_period

router = APIRouter()
PATHS = {"receivables": "receivable", "payables": "payable"}
LABELS = {
    "receivable": (
        "Công nợ phải thu",
        "Tổng hợp công nợ phải thu khách hàng (TK 131)",
    ),
    "payable": (
        "Công nợ phải trả",
        "Tổng hợp công nợ phải trả người bán (TK 331)",
    ),
}


def _period(request: Request):
    return resolve_view_period(request.query_params, default_mode="year")


def _as_of(request: Request) -> date:
    try:
        return parse_date(request.query_params.get("as_of")) or today()
    except ValueError:
        return today()


def _register(path: str, kind: str) -> None:
    title, subtitle = LABELS[kind]

    @router.get(f"/debts/{path}", name=f"debts_{path}")
    def summary_page(
        request: Request,
        q: str = "",
        user: CurrentUser = Depends(require("debts.view")),
        db: Session = Depends(get_tenant_db),
    ):
        period = _period(request)
        sm = ds.summary(
            db, user.company_id, kind, period.start, period.end, q=q
        )
        return render(
            request,
            "debts/summary.html",
            {
                "kind": kind,
                "path": path,
                "title": title,
                "subtitle": subtitle,
                "sm": sm,
                "period": period,
                "q": q,
                "info": ds.KINDS[kind],
                "breadcrumbs": [(title, None)],
            },
        )

    @router.get(f"/debts/{path}.xlsx", name=f"debts_{path}_xlsx")
    def summary_xlsx(
        request: Request,
        q: str = "",
        user: CurrentUser = Depends(require("debts.export")),
        db: Session = Depends(get_tenant_db),
    ):
        period = _period(request)
        sm = ds.summary(
            db, user.company_id, kind, period.start, period.end, q=q
        )
        company = db.get(Company, user.company_id)
        book = ReportBook()
        ws = book.add_sheet(
            "Tổng hợp",
            company=company,
            title=subtitle.upper(),
            subtitle=period.label,
            columns=[
                Col("Mã", 12),
                Col(ds.KINDS[kind]["partner"], 36),
                Col("MST", 14),
                Col("Đầu kỳ Nợ", 15, "money"),
                Col("Đầu kỳ Có", 15, "money"),
                Col("Phát sinh Nợ", 15, "money"),
                Col("Phát sinh Có", 15, "money"),
                Col("Cuối kỳ Nợ", 15, "money"),
                Col("Cuối kỳ Có", 15, "money"),
            ],
        )
        for r in sm.rows:
            p = r.partner
            book.row(
                ws,
                [
                    p.code if p else "",
                    p.name if p else "(không đối tượng)",
                    p.tax_code if p else "",
                    *r.opening_dc,
                    r.debit,
                    r.credit,
                    *r.closing_dc,
                ],
            )
        book.total_row(
            ws, "Tổng cộng", sum_cols=[3, 4, 5, 6, 7, 8], label_col=2
        )
        book.signatures(ws, company)
        return xlsx_response(
            book.to_bytes(),
            f"cong-no-{path}-{period.start:%Y%m%d}-{period.end:%Y%m%d}.xlsx",
        )

    @router.get(f"/debts/{path}/aging", name=f"debts_{path}_aging")
    def aging_page(
        request: Request,
        user: CurrentUser = Depends(require("debts.view")),
        db: Session = Depends(get_tenant_db),
    ):
        as_of = _as_of(request)
        rows = ds.aging(db, user.company_id, kind, as_of)
        return render(
            request,
            "debts/aging.html",
            {
                "kind": kind,
                "path": path,
                "title": title,
                "rows": rows,
                "totals": ds.aging_totals(rows),
                "as_of": as_of,
                "buckets": ds.BUCKETS,
                "info": ds.KINDS[kind],
                "breadcrumbs": [(title, f"/debts/{path}"), ("Tuổi nợ", None)],
            },
        )

    @router.get(f"/debts/{path}/aging.xlsx", name=f"debts_{path}_aging_xlsx")
    def aging_xlsx(
        request: Request,
        user: CurrentUser = Depends(require("debts.export")),
        db: Session = Depends(get_tenant_db),
    ):
        as_of = _as_of(request)
        rows = ds.aging(db, user.company_id, kind, as_of)
        company = db.get(Company, user.company_id)
        book = ReportBook()
        ws = book.add_sheet(
            "Tuổi nợ",
            company=company,
            title=f"BÁO CÁO TUỔI NỢ {title.upper()}",
            subtitle=f"Đến ngày {as_of:%d/%m/%Y}",
            columns=[
                Col("Mã", 12),
                Col(ds.KINDS[kind]["partner"], 36),
                Col("Còn nợ", 16, "money"),
                *[Col(label, 15, "money") for _k, label in ds.BUCKETS],
                Col("Trả trước", 15, "money"),
            ],
        )
        for r in rows:
            p = r.partner
            book.row(
                ws,
                [
                    p.code if p else "",
                    p.name if p else "(không đối tượng)",
                    r.balance,
                    *[r.buckets.get(k, 0) for k, _label in ds.BUCKETS],
                    r.buckets.get("prepaid", 0),
                ],
            )
        book.total_row(
            ws,
            "Tổng cộng",
            sum_cols=list(range(2, 3 + len(ds.BUCKETS) + 1)),
            label_col=2,
        )
        return xlsx_response(
            book.to_bytes(), f"tuoi-no-{path}-{as_of:%Y%m%d}.xlsx"
        )


for _path, _kind in PATHS.items():
    _register(_path, _kind)


def _partner_context(
    db: Session,
    user: CurrentUser,
    partner_id: int,
    request: Request,
    kind: str | None,
):
    p = get_partner(db, user.company_id, partner_id)
    if p.is_employee and not user.can("payroll.view"):
        raise PermissionDeniedError(
            "Cần quyền xem lương để xem công nợ của nhân viên"
        )
    period = _period(request)
    if kind not in ds.KINDS:
        kind = (
            "payable" if p.is_supplier and not p.is_customer else "receivable"
        )
    led = ds.partner_ledger(
        db, user.company_id, kind, p.id, period.start, period.end
    )
    return p, period, kind, led


@router.get("/debts/partner/{partner_id:int}")
def partner_page(
    request: Request,
    partner_id: int,
    kind: str | None = None,
    user: CurrentUser = Depends(require("debts.view")),
    db: Session = Depends(get_tenant_db),
):
    p, period, kind, led = _partner_context(
        db, user, partner_id, request, kind
    )
    open_items = ds.aging(db, user.company_id, kind, today(), partner_id=p.id)
    return render(
        request,
        "debts/partner.html",
        {
            "p": p,
            "period": period,
            "kind": kind,
            "led": led,
            "info": ds.KINDS[kind],
            "open": open_items[0] if open_items else None,
            "as_of": today(),
            "breadcrumbs": [
                (
                    LABELS[kind][0],
                    "/debts/receivables"
                    if kind == "receivable"
                    else "/debts/payables",
                ),
                (p.name, None),
            ],
        },
    )


@router.get("/debts/partner/{partner_id:int}/ledger.xlsx")
def partner_ledger_xlsx(
    request: Request,
    partner_id: int,
    kind: str | None = None,
    user: CurrentUser = Depends(require("debts.export")),
    db: Session = Depends(get_tenant_db),
):
    p, period, kind, led = _partner_context(
        db, user, partner_id, request, kind
    )
    company = db.get(Company, user.company_id)
    title = "SỔ CHI TIẾT THANH TOÁN VỚI " + (
        "NGƯỜI MUA" if kind == "receivable" else "NGƯỜI BÁN"
    )
    book = ReportBook()
    ws = book.add_sheet(
        "Sổ chi tiết",
        company=company,
        title=title,
        subtitle=(
            f"Tài khoản {ds.KINDS[kind]['account']} – "
            f"Đối tượng: {p.code} – {p.name} – {period.label}"
        ),
        columns=[
            Col("Ngày ghi sổ", 12, "date"),
            Col("Số chứng từ", 16),
            Col("Ngày chứng từ", 12, "date"),
            Col("Diễn giải", 40),
            Col("TK đối ứng", 10),
            Col("Phát sinh Nợ", 15, "money"),
            Col("Phát sinh Có", 15, "money"),
            Col("Số dư Nợ", 15, "money"),
            Col("Số dư Có", 15, "money"),
        ],
    )
    od, oc = ds.split(led.opening)
    book.row(
        ws,
        [None, None, None, "Số dư đầu kỳ", None, None, None, od, oc],
        bold=True,
    )
    for r in led.rows:
        bd, bc = ds.split(r.balance)
        book.row(
            ws,
            [
                r.date,
                r.number,
                r.doc_date,
                r.description,
                r.contra,
                r.debit,
                r.credit,
                bd,
                bc,
            ],
        )
    cd, cc = ds.split(led.closing)
    book.row(
        ws,
        [
            None,
            None,
            None,
            "Cộng phát sinh / số dư cuối kỳ",
            None,
            led.total_debit,
            led.total_credit,
            cd,
            cc,
        ],
        bold=True,
    )
    book.signatures(ws, company)
    return xlsx_response(book.to_bytes(), f"so-chi-tiet-{p.code}.xlsx")


@router.get("/debts/partner/{partner_id:int}/reconciliation.pdf")
def partner_reconciliation(
    request: Request,
    partner_id: int,
    kind: str | None = None,
    user: CurrentUser = Depends(require("debts.export")),
    db: Session = Depends(get_tenant_db),
):
    p, period, kind, led = _partner_context(
        db, user, partner_id, request, kind
    )
    company = db.get(Company, user.company_id)
    pdf = render_pdf(
        "debts/print/reconciliation.html",
        {
            "company": company,
            "p": p,
            "period": period,
            "kind": kind,
            "led": led,
            "split": ds.split,
            "today": today(),
        },
    )
    return pdf_response(pdf, f"doi-chieu-cong-no-{p.code}.pdf")
