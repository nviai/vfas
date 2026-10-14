"""Auth and session security tests (fixed review A and D findings)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import BusinessError
from app.core.security import validate_password_strength
from tests.conftest import PASSWORD, ROLE_EMAILS, WebClient


def _login(web, email, password, headers=None):
    return web.client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": web.csrf},
        headers=headers or {},
        follow_redirects=False,
    )


def test_spoofed_forwarded_for_does_not_bypass_throttle(web, users):
    web.get("/login")
    for i in range(8):
        r = _login(
            web,
            ROLE_EMAILS["accountant"],
            "sai-mat-khau",
            {"X-Forwarded-For": f"10.0.0.{i}"},
        )
        assert r.status_code == 400
    r = _login(
        web,
        ROLE_EMAILS["accountant"],
        PASSWORD,
        {"X-Forwarded-For": "10.9.9.9"},
    )
    assert r.status_code == 400 and "quá nhiều" in r.text


def test_per_ip_limit_across_emails(web, users):
    web.get("/login")
    for i in range(30):
        _login(web, f"khong-co-{i}@x.vn", "sai")
    r = _login(web, ROLE_EMAILS["viewer"], PASSWORD)
    assert r.status_code == 400 and "quá nhiều" in r.text


def test_bearer_token_ignored_on_web_routes(web, db, ctx, users):
    from app.features.auth.service import create_api_token

    _tok, raw = create_api_token(db, ctx, "đọc", ["read"])
    c = web.client
    c.cookies.clear()
    r = c.post(
        "/me",
        data={"full_name": "Bị đổi"},
        headers={"Authorization": f"Bearer {raw}"},
        follow_redirects=False,
    )
    # must not be treated as a logged-in user
    assert r.status_code in (303, 401, 403)
    from app.features.auth.service import get_user_by_email

    db.expire_all()
    assert (
        get_user_by_email(db, ROLE_EMAILS["chief_accountant"]).full_name
        != "Bị đổi"
    )
    assert (
        c.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {raw}"}
        ).status_code
        == 200
    )


def test_signup_rate_limited(web, settings):
    web.get("/setup")
    web.post(
        "/setup",
        {
            "setup_token": "test-setup-token",
            "full_name": "Admin",
            "email": "admin@host.vn",
            "password": PASSWORD,
            "company_name": "Host",
        },
    )
    web.client.cookies.clear()
    web.csrf = None
    codes = []
    for i in range(settings.signup_per_ip_hour + 1):
        web.client.cookies.clear()
        web.csrf = None
        web.get("/signup")
        codes.append(
            web.post(
                "/signup",
                {
                    "full_name": f"Người {i}",
                    "email": f"u{i}@spam.vn",
                    "password": PASSWORD,
                    "company_name": f"Spam {i}",
                },
                follow_redirects=False,
            ).status_code
        )
    assert (
        codes[: settings.signup_per_ip_hour]
        == [303] * settings.signup_per_ip_hour
    )
    assert codes[-1] == 429


def test_company_creation_limit(web, settings, users):
    settings.max_companies_per_user = 1
    web.login(ROLE_EMAILS["accountant"])
    assert (
        web.post(
            "/companies/new", {"name": "Công ty phụ 1"}, follow_redirects=False
        ).status_code
        == 303
    )
    assert (
        web.post(
            "/companies/new", {"name": "Công ty phụ 2"}, follow_redirects=False
        ).status_code
        == 403
    )


def test_https_cookies_and_headers(app, users):
    with TestClient(app, base_url="https://testserver") as c:
        w = WebClient(c)
        w.get("/login")
        r = _login(w, ROLE_EMAILS["owner"], PASSWORD)
        assert r.status_code == 303
        cookie = [
            h
            for h in r.headers.get_list("set-cookie")
            if h.startswith("vfas_session=")
        ][0]
        assert "Secure" in cookie and "HttpOnly" in cookie
        page = c.get("/")
        assert page.headers["strict-transport-security"].startswith("max-age=")
        assert "camera=()" in page.headers["permissions-policy"]


def test_http_cookie_not_secure_without_tls(web, users):
    web.get("/login")
    r = _login(web, ROLE_EMAILS["owner"], PASSWORD)
    cookie = [
        h
        for h in r.headers.get_list("set-cookie")
        if h.startswith("vfas_session=")
    ][0]
    assert (
        "Secure" not in cookie and "strict-transport-security" not in r.headers
    )


def test_password_policy():
    assert validate_password_strength("12345678")
    assert validate_password_strength("matkhau123")
    assert validate_password_strength("a" * 129)
    assert validate_password_strength("aaaaaaaaaa")
    assert validate_password_strength("ketoanvien1", "ketoanvien@x.vn")
    assert validate_password_strength("Ketoan#2026") is None


def test_change_password_brute_force_blocked(web, db, users):
    from app.features.auth.service import change_password, get_user_by_email

    u = get_user_by_email(db, ROLE_EMAILS["accountant"])
    for _ in range(8):
        with pytest.raises(BusinessError):
            change_password(db, u, "sai", "MatKhauMoi#99", ip="1.2.3.4")
    with pytest.raises(BusinessError) as ei:
        change_password(db, u, PASSWORD, "MatKhauMoi#99", ip="1.2.3.4")
    assert "quá nhiều" in ei.value.message


def test_idle_session_expires(web, db, settings, users):
    from app.features.auth.models import UserSession

    web.login(ROLE_EMAILS["owner"])
    assert web.get("/", follow_redirects=False).status_code == 200
    old = datetime.now(timezone.utc) - timedelta(
        minutes=settings.session_idle_minutes + 5
    )
    for s in db.scalars(select(UserSession)):
        s.last_seen_at = old
    db.commit()
    r = web.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_validation_error_does_not_echo_input(web, db, ctx, users):
    from app.features.auth.service import create_api_token

    _tok, raw = create_api_token(db, ctx, "đọc", ["read"])
    r = web.client.get(
        "/api/v1/entries?limit=<script>xyz",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert (
        r.status_code == 422
        and "<script>xyz" not in r.text
        and "xyz" not in r.text
    )


def test_access_log_redacts_secrets():
    import logging

    from app.main import _RedactSecrets

    rec = logging.LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        '%s - "%s %s"',
        ("1.1.1.1", "GET", "/invite/SECRETTOKEN"),
        None,
    )
    _RedactSecrets().filter(rec)
    assert "SECRETTOKEN" not in str(rec.args)


def test_switch_company_rotates_csrf(web, db, users, company):
    """Switching company in another tab invalidates open forms.

    A form opened in company A (old CSRF token) cannot be submitted
    after switching to company B in another tab.
    """
    from app.features.auth.service import get_user_by_email
    from app.features.org.service import add_member_direct
    from tests.conftest import make_company, make_user

    owner_b = make_user(db, "chu.b@khac.vn")
    company_b = make_company(
        db, owner_b, name="Công ty B", tax_code="0101010101"
    )
    add_member_direct(
        db,
        company_b.id,
        get_user_by_email(db, ROLE_EMAILS["chief_accountant"]),
        "chief_accountant",
    )
    web.login(ROLE_EMAILS["chief_accountant"])
    old_csrf = web.csrf
    r = web.post(
        "/switch-company",
        {"company_id": str(company_b.id)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = web.client.post(
        "/partners",
        data={"name": "Ghi nhầm công ty", "csrf_token": old_csrf},
        follow_redirects=False,
    )
    assert r.status_code == 403
    from app.features.partners.models import Partner

    assert (
        db.scalar(select(Partner).where(Partner.name == "Ghi nhầm công ty"))
        is None
    )
