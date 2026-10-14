"""Security review B regressions (authorization, payroll privacy), D-5.

B-01/D-5: salaries are not exposed to users without payroll access
(ledgers, receivables/payables, AI, audit log).
B-02: Ctrl+K only returns vouchers the user may view.
B-04: another company's ids in submitted data are rejected.
B-05: a cashier cannot edit the description of a posted voucher.
B-06: the audit log records the acting user's email.
B-07: employee personal data is shown only to users with payroll
access.
"""

import io
from datetime import date

import pytest
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.errors import BusinessError
from app.core.registry import ACCOUNTANT, CASHIER, CHIEF, OWNER, VIEWER
from app.core.security import build_current_user
from app.core.tenancy import Ctx
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.payroll import service as payroll
from app.features.payroll.service import EmployeeInput
from tests.conftest import ROLE_EMAILS, make_company, make_user, post

SALARY = 31_234_567
SALARY_TXT = "31.234.567"
NAME = "Nguyễn Văn Bí Mật"
ID_NUMBER = "079012345678"
BANK_NO = "9988776655"
ADDRESS = "12 Đường Riêng Tư"
YEAR_Q = "mode=year&year=2026"


@pytest.fixture()
def payroll_book(db, ctxs):
    ctx = ctxs[CHIEF]
    post(
        db, ctx, date(2026, 1, 2), "1111", "4111", 200_000_000, "PT", "Góp vốn"
    )
    emp = payroll.create_employee(
        db,
        ctx,
        EmployeeInput(
            full_name=NAME,
            base_salary=SALARY,
            insurance_salary=SALARY,
            start_date=date(2025, 1, 1),
            id_number=ID_NUMBER,
            bank_account=BANK_NO,
            bank_name="VCB",
            address=ADDRESS,
        ),
    )
    # Add a second employee: the TK 334 total (visible) differs from
    # each individual salary (must be hidden).
    payroll.create_employee(
        db,
        ctx,
        EmployeeInput(
            full_name="Trần Thị Hai",
            base_salary=9_000_000,
            insurance_salary=9_000_000,
            start_date=date(2025, 1, 1),
        ),
    )
    run = payroll.create_run(db, ctx, 2026, 1)
    lg = payroll.post_run(db, ctx, run)
    db.refresh(run)
    pay = payroll.create_payment(db, ctx, run, "cash", date(2026, 2, 5))
    js.post_entry(db, ctx, pay)
    return {"emp": emp, "partner_id": emp.partner_id, "lg": lg, "pay": pay}


# --------------------------------------------------------- B-01 / D-5


def test_ledgers_mask_payroll_for_viewer(web, users, payroll_book):
    web.login(ROLE_EMAILS[VIEWER])
    for url in (
        f"/reports/ledger?account=334&{YEAR_Q}",
        f"/reports/s03a?{YEAR_Q}",
        f"/reports/s03b?account=642&{YEAR_Q}",
        f"/reports/s03b?account=334&{YEAR_Q}",
    ):
        r = web.get(url)
        assert r.status_code == 200, url
        assert NAME not in r.text and SALARY_TXT not in r.text, url
    assert "chi tiết ẩn" in web.get(f"/reports/s03a?{YEAR_Q}").text
    # The exported Excel file is masked too
    from openpyxl import load_workbook

    r = web.get(f"/reports/ledger.xlsx?account=334&{YEAR_Q}")
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    cells = " ".join(
        str(c.value)
        for ws in wb
        for row in ws.iter_rows()
        for c in row
        if c.value is not None
    )
    assert NAME not in cells and str(SALARY) not in cells
    # Users with payroll access still see everything
    web.login(ROLE_EMAILS[CHIEF])
    assert SALARY_TXT in web.get(f"/reports/ledger?account=334&{YEAR_Q}").text


def test_masked_totals_unchanged(db, company, payroll_book):
    from app.features.gl import books

    full = books.general_journal(
        db, company.id, date(2026, 1, 1), date(2026, 12, 31)
    )
    masked = books.general_journal(
        db, company.id, date(2026, 1, 1), date(2026, 12, 31), mask_payroll=True
    )
    assert (
        sum(r.debit for r in masked.rows)
        == sum(r.debit for r in full.rows)
        == full.total_debit
    )
    assert sum(r.credit for r in masked.rows) == full.total_credit
    assert len(masked.rows) < len(full.rows)
    led = books.ledger_book(
        db,
        company.id,
        "334",
        date(2026, 1, 1),
        date(2026, 12, 31),
        mask_payroll=True,
    )
    ref = books.ledger_book(
        db, company.id, "334", date(2026, 1, 1), date(2026, 12, 31)
    )
    assert led.closing == ref.closing and (
        led.rows[-1].balance == ref.rows[-1].balance
    )
    assert all(r.partner_id is None for r in led.rows)


def test_employee_ledger_and_debts_need_payroll_permission(
    web, users, payroll_book
):
    pid = payroll_book["partner_id"]
    web.login(ROLE_EMAILS[VIEWER])
    assert (
        web.get(
            f"/reports/ledger?account=334&partner_id={pid}&{YEAR_Q}"
        ).status_code
        == 403
    )
    assert (
        web.get(
            f"/reports/ledger?account=141&partner_id={pid}&{YEAR_Q}"
        ).status_code
        == 403
    )
    assert web.get(f"/debts/partner/{pid}").status_code == 403
    assert (
        web.get(f"/debts/partner/{pid}/reconciliation.pdf").status_code == 403
    )
    web.login(ROLE_EMAILS[CHIEF])
    assert (
        web.get(
            f"/reports/ledger?account=334&partner_id={pid}&{YEAR_Q}"
        ).status_code
        == 200
    )
    assert web.get(f"/debts/partner/{pid}").status_code == 200


def test_ai_ledger_tool_masked(db, ctxs, payroll_book):
    from app.features.gl.ai_tools import get_account_ledger

    viewer = ctxs[VIEWER]
    out = get_account_ledger(
        db,
        viewer,
        account="334",
        start="2026-01-01",
        end="2026-12-31",
        limit=200,
    )
    text = repr(out)
    assert NAME not in text and str(SALARY) not in text
    code = payroll_book["emp"].partner.code
    out = get_account_ledger(
        db,
        viewer,
        account="334",
        start="2026-01-01",
        end="2026-12-31",
        partner_code=code,
    )
    assert "error" in out
    full = get_account_ledger(
        db,
        ctxs[CHIEF],
        account="334",
        start="2026-01-01",
        end="2026-12-31",
        limit=200,
    )
    assert NAME in repr(full)


def test_audit_hides_payroll_details(web, db, ctxs, users, payroll_book):
    ctx = ctxs[CHIEF]
    emp = payroll_book["emp"]
    inp = EmployeeInput(
        full_name=emp.full_name,
        base_salary=SALARY + 1_000_000,
        insurance_salary=SALARY,
        start_date=date(2025, 1, 1),
    )
    payroll.update_employee(db, ctx, emp, inp)
    log = db.scalars(
        select(AuditLog).where(AuditLog.action == "payroll.employee_update")
    ).one()
    assert "base_salary" in (log.after or "") and str(
        SALARY + 1_000_000
    ) not in (log.after or "")
    assert log.before is None
    web.login(ROLE_EMAILS[VIEWER])
    page = web.get("/audit").text
    assert "Chi tiết ẩn – cần quyền xem lương" in page
    assert SALARY_TXT not in page and str(SALARY) not in page
    web.login(ROLE_EMAILS[CHIEF])
    assert "changed_fields" in web.get("/audit").text


# ---------------------------------------------------------------- B-02


def test_search_only_visible_doc_types(web, users, payroll_book):
    lg_number = payroll_book["lg"].number
    web.login(ROLE_EMAILS[CASHIER])
    labels = [
        r["label"] for r in web.get(f"/search?q={lg_number}").json()["results"]
    ]
    assert not any(lg_number in lb for lb in labels)
    labels = [
        r["label"]
        for r in web.get("/search?q=Hạch toán lương").json()["results"]
    ]
    assert not any("Hạch toán lương" in lb for lb in labels)
    # The cashier can still find cash receipts/payments
    assert any(
        "PT" in r["label"]
        for r in web.get("/search?q=Góp vốn").json()["results"]
    )
    web.login(ROLE_EMAILS[CHIEF])
    labels = [
        r["label"] for r in web.get(f"/search?q={lg_number}").json()["results"]
    ]
    assert any(lg_number in lb for lb in labels)


# ---------------------------------------------------------------- B-04


@pytest.fixture()
def other(db):
    owner = make_user(db, "chu.khac@b.vn")
    c = make_company(db, owner, name="Công ty B", tax_code="0101010101")
    return c, Ctx(company_id=c.id, user=build_current_user(db, owner, c.id))


def test_foreign_ids_rejected(web, db, ctx, users, other):
    from app.features.assets import service as assets
    from app.features.cash import counts
    from app.features.inventory.models import Warehouse
    from app.features.sales.common import build_lines
    from app.features.sales.service import parse_form as sales_parse

    comp_b, ctx_b = other
    wh_b = Warehouse(company_id=comp_b.id, code="KB", name="Kho B")
    db.add(wh_b)
    db.commit()
    e_b = post(db, ctx_b, date(2026, 1, 3), "1111", "4111", 5_000_000, "PT")
    # another company's warehouse in a sales/purchase line
    _lines, errors = build_lines(
        db,
        ctx.company_id,
        [
            {
                "description": "Dịch vụ",
                "quantity": "1",
                "unit_price": "100000",
                "vat_rate": "10",
                "account": "5113",
                "warehouse_id": str(wh_b.id),
            }
        ],
        default_account="5113",
    )
    assert any("kho không hợp lệ" in e for e in errors)
    # another company's original sales voucher (discount/return)
    _inp, errs, _ = sales_parse(
        db,
        ctx,
        {
            "date": "2026-01-10",
            "payment_method": "cash",
            "partner_name": "Khách lẻ",
            "original_entry_id": str(e_b.id),
        },
        "return",
    )
    assert "original_entry_id" in errs
    # another company's tools-and-supplies increase voucher
    with pytest.raises(BusinessError, match="Chứng từ ghi tăng không hợp lệ"):
        assets.create_prepaid(
            db,
            ctx,
            assets.PrepaidInput(
                name="Bàn",
                amount=3_000_000,
                months=12,
                start_month=date(2026, 1, 1),
                entry_id=e_b.id,
            ),
        )
    # another company's stock count record
    company_b = db.get(type(comp_b), comp_b.id)
    cc_inp, _ = counts.parse_form(
        {"count_date": "2026-01-03", "qty_500000": "1"}, company_b
    )
    cc_b = counts.create_cash_count(db, ctx_b, cc_inp)
    post(db, ctx, date(2026, 1, 2), "1111", "4111", 10_000_000, "PT")
    web.login(ROLE_EMAILS[CHIEF])
    form = {
        "kind": "PC",
        "mode": "quick",
        "date": "2026-01-05",
        "contact_name": "Thủ quỹ",
        "description": "Xử lý kiểm kê",
        "account": "1381",
        "amount": "1000",
        "action": "save",
    }
    r = web.post(
        "/cash",
        {**form, "cash_count_id": str(cc_b.id)},
        follow_redirects=False,
    )
    assert r.status_code != 303 and "Biên bản kiểm kê không hợp lệ" in r.text
    # same form without the other company's record -> saves normally
    assert web.post("/cash", form, follow_redirects=False).status_code == 303
    assert (
        db.scalar(
            select(JournalEntry.id).where(
                JournalEntry.company_id == ctx.company_id,
                JournalEntry.source_id == cc_b.id,
                JournalEntry.source == "cashcount",
            )
        )
        is None
    )


# ---------------------------------------------------------------- B-05


def test_cashier_cannot_edit_posted_info(web, db, ctxs, users):
    e = post(
        db,
        ctxs[CASHIER],
        date(2026, 1, 2),
        "1111",
        "4111",
        1_000_000,
        "PT",
        "Gốc",
    )
    web.login(ROLE_EMAILS[CASHIER])
    assert (
        web.post(
            f"/entries/{e.id}/info", {"description": "Sửa trộm"}
        ).status_code
        == 403
    )
    db.expire_all()
    assert db.get(JournalEntry, e.id).description == "Gốc"
    web.login(ROLE_EMAILS[ACCOUNTANT])
    assert (
        web.post(
            f"/entries/{e.id}/info",
            {"description": "Sửa hợp lệ"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    db.expire_all()
    assert db.get(JournalEntry, e.id).description == "Sửa hợp lệ"


# ---------------------------------------------------------------- B-06


def test_audit_username_has_email(web, db, ctxs, users):
    post(db, ctxs[ACCOUNTANT], date(2026, 1, 2), "1111", "4111", 1_000, "PT")
    log = db.scalars(
        select(AuditLog).where(AuditLog.action == "entry.post")
    ).first()
    assert log.username.endswith(f"<{ROLE_EMAILS[ACCOUNTANT]}>")
    web.login(ROLE_EMAILS[OWNER])
    assert ROLE_EMAILS[ACCOUNTANT] in web.get("/audit").text


# ---------------------------------------------------------------- B-07


def test_employee_personal_data_hidden(web, db, ctxs, users, payroll_book):
    pid = payroll_book["partner_id"]
    web.login(ROLE_EMAILS[VIEWER])
    for url in (f"/partners/{pid}", "/partners?role=employee"):
        page = web.get(url).text
        assert (
            ID_NUMBER not in page
            and BANK_NO not in page
            and ADDRESS not in page
        ), url
    found = web.get("/lookup/partners?q=Bí Mật").json()
    assert found and all(x["extra"]["address"] == "" for x in found)
    assert all(
        ADDRESS not in (r.get("hint") or "")
        for r in web.get("/search?q=Bí Mật").json()["results"]
    )
    from app.features.ai.ai_tools import search_partners

    assert ADDRESS not in repr(search_partners(db, ctxs[VIEWER], "Bí Mật"))
    web.login(ROLE_EMAILS[CHIEF])
    page = web.get(f"/partners/{pid}").text
    assert ID_NUMBER in page and BANK_NO in page
