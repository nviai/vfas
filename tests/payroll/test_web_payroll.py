"""Payroll web.

Create, edit, post, pay, reports, parameters; permissions (cashier
and viewer cannot see salaries).
"""

import io
import re

from openpyxl import load_workbook
from pypdf import PdfReader

from app.core.registry import ACCOUNTANT, CASHIER, CHIEF, OWNER, VIEWER
from tests.conftest import ROLE_EMAILS


def _employee(web, name, salary, **extra):
    data = {
        "full_name": name,
        "base_salary": salary,
        "insurance_salary": salary,
        "contract_type": "long",
        "is_resident": "1",
        "dependents": "0",
        "expense_account": "6422",
        "start_date": "2025-01-01",
    }
    data.update(extra)
    r = web.post("/payroll/employees", data, follow_redirects=False)
    assert r.status_code == 303, r.text[:400]
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_full_flow(web, users):
    web.login(ROLE_EMAILS[ACCOUNTANT])
    web.get("/payroll/employees/new")
    e1 = _employee(web, "Nguyễn Văn An", "30tr", dependents="1")
    _employee(
        web, "Trần Thị Bình", "12.000.000", nontaxable_allowance="730000"
    )
    r = web.post(
        "/payroll/runs",
        {"year": "2026", "month": "1", "standard_days": "26"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    run_url = r.headers["location"]
    page = web.get(run_url).text
    # PIT for January 2026
    assert "Nguyễn Văn An" in page and "257.500" in page
    line_ids = re.findall(r'name="days-(\d+)"', page)
    assert len(line_ids) == 2
    data = {"standard_days": "26", "action": "save"}
    for lid in line_ids:
        data[f"days-{lid}"] = "26"
    data[f"other-{line_ids[0]}"] = "1000000"
    assert web.post(run_url, data, follow_redirects=False).status_code == 303
    data["action"] = "post"
    r = web.post(run_url, data, follow_redirects=False)
    assert r.status_code == 303
    page = web.get(run_url).text
    assert "LG2026-0001" in page and 'name="days-' not in page
    run_id = run_url.rsplit("/", 1)[1]
    for url in (
        f"/payroll/runs/{run_id}/sheet.pdf",
        f"/payroll/runs/{run_id}/payslips.pdf",
    ):
        r = web.get(url)
        assert r.status_code == 200
        text = "".join(
            p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages
        )
        assert "Nguyễn Văn An" in text
    r = web.get(f"/payroll/runs/{run_id}/sheet.xlsx")
    ws = load_workbook(io.BytesIO(r.content)).active
    assert ws.title == "02-LDTL"
    # Pay salaries in cash -> draft PC voucher
    r = web.post(
        f"/payroll/runs/{run_id}/pay",
        {"method": "cash", "date": "2026-02-05"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    for url in (
        "/payroll",
        "/payroll/employees",
        f"/payroll/employees/{e1}",
        "/payroll/reports?year=2026",
        "/payroll/reports?year=2026&month=1&tab=insurance",
        "/payroll/settings",
    ):
        assert web.get(url).status_code == 200, url
    assert (
        web.get("/payroll/reports.xlsx?year=2026&tab=insurance").status_code
        == 200
    )
    assert (
        web.get("/payroll/reports.xlsx?year=2026&tab=pit").status_code == 200
    )
    web.get("/lang/en")
    for url in (
        "/payroll",
        run_url,
        "/payroll/employees",
        "/payroll/settings",
        "/payroll/reports?year=2026",
    ):
        assert web.get(url).status_code == 200, url


def test_params_page_add_version(web, users):
    web.login(ROLE_EMAILS[CHIEF])
    web.get("/payroll/settings")
    r = web.post(
        "/payroll/settings",
        {
            "key": "family_deduction",
            "effective_from": "2027-01-01",
            "self": "17tr",
            "dependent": "7tr",
            "source": "Thử",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "17.000.000" in web.get("/payroll/settings").text


def test_salary_is_private(web, users, db, ctxs):
    from app.features.payroll import service
    from app.features.payroll.service import EmployeeInput

    e = service.create_employee(
        db,
        ctxs[ACCOUNTANT],
        EmployeeInput(full_name="Lương Bí Mật", base_salary=99_000_000),
    )
    run = service.create_run(db, ctxs[ACCOUNTANT], 2026, 3)
    entry = service.post_run(db, ctxs[ACCOUNTANT], run)
    for role in (CASHIER, VIEWER):
        web.login(ROLE_EMAILS[role])
        for url in (
            "/payroll",
            f"/payroll/runs/{run.id}",
            "/payroll/employees",
            f"/payroll/employees/{e.id}",
            "/payroll/reports",
            "/payroll/settings",
            f"/payroll/runs/{run.id}/sheet.pdf",
            f"/payroll/entries/{entry.id}",
        ):
            assert web.get(url).status_code == 403, (role, url)
        assert "Lương Bí Mật" not in web.get("/").text
    web.login(ROLE_EMAILS[OWNER])
    assert web.get(f"/payroll/runs/{run.id}").status_code == 200
    r = web.get(f"/payroll/entries/{entry.id}", follow_redirects=False)
    assert (
        r.status_code == 303
        and r.headers["location"] == f"/payroll/runs/{run.id}"
    )
