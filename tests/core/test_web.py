"""Core web pages and flows.

First-run setup, login, sign-up, invitations, company switching,
permissions, core pages.
"""

import re

from app.core.registry import ACCOUNTANT, CASHIER, CHIEF, OWNER, VIEWER
from tests.conftest import PASSWORD, ROLE_EMAILS, make_company, make_user

CORE_PAGES = [
    "/",
    "/accounts",
    "/partners",
    "/partners/new",
    "/settings",
    "/settings?tab=signers",
    "/settings?tab=accounting",
    "/settings?tab=numbering",
    "/members",
    "/me",
    "/audit",
    "/checks",
    "/about",
    "/select-company",
]


def test_first_run_setup_creates_admin_and_company(web):
    r = web.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    r = web.get("/login", follow_redirects=False)
    assert r.headers["location"] == "/setup"
    web.get("/setup")
    # Missing/wrong setup token -> rejected (prevents taking over a new
    # server)
    r = web.post(
        "/setup",
        {
            "full_name": "Kẻ lạ",
            "email": "x@x.vn",
            "password": PASSWORD,
            "company_name": "X",
            "setup_token": "sai",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400 and "Mã cài đặt" in r.text
    r = web.post(
        "/setup",
        {
            "setup_token": "test-setup-token",
            "full_name": "Giám đốc",
            "email": "Boss@NVIAI.vn",
            "password": PASSWORD,
            "password2": PASSWORD,
            "company_name": "NVIAI",
            "tax_code": "0000000001",
            "start_date": "12/01/2023",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    r = web.get("/")
    assert r.status_code == 200 and "NVIAI" in r.text
    r = web.get("/accounts?q=1111")
    assert "Tiền Việt Nam" in r.text
    # setup runs only once
    assert (
        web.get("/setup", follow_redirects=False).headers["location"]
        == "/login"
    )
    # the user who ran setup is a superadmin
    assert web.get("/admin").status_code == 200


def test_login_failure_and_throttle(web, users):
    web.get("/login")
    for _ in range(8):
        r = web.post(
            "/login",
            {"email": ROLE_EMAILS[CHIEF], "password": "sai"},
            follow_redirects=False,
        )
        assert r.status_code == 400
    r = web.post(
        "/login",
        {"email": ROLE_EMAILS[CHIEF], "password": PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 400 and "quá nhiều" in r.text


def test_core_pages_render_for_owner_vi_en(web, users):
    web.login(ROLE_EMAILS[OWNER])
    for url in CORE_PAGES:
        assert web.get(url).status_code == 200, url
    web.get("/lang/en")
    for url in CORE_PAGES:
        r = web.get(url)
        assert r.status_code == 200, url


def test_permissions(web, users):
    web.login(ROLE_EMAILS[VIEWER])
    assert web.get("/accounts").status_code == 200
    for url in ("/settings", "/members", "/partners/new"):
        assert web.get(url).status_code == 403, url
    assert web.get("/admin").status_code == 403
    web.login(ROLE_EMAILS[CASHIER])
    assert web.get("/audit").status_code == 403
    web.login(ROLE_EMAILS[ACCOUNTANT])
    r = web.post(
        "/partners",
        {"name": "Công ty ABC", "is_customer": "1", "tax_code": "0101243150"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Công ty ABC" in web.get("/partners?role=customer").text


def test_invitation_flow(web, users, db):
    web.login(ROLE_EMAILS[OWNER])
    web.get("/members")
    r = web.post(
        "/members/invite",
        {"email": "moi@nviai.vn", "role": "accountant"},
        follow_redirects=False,
    )
    # the token is not in the URL
    assert r.status_code == 303 and "link=" not in r.headers["location"]
    page = web.get("/members").text
    m = re.search(r'value="(https?://[^"]+/invite/[^"]+)"', page)
    assert m, "invitation link not found"
    token_path = "/invite/" + m.group(1).rsplit("/invite/", 1)[1]
    # the link is shown only once
    assert "/invite/" not in web.get("/members").text
    web.client.cookies.clear()
    web.csrf = None
    r = web.get(token_path)
    assert r.status_code == 200 and "moi@nviai.vn" in r.text
    r = web.post(
        token_path,
        {"full_name": "Người mới", "password": PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = web.get("/")
    assert r.status_code == 200
    # An accountant cannot manage members
    assert web.get("/members").status_code == 403
    # Invitations are single-use
    assert web.get(token_path).status_code == 404


def test_switch_company_and_isolation(web, db, users, company):
    owner2 = make_user(db, "chu2@khac.vn")
    c2 = make_company(
        db, owner2, name="Công ty Thứ Hai", tax_code="0101010101"
    )
    from app.features.auth.service import get_user_by_email
    from app.features.org.service import add_member_direct

    add_member_direct(
        db, c2.id, get_user_by_email(db, ROLE_EMAILS[CHIEF]), "viewer"
    )
    web.login(ROLE_EMAILS[CHIEF])
    web.post("/partners", {"name": "Khách của công ty 1", "is_customer": "1"})
    r = web.get("/select-company")
    assert "Công ty Thứ Hai" in r.text
    r = web.post(
        "/switch-company", {"company_id": str(c2.id)}, follow_redirects=False
    )
    assert r.status_code == 303
    assert "Khách của công ty 1" not in web.get("/partners").text
    assert web.get("/lookup/partners?q=Khách").json() == []
    # the role in company 2 is viewer
    assert web.get("/settings").status_code == 403
    # Cannot switch to a company the user is not a member of
    owner3 = make_user(db, "chu3@khac.vn")
    c3 = make_company(db, owner3, name="Công ty Ba", tax_code="0102030405")
    assert (
        web.post("/switch-company", {"company_id": str(c3.id)}).status_code
        == 404
    )


def test_signup_creates_owner_company(web):
    web.get("/setup")
    web.post(
        "/setup",
        {
            "setup_token": "test-setup-token",
            "full_name": "Admin",
            "email": "admin@host.vn",
            "password": PASSWORD,
            "password2": PASSWORD,
            "company_name": "Host",
        },
    )
    web.client.cookies.clear()
    web.csrf = None
    web.get("/signup")
    r = web.post(
        "/signup",
        {
            "full_name": "Kế toán dịch vụ",
            "email": "kt@dichvu.vn",
            "password": PASSWORD,
            "password2": PASSWORD,
            "company_name": "Khách hàng A",
            "tax_code": "12345",
        },
        follow_redirects=False,
    )
    assert r.status_code == 400 and "Mã số thuế" in r.text
    r = web.post(
        "/signup",
        {
            "full_name": "Kế toán dịch vụ",
            "email": "kt@dichvu.vn",
            "password": PASSWORD,
            "password2": PASSWORD,
            "company_name": "Khách hàng A",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert web.get("/members").status_code == 200
    assert web.get("/admin").status_code == 403
    r = web.get("/companies/new")
    assert r.status_code == 200
    r = web.post(
        "/companies/new",
        {"name": "Khách hàng B", "tax_code": "0312345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = web.get("/select-company").text
    assert "Khách hàng A" in page and "Khách hàng B" in page
    assert re.search(r"Khách hàng B", web.get("/").text)


def test_dashboard_numbers(web, db, ctx, users):
    from datetime import date

    from tests.conftest import post

    post(db, ctx, date(2026, 1, 5), "1111", "4111", 50_000_000, "PT")
    post(db, ctx, date(2026, 2, 10), "1111", "5113", 20_000_000, "PT")
    post(db, ctx, date(2026, 3, 1), "6422", "1111", 7_000_000, "PC")
    web.login(ROLE_EMAILS[CHIEF])
    r = web.get("/")
    assert r.status_code == 200
    assert "63.000.000" in r.text  # cash
    assert "13.000.000" in r.text  # accumulated profit
    web.login(ROLE_EMAILS[CASHIER])
    r = web.get("/")
    assert r.status_code == 200 and "Lợi nhuận" not in r.text
    from app.core.registry import registry

    if "cash.view" in registry.permissions:  # cash module is loaded
        assert "63.000.000" in r.text
