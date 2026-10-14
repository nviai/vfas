"""Fixed assets, depreciation, tools / prepaid expenses and allocation.

Registers, asset cards and run sheets as web pages, PDF and Excel.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dates import fmt_date, parse_date, today
from app.core.errors import BusinessError
from app.core.money import MoneyError, parse_vnd
from app.core.pdf import render_pdf
from app.core.regimes import account as regime_account
from app.core.security import CurrentUser, require
from app.core.web import form_data, get_ctx, get_tenant_db, redirect, render
from app.core.xlsx import Col, ReportBook, pdf_response, xlsx_response
from app.features.accounts.service import list_accounts
from app.features.assets import service
from app.features.assets.models import (
    ASSET_CATEGORIES,
    DISPOSED,
    IN_USE,
    PREPAID_KINDS,
    RUN_ALLOCATION,
    RUN_DEPRECIATION,
    AssetRun,
    FixedAsset,
)
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.org.models import Company
from app.features.partners.models import Partner

router = APIRouter()

RUN_PAGES = {
    RUN_DEPRECIATION: {
        "url": "/assets/depreciation",
        "title": "Trích khấu hao TSCĐ",
        "doc": "KH",
        "sheet_title": "BẢNG TÍNH VÀ PHÂN BỔ KHẤU HAO TSCĐ",
        "form_code": "06-TSCĐ",
        "item_label": "Tài sản",
        "parent": ("Tài sản cố định", "/assets"),
    },
    RUN_ALLOCATION: {
        "url": "/prepaid/allocation",
        "title": "Phân bổ CCDC, chi phí trả trước",
        "doc": "PB",
        "sheet_title": "BẢNG PHÂN BỔ CÔNG CỤ DỤNG CỤ, CHI PHÍ TRẢ TRƯỚC",
        "form_code": None,
        "item_label": "CCDC / khoản chi phí",
        "parent": ("CCDC & chi phí trả trước", "/prepaid"),
    },
}


# --------------------------------------------------------- form helpers


def _money(form: dict, name: str, default: int = 0) -> int:
    raw = form.get(name)
    if raw in (None, ""):
        return default
    try:
        v = parse_vnd(raw)
    except MoneyError as e:
        raise BusinessError(str(e)) from e
    if v < 0:
        raise BusinessError("Số tiền không được âm")
    return v


def _int(form: dict, name: str, default: int = 0) -> int:
    raw = (form.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise BusinessError(f"Số không hợp lệ: {raw}") from e


def _date(form: dict, name: str, default: date | None = None) -> date | None:
    try:
        return parse_date(form.get(name)) or default
    except ValueError as e:
        raise BusinessError("Ngày không hợp lệ") from e


def _ym(year: int | None, month: int | None) -> tuple[int, int]:
    d = today()
    y = year or d.year
    m = month or d.month
    if not (1 <= m <= 12):
        raise BusinessError("Tháng không hợp lệ")
    return y, m


def _company(db: Session, user: CurrentUser) -> Company:
    return db.get(Company, user.company_id)


def _asset_accounts(db: Session, company_id: int) -> list[tuple[str, str]]:
    return [
        (a.code, a.label)
        for a in list_accounts(
            db, company_id, postable=True, prefix="211,212,213", active=True
        )
    ]


def _expense_accounts(db: Session, company_id: int) -> list[tuple[str, str]]:
    return [
        (a.code, a.label)
        for a in list_accounts(
            db,
            company_id,
            postable=True,
            prefix=",".join(service.expense_prefixes(db, company_id)),
            active=True,
        )
    ]


# ----------------------------------------- fixed assets: list, register


@router.get("/assets")
def assets_page(
    request: Request,
    status: str = "in_use",
    q: str = "",
    on: str = "",
    user: CurrentUser = Depends(require("assets.view")),
    db: Session = Depends(get_tenant_db),
):
    d = parse_date(on) if on else today()
    rows = service.register(db, user.company_id, d, include_disposed=True)
    if status in (IN_USE, DISPOSED):
        rows = [r for r in rows if r.asset.status == status]
    if q:
        ql = q.strip().lower()
        rows = [
            r
            for r in rows
            if ql in r.asset.name.lower() or ql in r.asset.code.lower()
        ]
    future = [
        a
        for a in service.list_assets(db, user.company_id)
        if a.acquired_date > d
    ]
    return render(
        request,
        "assets/index.html",
        {
            "rows": rows,
            "status": status,
            "q": q,
            "on": d,
            "future": future,
            "total_cost": sum(r.cost for r in rows),
            "total_acc": sum(r.accumulated for r in rows),
            "monthly": {
                r.asset.id: service.monthly_rate(r.asset) for r in rows
            },
            "breadcrumbs": [("Tài sản", None), ("Tài sản cố định", None)],
        },
    )


def _register_ctx(db: Session, user: CurrentUser, on: str) -> dict:
    d = parse_date(on) if on else today()
    rows = service.register(db, user.company_id, d)
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.asset.category, []).append(r)
    return {
        "company": _company(db, user),
        "on": d,
        "rows": rows,
        "groups": [(ASSET_CATEGORIES[k], v) for k, v in groups.items()],
        "total_cost": sum(r.cost for r in rows),
        "total_acc": sum(r.accumulated for r in rows),
    }


@router.get("/assets/register.pdf")
def register_pdf(
    on: str = "",
    user: CurrentUser = Depends(require("assets.export")),
    db: Session = Depends(get_tenant_db),
):
    ctx = _register_ctx(db, user, on)
    return pdf_response(
        render_pdf("assets/print/register.html", ctx),
        f"so-tscd-{ctx['on']:%Y%m%d}.pdf",
    )


@router.get("/assets/register.xlsx")
def register_xlsx(
    on: str = "",
    user: CurrentUser = Depends(require("assets.export")),
    db: Session = Depends(get_tenant_db),
):
    c = _register_ctx(db, user, on)
    book = ReportBook()
    ws = book.add_sheet(
        "S09-DNN",
        company=c["company"],
        form_code="S09-DNN",
        title="SỔ TÀI SẢN CỐ ĐỊNH",
        subtitle=f"Đến ngày {fmt_date(c['on'])}",
        columns=[
            Col("STT", 6, "int"),
            Col("Mã TSCĐ", 12),
            Col("Tên TSCĐ", 34),
            Col("Nhóm", 22),
            Col("Ngày ghi tăng", 13, "date"),
            Col("Số tháng SD", 10, "int"),
            Col("Nguyên giá", 17, "money"),
            Col("Hao mòn lũy kế", 17, "money"),
            Col("Giá trị còn lại", 17, "money"),
            Col("Bộ phận sử dụng", 20),
        ],
    )
    first = None
    for i, r in enumerate(c["rows"], start=1):
        idx = book.row(
            ws,
            [
                i,
                r.asset.code,
                r.asset.name,
                r.asset.category_label,
                r.asset.acquired_date,
                r.asset.useful_life_months,
                r.cost,
                r.accumulated,
                None,
                r.asset.department,
            ],
        )
        ws.cell(idx, 9).value = f"=G{idx}-H{idx}"
        first = first or idx
    book.total_row(ws, "Cộng", sum_cols=[6, 7, 8], label_col=3, from_row=first)
    book.signatures(ws, c["company"])
    return xlsx_response(book.to_bytes(), f"so-tscd-{c['on']:%Y%m%d}.xlsx")


# ------------------------------------------- fixed assets: create, edit


def _asset_input(
    form: dict, existing: FixedAsset | None = None
) -> service.AssetInput:
    acquired = _date(form, "acquired_date")
    if acquired is None:
        raise BusinessError("Nhập ngày ghi tăng")
    life_months = _int(form, "useful_life_months")
    if not life_months and form.get("useful_life_years"):
        life_months = _int(form, "useful_life_years") * 12
    is_opening = form.get("is_opening") == "1"
    year_made = _int(form, "year_made") or None
    partner_id = _int(form, "partner_id") or None
    return service.AssetInput(
        name=form.get("name") or "",
        cost=_money(form, "cost"),
        useful_life_months=life_months,
        acquired_date=acquired,
        code=form.get("code") or "",
        category=form.get("category") or "office",
        asset_account=form.get("asset_account") or "",
        depreciation_account="",
        expense_account=form.get("expense_account") or "",
        department=form.get("department") or "",
        manufacturer=form.get("manufacturer") or "",
        serial_no=form.get("serial_no") or "",
        year_made=year_made,
        opening_accumulated=_money(form, "opening_accumulated")
        if is_opening
        else 0,
        opening_months_used=_int(form, "opening_months_used")
        if is_opening
        else 0,
        start_next_month=form.get("start_next_month") == "1",
        depreciation_start=(
            _date(form, "depreciation_start") if existing else None
        ),
        source=existing.source
        if existing
        else ("opening" if is_opening else (form.get("source") or "manual")),
        entry_id=existing.entry_id
        if existing
        else (_int(form, "entry_id") or None),
        partner_id=partner_id,
        note=form.get("note") or "",
    )


def _asset_form_ctx(
    db: Session,
    user: CurrentUser,
    form: dict,
    asset: FixedAsset | None,
    errors: dict,
) -> dict:
    form = dict(form)
    pid = form.get("partner_id")
    if pid and str(pid).isdigit():
        partner = db.get(Partner, int(pid))
        if partner is not None and partner.company_id == user.company_id:
            form["partner_label"] = partner.label
    return {
        "form": form,
        "asset": asset,
        "errors": errors,
        "categories": list(ASSET_CATEGORIES.items()),
        "asset_accounts": _asset_accounts(db, user.company_id),
        "expense_accounts": _expense_accounts(db, user.company_id),
        "breadcrumbs": [
            ("Tài sản cố định", "/assets"),
            (asset.code if asset else "Ghi tăng", None),
        ],
    }


@router.get("/assets/new")
def asset_new(
    request: Request,
    from_entry: int | None = None,
    line: int = 0,
    user: CurrentUser = Depends(require("assets.create")),
    db: Session = Depends(get_tenant_db),
):
    company = db.get(Company, user.company_id)
    form: dict = {
        "category": "office",
        "asset_account": regime_account(company, "fixed_asset"),
        "expense_account": regime_account(company, "expense_depreciation"),
        "acquired_date": today().isoformat(),
        "useful_life_months": "",
    }
    suggestions = []
    if from_entry:
        suggestions = service.purchase_prefill(
            db, get_ctx(request), from_entry
        )
        if suggestions:
            s = suggestions[min(line, len(suggestions) - 1)]
            form.update(
                {
                    "name": s["name"],
                    "asset_account": s["asset_account"],
                    "cost": s["cost"],
                    "acquired_date": s["acquired_date"].isoformat(),
                    "entry_id": s["entry_id"],
                    "partner_id": s.get("partner_id") or "",
                    "source": "purchase",
                }
            )
    ctx = _asset_form_ctx(db, user, form, None, {})
    ctx["suggestions"] = suggestions
    return render(request, "assets/form.html", ctx)


@router.post("/assets")
def asset_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("assets.create")),
    db: Session = Depends(get_tenant_db),
):
    try:
        a = service.create_asset(db, get_ctx(request), _asset_input(form))
    except BusinessError as e:
        db.rollback()
        return render(
            request,
            "assets/form.html",
            _asset_form_ctx(db, user, form, None, {"_": e.message}),
            status_code=422,
        )
    return redirect(
        f"/assets/{a.id}", f"Đã ghi tăng TSCĐ {a.code}", request=request
    )


@router.get("/assets/depreciation")
def depreciation_page(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    user: CurrentUser = Depends(require("assets.view")),
    db: Session = Depends(get_tenant_db),
):
    return _run_page(request, db, user, RUN_DEPRECIATION, year, month)


@router.post("/assets/depreciation/run")
def depreciation_run(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("assets.post")),
    db: Session = Depends(get_tenant_db),
):
    return _execute(request, db, RUN_DEPRECIATION, form)


@router.get("/assets/depreciation/sheet.pdf")
def depreciation_pdf(
    year: int,
    month: int,
    user: CurrentUser = Depends(require("assets.export")),
    db: Session = Depends(get_tenant_db),
):
    return _sheet_pdf(db, user, RUN_DEPRECIATION, year, month)


@router.get("/assets/depreciation/sheet.xlsx")
def depreciation_xlsx(
    year: int,
    month: int,
    user: CurrentUser = Depends(require("assets.export")),
    db: Session = Depends(get_tenant_db),
):
    return _sheet_xlsx(db, user, RUN_DEPRECIATION, year, month)


@router.get("/assets/entries/{entry_id}")
def asset_entry(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(require("assets.view")),
    db: Session = Depends(get_tenant_db),
):
    ctx = get_ctx(request)
    e = js.get_entry(db, ctx, entry_id, doc_types=("KH", "TS", "PB"))
    run = db.scalar(
        select(AssetRun).where(
            AssetRun.company_id == ctx.company_id, AssetRun.entry_id == e.id
        )
    )
    if run is not None:
        return RedirectResponse(
            f"{RUN_PAGES[run.kind]['url']}?year={run.year}&month={run.month}",
            status_code=303,
        )
    asset = db.scalar(
        select(FixedAsset).where(
            FixedAsset.company_id == ctx.company_id,
            FixedAsset.disposal_entry_id == e.id,
        )
    )
    if asset is not None:
        return RedirectResponse(f"/assets/{asset.id}", status_code=303)
    return render(
        request,
        "assets/entry.html",
        {
            "e": e,
            "back": "/assets",
            "breadcrumbs": [("Tài sản", "/assets"), (e.display_number, None)],
        },
    )


@router.get("/assets/{asset_id}")
def asset_detail(
    request: Request,
    asset_id: int,
    user: CurrentUser = Depends(require("assets.view")),
    db: Session = Depends(get_tenant_db),
):
    a = service.get_asset(db, user.company_id, asset_id)
    posted = service.posted_depreciation(db, user.company_id, [a.id]).get(
        a.id, 0
    )
    accumulated = (a.opening_accumulated or 0) + posted
    history = _history(db, user.company_id, "asset", a.id)
    entries = [
        e
        for e in (
            db.get(JournalEntry, a.entry_id) if a.entry_id else None,
            db.get(JournalEntry, a.disposal_entry_id)
            if a.disposal_entry_id
            else None,
        )
        if e
    ]
    return render(
        request,
        "assets/detail.html",
        {
            "a": a,
            "accumulated": accumulated,
            "remaining": a.cost - accumulated,
            "monthly": service.monthly_rate(a),
            "schedule": service.schedule(a),
            "history": history,
            "entries": entries,
            "today": today(),
            "partner": db.get(Partner, a.partner_id) if a.partner_id else None,
            "breadcrumbs": [("Tài sản cố định", "/assets"), (a.code, None)],
        },
    )


def _history(
    db: Session, company_id: int, kind: str, ref_id: int
) -> list[dict]:
    from app.features.assets.models import AssetRunLine

    rows = db.execute(
        select(AssetRun, AssetRunLine)
        .join(AssetRunLine, AssetRunLine.run_id == AssetRun.id)
        .where(
            AssetRun.company_id == company_id,
            AssetRunLine.ref_kind == kind,
            AssetRunLine.ref_id == ref_id,
        )
        .order_by(AssetRun.year, AssetRun.month)
    ).all()
    return [
        {
            "year": r.year,
            "month": r.month,
            "amount": ln.amount,
            "entry": r.entry,
            "posted": bool(r.entry and r.entry.status == "posted"),
        }
        for r, ln in rows
    ]


@router.get("/assets/{asset_id}/edit")
def asset_edit(
    request: Request,
    asset_id: int,
    user: CurrentUser = Depends(require("assets.manage")),
    db: Session = Depends(get_tenant_db),
):
    a = service.get_asset(db, user.company_id, asset_id)
    form = {f: getattr(a, f) for f in service.FIELDS}
    form["is_opening"] = a.source == "opening"
    for k in ("acquired_date", "depreciation_start"):
        form[k] = form[k].isoformat() if form[k] else ""
    return render(
        request, "assets/form.html", _asset_form_ctx(db, user, form, a, {})
    )


@router.post("/assets/{asset_id}")
def asset_update(
    request: Request,
    asset_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("assets.manage")),
    db: Session = Depends(get_tenant_db),
):
    a = service.get_asset(db, user.company_id, asset_id)
    try:
        service.update_asset(db, get_ctx(request), a, _asset_input(form, a))
    except BusinessError as e:
        db.rollback()
        return render(
            request,
            "assets/form.html",
            _asset_form_ctx(db, user, form, a, {"_": e.message}),
            status_code=422,
        )
    return redirect(f"/assets/{a.id}", "Đã lưu thẻ tài sản", request=request)


@router.post("/assets/{asset_id}/dispose")
def asset_dispose(
    request: Request,
    asset_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("assets.post")),
    db: Session = Depends(get_tenant_db),
):
    a = service.get_asset(db, user.company_id, asset_id)
    on = _date(form, "date", today())
    e = service.dispose(
        db,
        get_ctx(request),
        a,
        on,
        form.get("reason") or "",
        form.get("loss_account") or "811",
    )
    return redirect(
        f"/assets/{asset_id}",
        f"Đã ghi giảm TSCĐ – chứng từ {e.number}",
        request=request,
    )


@router.post("/assets/{asset_id}/delete")
def asset_delete(
    request: Request,
    asset_id: int,
    user: CurrentUser = Depends(require("assets.manage")),
    db: Session = Depends(get_tenant_db),
):
    a = service.get_asset(db, user.company_id, asset_id)
    service.delete_asset(db, get_ctx(request), a)
    return redirect("/assets", "Đã xóa thẻ tài sản", request=request)


@router.get("/assets/{asset_id}/card.pdf")
def asset_card_pdf(
    asset_id: int,
    user: CurrentUser = Depends(require("assets.export")),
    db: Session = Depends(get_tenant_db),
):
    a = service.get_asset(db, user.company_id, asset_id)
    hist = [
        h for h in _history(db, user.company_id, "asset", a.id) if h["posted"]
    ]
    by_year: dict[int, int] = {}
    for h in hist:
        by_year[h["year"]] = by_year.get(h["year"], 0) + h["amount"]
    acc = a.opening_accumulated or 0
    years = []
    for y in sorted(by_year):
        acc += by_year[y]
        years.append({"year": y, "amount": by_year[y], "accumulated": acc})
    ctx = {
        "company": _company(db, user),
        "a": a,
        "years": years,
        "accumulated": acc,
        "entry": db.get(JournalEntry, a.entry_id) if a.entry_id else None,
        "disposal": db.get(JournalEntry, a.disposal_entry_id)
        if a.disposal_entry_id
        else None,
    }
    return pdf_response(
        render_pdf("assets/print/card.html", ctx), f"the-tscd-{a.code}.pdf"
    )


# ------------------------------ depreciation / allocation runs (shared)


def _run_page(
    request: Request,
    db: Session,
    user: CurrentUser,
    kind: str,
    year: int | None,
    month: int | None,
):
    y, m = _ym(year, month)
    meta = RUN_PAGES[kind]
    pv = service.preview(db, user.company_id, kind, y, m)
    run = service.get_run(db, user.company_id, kind, y, m)
    runs = service.list_runs(db, user.company_id, kind)
    missing = service.missing_months(db, user.company_id, kind, today())
    from app.features.journal import locks

    return render(
        request,
        "assets/runs.html",
        {
            "kind": kind,
            "meta": meta,
            "year": y,
            "month": m,
            "preview": pv,
            "run": run,
            "runs": runs[:24],
            "missing": missing,
            "locked": locks.is_locked(db, user.company_id, date(y, m, 1)),
            "breadcrumbs": [
                (meta["parent"][0], meta["parent"][1]),
                (meta["title"], None),
            ],
        },
    )


def _execute(request: Request, db: Session, kind: str, form: dict):
    y, m = _ym(_int(form, "year") or None, _int(form, "month") or None)
    run = service.execute_run(db, get_ctx(request), kind, y, m)
    label = "trích khấu hao" if kind == RUN_DEPRECIATION else "phân bổ chi phí"
    return redirect(
        f"{RUN_PAGES[kind]['url']}?year={y}&month={m}",
        f"Đã {label} tháng {m:02d}/{y} – chứng từ {run.entry.number}",
        request=request,
    )


def _sheet_ctx(
    db: Session, user: CurrentUser, kind: str, year: int, month: int
) -> dict:
    y, m = _ym(year, month)
    run = service.get_run(db, user.company_id, kind, y, m)
    posted = bool(run and run.entry and run.entry.status == "posted")
    if posted:
        refs = {}
        model = FixedAsset if kind == RUN_DEPRECIATION else service.PrepaidItem
        for ln in run.lines:
            refs[ln.ref_id] = db.get(model, ln.ref_id)
        rows = [
            {
                "ref": refs[ln.ref_id],
                "amount": ln.amount,
                "debit": ln.debit_account,
                "credit": ln.credit_account,
            }
            for ln in run.lines
        ]
    else:
        pv = service.preview(db, user.company_id, kind, y, m)
        rows = [
            {
                "ref": ln.ref,
                "amount": ln.amount,
                "debit": ln.debit_account,
                "credit": ln.credit_account,
            }
            for ln in pv.lines
        ]
    by_acc: dict[str, int] = {}
    for r in rows:
        by_acc[r["debit"]] = by_acc.get(r["debit"], 0) + r["amount"]
    return {
        "company": _company(db, user),
        "kind": kind,
        "meta": RUN_PAGES[kind],
        "year": y,
        "month": m,
        "rows": rows,
        "by_account": sorted(by_acc.items()),
        "total": sum(r["amount"] for r in rows),
        "posted": posted,
        "entry": run.entry if run else None,
    }


def _sheet_pdf(
    db: Session, user: CurrentUser, kind: str, year: int, month: int
):
    c = _sheet_ctx(db, user, kind, year, month)
    name = "bang-khau-hao" if kind == RUN_DEPRECIATION else "bang-phan-bo"
    return pdf_response(
        render_pdf("assets/print/run_sheet.html", c),
        f"{name}-{c['year']}-{c['month']:02d}.pdf",
    )


def _sheet_xlsx(
    db: Session, user: CurrentUser, kind: str, year: int, month: int
):
    c = _sheet_ctx(db, user, kind, year, month)
    book = ReportBook()
    dep = kind == RUN_DEPRECIATION
    cols = [
        Col("STT", 6, "int"),
        Col("Mã", 12),
        Col("Tên " + ("TSCĐ" if dep else "CCDC / khoản chi phí"), 36),
        Col("Giá trị" if not dep else "Nguyên giá", 17, "money"),
        Col("Số tháng", 9, "int"),
        Col(
            "Số " + ("khấu hao" if dep else "phân bổ") + " tháng", 17, "money"
        ),
        Col("TK Nợ", 9),
        Col("TK Có", 9),
    ]
    ws = book.add_sheet(
        "Bang tinh",
        company=c["company"],
        form_code=c["meta"]["form_code"],
        title=c["meta"]["sheet_title"],
        subtitle=f"Tháng {c['month']:02d} năm {c['year']}",
        columns=cols,
    )
    first = None
    for i, r in enumerate(c["rows"], start=1):
        ref = r["ref"]
        idx = book.row(
            ws,
            [
                i,
                ref.code,
                ref.name,
                ref.cost if dep else ref.amount,
                ref.useful_life_months if dep else ref.months,
                r["amount"],
                r["debit"],
                r["credit"],
            ],
        )
        first = first or idx
    book.total_row(ws, "Cộng", sum_cols=[5], label_col=3, from_row=first)
    book.signatures(ws, c["company"])
    name = "bang-khau-hao" if dep else "bang-phan-bo"
    return xlsx_response(
        book.to_bytes(), f"{name}-{c['year']}-{c['month']:02d}.xlsx"
    )


# --------------------------------------------- tools / prepaid expenses


def _prepaid_input(form: dict, existing=None) -> service.PrepaidInput:
    start = _date(form, "start_month")
    if start is None:
        raw = (form.get("start_month") or "").strip()
        raise BusinessError(
            "Nhập tháng bắt đầu phân bổ" if not raw else "Tháng không hợp lệ"
        )
    is_opening = form.get("is_opening") == "1"
    return service.PrepaidInput(
        name=form.get("name") or "",
        amount=_money(form, "amount"),
        months=_int(form, "months"),
        start_month=start,
        code=form.get("code") or "",
        kind=form.get("kind") or "tool",
        quantity=_int(form, "quantity", 1) or 1,
        prepaid_account=form.get("prepaid_account") or "242",
        expense_account=form.get("expense_account") or "",
        department=form.get("department") or "",
        opening_allocated=_money(form, "opening_allocated")
        if is_opening
        else 0,
        opening_months_used=_int(form, "opening_months_used")
        if is_opening
        else 0,
        entry_id=existing.entry_id
        if existing
        else (_int(form, "entry_id") or None),
        note=form.get("note") or "",
    )


def _month_value(v) -> str:
    if not v:
        return ""
    d = v if isinstance(v, date) else parse_date(v)
    return f"{d:%Y-%m}" if d else ""


def _prepaid_ctx(
    db: Session, user: CurrentUser, form: dict, item, errors: dict
) -> dict:
    return {
        "form": form,
        "item": item,
        "errors": errors,
        "kinds": list(PREPAID_KINDS.items()),
        "expense_accounts": _expense_accounts(db, user.company_id),
        "breadcrumbs": [
            ("CCDC & chi phí trả trước", "/prepaid"),
            (item.code if item else "Ghi tăng", None),
        ],
    }


def _parse_month_form(form: dict) -> dict:
    raw = (form.get("start_month") or "").strip()
    if len(raw) == 7 and raw[4] == "-":  # input type=month: YYYY-MM
        form = dict(form)
        form["start_month"] = raw + "-01"
    return form


@router.get("/prepaid")
def prepaid_page(
    request: Request,
    show: str = "active",
    user: CurrentUser = Depends(require("assets.view")),
    db: Session = Depends(get_tenant_db),
):
    items = service.list_prepaid(
        db, user.company_id, active=None if show == "all" else True
    )
    posted = service.posted_depreciation(
        db, user.company_id, [p.id for p in items], kind="prepaid"
    )
    rows = []
    for p in items:
        allocated = (p.opening_allocated or 0) + posted.get(p.id, 0)
        rows.append(
            {
                "p": p,
                "allocated": allocated,
                "remaining": p.amount - allocated,
                "monthly": service.scheduled_amount(
                    *service.prepaid_base(p), 1
                ),
            }
        )
    return render(
        request,
        "assets/prepaid_index.html",
        {
            "rows": rows,
            "show": show,
            "total": sum(r["p"].amount for r in rows),
            "total_remaining": sum(r["remaining"] for r in rows),
            "breadcrumbs": [
                ("Tài sản", None),
                ("CCDC & chi phí trả trước", None),
            ],
        },
    )


@router.get("/prepaid/new")
def prepaid_new(
    request: Request,
    from_entry: int | None = None,
    user: CurrentUser = Depends(require("assets.create")),
    db: Session = Depends(get_tenant_db),
):
    form: dict = {
        "kind": "tool",
        "expense_account": regime_account(
            db.get(Company, user.company_id), "expense_tools"
        ),
        "start_month": f"{today():%Y-%m}",
        "months": 12,
        "quantity": 1,
    }
    if from_entry:
        pre = service.prepaid_prefill(db, get_ctx(request), from_entry)
        if pre:
            form.update(
                {
                    "name": pre[0]["name"],
                    "amount": pre[0]["amount"],
                    "start_month": _month_value(pre[0]["start_month"]),
                    "entry_id": pre[0]["entry_id"],
                }
            )
    return render(
        request,
        "assets/prepaid_form.html",
        _prepaid_ctx(db, user, form, None, {}),
    )


@router.post("/prepaid")
def prepaid_create(
    request: Request,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("assets.create")),
    db: Session = Depends(get_tenant_db),
):
    form = _parse_month_form(form)
    try:
        p = service.create_prepaid(db, get_ctx(request), _prepaid_input(form))
    except BusinessError as e:
        db.rollback()
        form["start_month"] = _month_value(form.get("start_month"))
        return render(
            request,
            "assets/prepaid_form.html",
            _prepaid_ctx(db, user, form, None, {"_": e.message}),
            status_code=422,
        )
    return redirect(
        f"/prepaid/{p.id}", f"Đã ghi tăng {p.code}", request=request
    )


@router.get("/prepaid/allocation")
def allocation_page(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    user: CurrentUser = Depends(require("assets.view")),
    db: Session = Depends(get_tenant_db),
):
    return _run_page(request, db, user, RUN_ALLOCATION, year, month)


@router.post("/prepaid/allocation/run")
def allocation_run(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("assets.post")),
    db: Session = Depends(get_tenant_db),
):
    return _execute(request, db, RUN_ALLOCATION, form)


@router.get("/prepaid/allocation/sheet.pdf")
def allocation_pdf(
    year: int,
    month: int,
    user: CurrentUser = Depends(require("assets.export")),
    db: Session = Depends(get_tenant_db),
):
    return _sheet_pdf(db, user, RUN_ALLOCATION, year, month)


@router.get("/prepaid/allocation/sheet.xlsx")
def allocation_xlsx(
    year: int,
    month: int,
    user: CurrentUser = Depends(require("assets.export")),
    db: Session = Depends(get_tenant_db),
):
    return _sheet_xlsx(db, user, RUN_ALLOCATION, year, month)


@router.get("/prepaid/{item_id}")
def prepaid_detail(
    request: Request,
    item_id: int,
    user: CurrentUser = Depends(require("assets.view")),
    db: Session = Depends(get_tenant_db),
):
    p = service.get_prepaid(db, user.company_id, item_id)
    posted = service.posted_depreciation(
        db, user.company_id, [p.id], kind="prepaid"
    ).get(p.id, 0)
    allocated = (p.opening_allocated or 0) + posted
    form = {f: getattr(p, f) for f in service.PREPAID_FIELDS}
    form["start_month"] = _month_value(p.start_month)
    form["is_opening"] = bool(p.opening_allocated or p.opening_months_used)
    base, n = service.prepaid_base(p)
    schedule = [
        (
            service.add_months(p.start_month, k - 1),
            service.scheduled_amount(base, n, k),
        )
        for k in range(1, n + 1)
    ]
    ctx = _prepaid_ctx(db, user, form, p, {})
    ctx.update(
        {
            "p": p,
            "allocated": allocated,
            "remaining": p.amount - allocated,
            "schedule": schedule,
            "history": _history(db, user.company_id, "prepaid", p.id),
            "entry": db.get(JournalEntry, p.entry_id) if p.entry_id else None,
        }
    )
    return render(request, "assets/prepaid_detail.html", ctx)


@router.post("/prepaid/{item_id}")
def prepaid_update(
    request: Request,
    item_id: int,
    form: dict = Depends(form_data),
    user: CurrentUser = Depends(require("assets.manage")),
    db: Session = Depends(get_tenant_db),
):
    p = service.get_prepaid(db, user.company_id, item_id)
    form = _parse_month_form(form)
    active = None
    if form.get("action") in ("stop", "resume"):
        active = form["action"] == "resume"
        inp = service.PrepaidInput(
            **{f: getattr(p, f) for f in service.PREPAID_FIELDS}
        )
        service.update_prepaid(
            db,
            get_ctx(request),
            p,
            inp,
            active=active,
            stopped_date=_date(form, "stopped_date", today()),
        )
        return redirect(
            f"/prepaid/{p.id}",
            "Đã dừng phân bổ" if not active else "Đã phân bổ tiếp",
            request=request,
        )
    service.update_prepaid(db, get_ctx(request), p, _prepaid_input(form, p))
    return redirect(f"/prepaid/{p.id}", "Đã lưu", request=request)
