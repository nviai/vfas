"""Tax pages.

VAT invoice lists, the 01/GTGT return (web, PDF, Excel, XML), the CIT
worksheet and the filing calendar.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.dates import parse_date, today
from app.core.errors import BusinessError, NotFoundError
from app.core.pdf import render_pdf
from app.core.security import CurrentUser, require
from app.core.web import form_data, get_ctx, get_tenant_db, redirect, render
from app.core.xlsx import (
    Col,
    ReportBook,
    content_disposition,
    pdf_response,
    xlsx_response,
    xml_response,
)
from app.features.org.models import Company
from app.features.sales.common import VAT_LABELS, parse_money
from app.features.vat import calendar, cit, htkk, htkk_cit, khbs
from app.features.vat import service as vs
from app.features.vat.models import FORM_VAT

router = APIRouter()

LIST_TITLES = {
    "in": "Bảng kê hóa đơn, chứng từ hàng hóa, dịch vụ mua vào",
    "out": "Bảng kê hóa đơn, chứng từ hàng hóa, dịch vụ bán ra",
}


def _company(db: Session, user: CurrentUser) -> Company:
    return db.get(Company, user.company_id)


def _period_from_query(company: Company, params) -> vs.TaxPeriod:
    if params.get("year") and params.get("period"):
        try:
            return vs.parse_period(
                company,
                params.get("period_type"),
                params.get("year"),
                params.get("period"),
            )
        except BusinessError:
            pass
    # Default: the most recently ended period
    return vs.period_of(company, today()).previous()


def _period_options(
    company: Company, current: vs.TaxPeriod
) -> list[tuple[str, str]]:
    out = []
    p = vs.period_of(company, today())
    for _ in range(12 if company.vat_period == "quarter" else 18):
        out.append((f"{p.period_type}/{p.year}/{p.period}", p.label))
        p = p.previous()
    key = f"{current.period_type}/{current.year}/{current.period}"
    if key not in {k for k, _ in out}:
        out.insert(0, (key, current.label))
    return out


# -------------------------------------------------------- invoice lists


@router.get("/vat/lists")
def vat_lists(
    request: Request,
    direction: str = "out",
    user: CurrentUser = Depends(require("vat.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    period = _period_from_query(company, request.query_params)
    direction = direction if direction in ("in", "out") else "out"
    data = vs.vat_list(db, company.id, period.start, period.end, direction)
    return render(
        request,
        "vat/lists.html",
        {
            "period": period,
            "direction": direction,
            "data": data,
            "title": LIST_TITLES[direction],
            "vat_labels": VAT_LABELS,
            "options": _period_options(company, period),
            "breadcrumbs": [("Thuế", None), ("Bảng kê thuế GTGT", None)],
        },
    )


@router.get("/vat/lists.xlsx")
def vat_lists_xlsx(
    request: Request,
    direction: str = "out",
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    period = _period_from_query(company, request.query_params)
    direction = direction if direction in ("in", "out") else "out"
    data = vs.vat_list(db, company.id, period.start, period.end, direction)
    book = ReportBook()
    who = "người bán" if direction == "in" else "người mua"
    ws = book.add_sheet(
        "Bảng kê",
        company=company,
        title=LIST_TITLES[direction].upper(),
        subtitle=f"Kỳ tính thuế: {period.label}",
        columns=[
            Col("STT", 6, "int"),
            Col("Ký hiệu", 12),
            Col("Số hóa đơn", 14),
            Col("Ngày hóa đơn", 13, "date"),
            Col(f"Tên {who}", 36),
            Col(f"MST {who}", 15),
            Col("Giá trị chưa thuế", 17, "money"),
            Col("Thuế suất", 10),
            Col("Thuế GTGT", 15, "money"),
            Col("Số chứng từ", 15),
            Col("Ghi chú", 18),
        ],
    )
    for n, (rec, entry, sign) in enumerate(data.rows, start=1):
        note = (
            "Điều chỉnh giảm"
            if sign < 0
            else (
                "Không được khấu trừ"
                if direction == "in" and not rec.deductible
                else ""
            )
        )
        book.row(
            ws,
            [
                n,
                rec.invoice_series,
                rec.invoice_no,
                rec.invoice_date,
                rec.partner_name,
                rec.partner_tax_code,
                sign * rec.goods_amount,
                VAT_LABELS.get(rec.vat_rate, rec.vat_rate),
                sign * rec.vat_amount,
                entry.number,
                note,
            ],
        )
    book.total_row(ws, "Tổng cộng", sum_cols=[6, 8], label_col=5)
    book.signatures(ws, company)
    return xlsx_response(
        book.to_bytes(),
        f"bang-ke-{direction}-{period.year}-{period.period}.xlsx",
    )


@router.get("/vat/lists.pdf")
def vat_lists_pdf(
    request: Request,
    direction: str = "out",
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    period = _period_from_query(company, request.query_params)
    direction = direction if direction in ("in", "out") else "out"
    data = vs.vat_list(db, company.id, period.start, period.end, direction)
    pdf = render_pdf(
        "vat/print/vat_list.html",
        {
            "company": company,
            "period": period,
            "data": data,
            "direction": direction,
            "title": LIST_TITLES[direction],
            "vat_labels": VAT_LABELS,
            "today": today(),
        },
    )
    return pdf_response(pdf, f"bang-ke-{direction}.pdf")


# ------------------------------------------------------- 01/GTGT return


@router.get("/vat/declarations")
def declarations(
    request: Request,
    user: CurrentUser = Depends(require("vat.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    saved, latest = {}, {}
    for d in vs.list_declarations(db, company.id):
        key = (d.period_type, d.year, d.period)
        if d.version == 0:
            saved[key] = d
        if key not in latest or d.version > latest[key].version:
            latest[key] = d
    rows = []
    p = vs.period_of(company, today())
    for _ in range(8 if company.vat_period == "quarter" else 12):
        d = saved.pop((p.period_type, p.year, p.period), None)
        rows.append({"period": p, "decl": d, "ended": p.end < today()})
        p = p.previous()
    for (pt, y, n), d in sorted(saved.items(), reverse=True):
        rows.append(
            {"period": vs.TaxPeriod(pt, y, n), "decl": d, "ended": True}
        )
    for r in rows:
        p = r["period"]
        r["latest"] = latest.get((p.period_type, p.year, p.period))
    return render(
        request,
        "vat/declarations.html",
        {
            "rows": rows,
            "company": company,
            "breadcrumbs": [("Thuế", None), ("Tờ khai 01/GTGT", None)],
        },
    )


def _decl_context(db: Session, company: Company, period: vs.TaxPeriod) -> dict:
    decl = vs.get_declaration(db, company.id, period)
    values = vs.build(db, company.id, period, decl)
    deductible, output, offset = vs.offset_amounts(db, company.id, period.end)
    final = bool(decl and decl.status != "draft")
    supplements = vs.supplements(db, company.id, period) if final else []
    if supplements:
        # Show what was filed last.
        latest = vs.get_declaration(
            db, company.id, period, supplements[-1].version
        )
        values = dict(latest.values)
    return {
        "period": period,
        "decl": decl,
        "supplements": supplements,
        "pending": vs.pending_changes(db, company.id, period) if final else [],
        "khbs_perm": "vat.manage",
        "export_perm": "vat.export",
        "today": today(),
        "v": values,
        "labels": vs.LABELS,
        "final": final,
        "offset": {
            "deductible": deductible,
            "output": output,
            "amount": offset,
        },
        "company": company,
    }


@router.get("/vat/declarations/{period_type}/{year:int}/{period:int}")
def declaration(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    user: CurrentUser = Depends(require("vat.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p = vs.parse_period(company, period_type, year, period)
    ctx = _decl_context(db, company, p)
    ctx["breadcrumbs"] = [
        ("Thuế", None),
        ("Tờ khai 01/GTGT", "/vat/declarations"),
        (p.label, None),
    ]
    return render(request, "vat/declaration.html", ctx)


@router.post("/vat/declarations/{period_type}/{year:int}/{period:int}")
def declaration_save(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("vat.manage")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p = vs.parse_period(company, period_type, year, period)
    url = f"/vat/declarations/{p.period_type}/{p.year}/{p.period}"
    try:
        manual = {k: parse_money(form.get(f"ct{k}")) for k in vs.MANUAL_FIELDS}
        carried = (
            parse_money(form.get("ct22"))
            if form.get("override22") == "1"
            else None
        )
    except ValueError as e:
        return redirect(url, str(e), "error", request=request)
    finalize = form.get("action") == "final"
    vs.save(
        db,
        get_ctx(request),
        p,
        manual=manual,
        carried_override=carried,
        finalize=finalize,
    )
    return redirect(
        url,
        f"Đã chốt tờ khai {p.label}" if finalize else "Đã lưu tờ khai",
        request=request,
    )


@router.post("/vat/declarations/{period_type}/{year:int}/{period:int}/reopen")
def declaration_reopen(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    user: CurrentUser = Depends(require("vat.manage")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p = vs.parse_period(company, period_type, year, period)
    vs.reopen(db, get_ctx(request), p)
    return redirect(
        f"/vat/declarations/{p.period_type}/{p.year}/{p.period}",
        f"Đã mở lại tờ khai {p.label}",
        "warning",
        request=request,
    )


@router.post("/vat/declarations/{period_type}/{year:int}/{period:int}/offset")
def declaration_offset(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    user: CurrentUser = Depends(require("vat.manage")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p = vs.parse_period(company, period_type, year, period)
    ctx = get_ctx(request)
    if not ctx.can("journal.create"):
        raise BusinessError("Cần quyền lập phiếu kế toán")
    e = vs.create_offset_entry(db, ctx, p)
    return redirect(
        e.url,
        "Đã lập phiếu kế toán khấu trừ thuế (nháp). Kiểm tra rồi ghi sổ.",
        request=request,
    )


@router.post(
    "/vat/declarations/{period_type}/{year:int}/{period:int}/supplements"
)
def declaration_supplement(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("vat.manage")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p = vs.parse_period(company, period_type, year, period)
    try:
        until = parse_date(form.get("until")) or today()
    except ValueError as e:
        raise BusinessError("Ngày không hợp lệ") from e
    decl = vs.supplement(
        db, get_ctx(request), p, reason=form.get("reason") or "", until=until
    )
    return redirect(
        f"/vat/declarations/{p.period_type}/{p.year}/{p.period}",
        f"Đã lập tờ khai bổ sung lần {decl.version}",
        request=request,
    )


@router.post(
    "/vat/declarations/{period_type}/{year:int}/{period:int}"
    "/supplements/{version:int}/delete"
)
def declaration_supplement_delete(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    version: int,
    user: CurrentUser = Depends(require("vat.manage")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p = vs.parse_period(company, period_type, year, period)
    khbs.delete_latest(db, get_ctx(request), FORM_VAT, p, version)
    return redirect(
        f"/vat/declarations/{p.period_type}/{p.year}/{p.period}",
        f"Đã xóa tờ khai bổ sung lần {version}",
        "warning",
        request=request,
    )


def _decl_values(
    db: Session,
    company: Company,
    period_type: str,
    year: int,
    period: int,
    version: int = 0,
):
    """(period, figures, declaration, supplement summary or None)."""
    p = vs.parse_period(company, period_type, year, period)
    if version:
        decl = vs.get_declaration(db, company.id, p, version)
        if decl is None:
            raise NotFoundError("Không tìm thấy tờ khai bổ sung")
        sup = khbs.summary_of(
            db,
            company.id,
            FORM_VAT,
            p,
            version,
            vs.KHBS_KEYS,
            vs.LABELS,
            vs.deadline(p),
        )
        return p, dict(decl.values), decl, sup
    decl = vs.get_declaration(db, company.id, p)
    return p, vs.build(db, company.id, p, decl), decl, None


@router.get("/vat/declarations/{period_type}/{year:int}/{period:int}.xml")
def declaration_xml(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    version: int = 0,
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p, v, _decl, sup = _decl_values(
        db, company, period_type, year, period, version
    )
    data = htkk.build_xml(
        company,
        p,
        v,
        version=version,
        appendices=khbs.appendices_xml(company, htkk.FORM, sup) if sup else "",
    )
    name = (
        f"01GTGT-{company.tax_code or 'mst'}-{p.year}-"
        f"{p.period_type[0].upper()}{p.period:02d}"
        + (f"-BS{version}" if version else "")
        + ".xml"
    )
    return Response(
        data,
        media_type="application/xml",
        headers={"Content-Disposition": content_disposition(name)},
    )


@router.get("/vat/declarations/{period_type}/{year:int}/{period:int}.pdf")
def declaration_pdf(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    version: int = 0,
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p, v, decl, sup = _decl_values(
        db, company, period_type, year, period, version
    )
    pdf = render_pdf(
        "vat/print/gtgt01.html",
        {
            "company": company,
            "period": p,
            "v": v,
            "labels": vs.LABELS,
            "decl": decl,
            "sup": sup,
            "form_name": "Tờ khai thuế GTGT (01/GTGT)",
            "today": today(),
        },
    )
    return pdf_response(
        pdf,
        f"to-khai-01GTGT-{p.year}-{p.period}"
        + (f"-BS{version}" if version else "")
        + ".pdf",
    )


@router.get("/vat/declarations/{period_type}/{year:int}/{period:int}.xlsx")
def declaration_xlsx(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p, v, _decl, _sup = _decl_values(db, company, period_type, year, period)
    book = ReportBook()
    ws = book.add_sheet(
        "01-GTGT",
        company=company,
        form_code="01/GTGT",
        issued=(
            "(Ban hành kèm theo Thông tư số 80/2021/TT-BTC "
            "ngày 29/9/2021 của Bộ Tài chính)"
        ),
        title="TỜ KHAI THUẾ GIÁ TRỊ GIA TĂNG",
        subtitle=f"[01] Kỳ tính thuế: {p.label}",
        columns=[
            Col("Chỉ tiêu", 70),
            Col("Mã", 8),
            Col("Giá trị HHDV", 18, "money"),
            Col("Thuế GTGT", 18, "money"),
        ],
    )
    for row in GTGT_ROWS:
        label, code_v, code_t = row
        book.row(
            ws,
            [
                label,
                "/".join(c for c in (code_v, code_t) if c),
                v.get(code_v) if code_v else None,
                v.get(code_t) if code_t else None,
            ],
        )
    book.signatures(
        ws,
        company,
        left="",
        middle="",
        right="Người nộp thuế hoặc đại diện hợp pháp của người nộp thuế",
    )
    return xlsx_response(
        book.to_bytes(), f"to-khai-01GTGT-{p.year}-{p.period}.xlsx"
    )


@router.get(
    "/vat/declarations/{period_type}/{year:int}/{period:int}/reduction.pdf"
)
def reduction_pdf(
    request: Request,
    period_type: str,
    year: int,
    period: int,
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    p, v, _decl, _sup = _decl_values(db, company, period_type, year, period)
    pdf = render_pdf(
        "vat/print/reduction.html",
        {
            "company": company,
            "period": p,
            "rows": v.get("_reduced", []),
            "today": today(),
        },
    )
    return pdf_response(pdf, f"phu-luc-giam-thue-{p.year}-{p.period}.pdf")


# (label, value column code, tax column code), in the order of the
# return form.
GTGT_ROWS = [
    ('Không phát sinh hoạt động mua, bán trong kỳ (đánh dấu "X")', "21", None),
    ("Thuế GTGT còn được khấu trừ kỳ trước chuyển sang", None, "22"),
    ("I. Hàng hóa, dịch vụ mua vào trong kỳ", None, None),
    ("1. Giá trị và thuế GTGT của hàng hóa, dịch vụ mua vào", "23", "24"),
    ("   Trong đó: hàng hóa, dịch vụ nhập khẩu", "23a", "24a"),
    (
        "2. Thuế GTGT của hàng hóa, dịch vụ mua vào được khấu trừ kỳ này",
        None,
        "25",
    ),
    ("II. Hàng hóa, dịch vụ bán ra trong kỳ", None, None),
    ("1. Hàng hóa, dịch vụ bán ra không chịu thuế GTGT", "26", None),
    ("2. Hàng hóa, dịch vụ bán ra chịu thuế GTGT", "27", "28"),
    ("   a. Hàng hóa, dịch vụ bán ra chịu thuế suất 0%", "29", None),
    ("   b. Hàng hóa, dịch vụ bán ra chịu thuế suất 5%", "30", "31"),
    ("   c. Hàng hóa, dịch vụ bán ra chịu thuế suất 10%", "32", "33"),
    (
        (
            "   d. Hàng hóa, dịch vụ bán ra không phải kê khai, "
            "tính nộp thuế GTGT"
        ),
        "32a",
        None,
    ),
    (
        "3. Tổng doanh thu và thuế GTGT của hàng hóa, dịch vụ bán ra",
        "34",
        "35",
    ),
    ("III. Thuế GTGT phát sinh trong kỳ", None, "36"),
    (
        (
            "IV. Điều chỉnh tăng, giảm thuế GTGT còn được khấu trừ "
            "của các kỳ trước"
        ),
        None,
        None,
    ),
    ("1. Điều chỉnh giảm", None, "37"),
    ("2. Điều chỉnh tăng", None, "38"),
    ("V. Thuế GTGT nhận bàn giao được khấu trừ trong kỳ", None, "39a"),
    ("VI. Xác định nghĩa vụ thuế GTGT phải nộp trong kỳ", None, None),
    (
        "1. Thuế GTGT phải nộp của hoạt động sản xuất kinh doanh trong kỳ",
        None,
        "40a",
    ),
    (
        (
            "2. Thuế GTGT mua vào của dự án đầu tư được bù trừ với "
            "thuế GTGT phải nộp"
        ),
        None,
        "40b",
    ),
    ("3. Thuế GTGT còn phải nộp trong kỳ", None, "40"),
    ("4. Thuế GTGT chưa khấu trừ hết kỳ này", None, "41"),
    ("4.1. Thuế GTGT đề nghị hoàn", None, "42"),
    ("4.2. Thuế GTGT còn được khấu trừ chuyển kỳ sau", None, "43"),
]


# ------------------------------------------------------------------ CIT


@router.get("/vat/cit")
def cit_page(
    request: Request,
    year: int | None = None,
    user: CurrentUser = Depends(require("vat.view")),
    db: Session = Depends(get_tenant_db),
):
    year = year or (today().year - 1 if today().month <= 3 else today().year)
    values, decl = cit.build(db, user.company_id, year)
    return render(
        request,
        "vat/cit.html",
        {
            "year": year,
            "v": values,
            "decl": decl,
            "labels": cit.LABELS,
            "years": list(range(today().year, today().year - 6, -1)),
            "final": bool(decl and decl.status != "draft"),
            "breadcrumbs": [("Thuế", None), ("Quyết toán TNDN", None)],
        },
    )


@router.post("/vat/cit")
def cit_save(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("vat.manage")),
    db: Session = Depends(get_tenant_db),
):
    try:
        year = int(form.get("year") or 0)
        manual = {k: parse_money(form.get(k)) for k in cit.MANUAL}
    except ValueError as e:
        return redirect("/vat/cit", str(e), "error", request=request)
    rate = int(form["rate"]) if (form.get("rate") or "").isdigit() else None
    finalize = form.get("action") == "final"
    try:
        losses = _loss_rows(form)
    except ValueError as e:
        return redirect(
            f"/vat/cit?year={year}", str(e), "error", request=request
        )
    cit.save(
        db,
        get_ctx(request),
        year,
        manual,
        rate,
        finalize=finalize,
        losses=losses,
    )
    return redirect(
        f"/vat/cit?year={year}",
        "Đã chốt bảng tính thuế TNDN" if finalize else "Đã lưu",
        request=request,
    )


def _loss_rows(form: dict) -> list[dict] | None:
    """Rows of the 03-2/TNDN table; None when the table was not
    submitted (the losses then come from the history).
    """
    if form.get("losses_form") != "1":
        return None
    rows = []
    for i in range(cit.LOSS_YEARS):
        y = (form.get(f"loss_year_{i}") or "").strip()
        loss = parse_money(form.get(f"loss_amount_{i}"))
        if not y and not loss:
            continue
        if not y.isdigit():
            raise ValueError("Năm phát sinh lỗ không hợp lệ")
        rows.append(
            {
                "year": int(y),
                "loss": loss,
                "before": parse_money(form.get(f"loss_before_{i}")),
            }
        )
    return rows


@router.post("/vat/cit/reopen")
def cit_reopen(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("vat.manage")),
    db: Session = Depends(get_tenant_db),
):
    year = int(form.get("year") or 0)
    cit.reopen(db, get_ctx(request), year)
    return redirect(
        f"/vat/cit?year={year}", "Đã mở lại", "warning", request=request
    )


@router.get("/vat/cit.pdf")
def cit_pdf(
    request: Request,
    year: int,
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    values, decl = cit.build(db, user.company_id, year)
    pdf = render_pdf(
        "vat/print/cit.html",
        {
            "company": company,
            "year": year,
            "v": values,
            "labels": cit.LABELS,
            "today": today(),
        },
    )
    return pdf_response(pdf, f"thue-tndn-{year}.pdf")


@router.get("/vat/cit.xml")
def cit_xml(
    request: Request,
    year: int,
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    values, _decl = cit.build(db, user.company_id, year)
    data = htkk_cit.build_xml(db, company, year, values)
    return xml_response(data, f"03TNDN-{company.tax_code or 'mst'}-{year}.xml")


@router.get("/vat/cit.xlsx")
def cit_xlsx(
    request: Request,
    year: int,
    user: CurrentUser = Depends(require("vat.export")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    values, _decl = cit.build(db, user.company_id, year)
    book = ReportBook()
    ws = book.add_sheet(
        "TNDN",
        company=company,
        title="BẢNG TÍNH THUẾ THU NHẬP DOANH NGHIỆP",
        subtitle=(
            f"Kỳ tính thuế: Năm {year} (tham khảo chỉ tiêu tờ khai 03/TNDN)"
        ),
        columns=[
            Col("Mã", 8),
            Col("Chỉ tiêu", 70),
            Col("Số tiền", 20, "money"),
        ],
    )
    for code, label in cit.LABELS.items():
        val = values.get(code)
        book.row(ws, [code, label, f"{val}%" if code == "C7" else val])
    return xlsx_response(book.to_bytes(), f"thue-tndn-{year}.xlsx")


# ------------------------------------------------------ filing calendar


@router.get("/vat/calendar")
def calendar_page(
    request: Request,
    user: CurrentUser = Depends(require("vat.view")),
    db: Session = Depends(get_tenant_db),
):
    company = _company(db, user)
    items = calendar.deadlines(company, today(), db, window=200)
    return render(
        request,
        "vat/calendar.html",
        {
            "items": items,
            "company": company,
            "breadcrumbs": [("Thuế", None), ("Lịch nộp thuế", None)],
        },
    )
