"""Email: verification after sign-up, password reset, invitations.

SMTP is replaced by an in-memory outbox (`mail.set_transport`).
"""

import json
import re

import pytest

from app.core import mail
from app.core.registry import OWNER
from tests.conftest import PASSWORD, ROLE_EMAILS

NEW_PASSWORD = "Moi#Matkhau2026"
LINK_RE = re.compile(
    r"https?://\S+?(/(?:verify-email|reset-password|invite)/\S+)"
)


@pytest.fixture()
def outbox(settings):
    sent = []
    settings.smtp_host = "smtp.test"
    settings.mail_from = "VFAS <no-reply@vfas.test>"
    mail.set_transport(sent.append)
    yield sent
    mail.set_transport(None)


def _flashed(page: str, text: str) -> bool:
    # Flash messages travel as JSON (non-ASCII escaped) to the toaster.
    return json.dumps(text)[1:-1] in page


def _link(msg) -> str:
    body = msg.get_body(("plain",)).get_content()
    m = LINK_RE.search(body)
    assert m, body
    return m.group(1)


def _signup(web, email="kt@dichvu.vn"):
    web.get("/signup")
    return web.post(
        "/signup",
        {
            "full_name": "Kế toán dịch vụ",
            "email": email,
            "password": PASSWORD,
            "password2": PASSWORD,
            "company_name": "Khách hàng A",
        },
        follow_redirects=False,
    )


def test_signup_requires_email_verification(web, users, outbox):
    r = _signup(web)
    assert r.status_code == 303
    # Every company page sends the user to the verification page
    r = web.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/verify-email"
    assert web.get("/members", follow_redirects=False).status_code == 303
    assert (
        web.get("/companies/new", follow_redirects=False).headers["location"]
        == "/verify-email"
    )
    page = web.get("/verify-email")
    assert page.status_code == 200 and "kt@dichvu.vn" in page.text
    assert len(outbox) == 1
    msg = outbox[0]
    assert msg["To"] == "kt@dichvu.vn"
    assert msg["From"] == "VFAS <no-reply@vfas.test>"
    assert "Xác minh" in msg["Subject"]
    assert msg.get_body(("html",)) is not None
    link = _link(msg)
    # A tampered token is rejected
    assert web.get(link[:-3] + "xyz").status_code == 404
    r = web.get(link, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert web.get("/members").status_code == 200
    assert web.get("/verify-email", follow_redirects=False).status_code == 303


def test_verification_resend_is_rate_limited(web, users, outbox):
    _signup(web)
    web.get("/verify-email")  # CSRF token of the new session
    for _ in range(2):
        web.post("/verify-email/resend")
    assert len(outbox) == 3
    r = web.post("/verify-email/resend")
    assert "quá nhiều" in r.text
    assert len(outbox) == 3


def test_signup_without_smtp_skips_verification(web, users):
    assert not mail.mail_enabled()
    r = _signup(web)
    assert r.status_code == 303
    assert web.get("/members").status_code == 200


def test_password_reset_flow(web, users, outbox, app):
    from fastapi.testclient import TestClient

    from tests.conftest import WebClient

    email = ROLE_EMAILS[OWNER]
    # Another browser stays signed in until the password changes
    with TestClient(app, base_url="http://testserver") as c:
        other = WebClient(c).login(email)
        assert other.get("/").status_code == 200

        web.client.cookies.clear()
        web.csrf = None
        assert "/forgot-password" in web.get("/login").text
        web.get("/forgot-password")
        r = web.post("/forgot-password", {"email": "khongco@nviai.vn"})
        assert r.status_code == 200 and "khongco@nviai.vn" in r.text
        assert outbox == []  # same answer, no email for unknown addresses
        r = web.post("/forgot-password", {"email": email.upper()})
        assert r.status_code == 200 and len(outbox) == 1
        link = _link(outbox[0])
        assert web.get(link).status_code == 200
        r = web.post(
            link,
            {"password": NEW_PASSWORD, "password2": "khac"},
            follow_redirects=False,
        )
        assert r.status_code == 400
        r = web.post(
            link,
            {"password": "12345678", "password2": "12345678"},
            follow_redirects=False,
        )
        assert r.status_code == 400
        r = web.post(
            link,
            {"password": NEW_PASSWORD, "password2": NEW_PASSWORD},
            follow_redirects=False,
        )
        assert r.status_code == 303 and r.headers["location"] == "/login"
        # Single use: the password fingerprint in the token changed
        assert web.get(link).status_code == 404
        # Every session was signed out
        assert other.get("/", follow_redirects=False).status_code == 303
    r = web.post(
        "/login",
        {"email": email, "password": PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 400
    web.login(email, NEW_PASSWORD)
    assert web.get("/").status_code == 200


def test_password_reset_rate_limit(web, users, outbox):
    web.get("/forgot-password")
    for _ in range(3):
        web.post("/forgot-password", {"email": ROLE_EMAILS[OWNER]})
    r = web.post("/forgot-password", {"email": ROLE_EMAILS[OWNER]})
    assert r.status_code == 429
    assert len(outbox) == 3


def test_forgot_password_needs_smtp(web, users):
    assert "/forgot-password" not in web.get("/login").text
    assert web.get("/forgot-password").status_code == 404


def test_invitation_email(web, users, outbox):
    web.login(ROLE_EMAILS[OWNER])
    web.get("/members")
    r = web.post(
        "/members/invite",
        {"email": "moi@nviai.vn", "role": "accountant"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(outbox) == 1 and outbox[0]["To"] == "moi@nviai.vn"
    assert "NVIAI" in outbox[0]["Subject"]
    first = _link(outbox[0])
    page = web.get("/members").text
    assert _flashed(page, "Đã gửi email mời") and first in page
    inv_id = re.search(r"/invitations/(\d+)/resend", page).group(1)
    web.post(f"/invitations/{inv_id}/resend", follow_redirects=False)
    assert len(outbox) == 2
    second = _link(outbox[1])
    assert second != first
    web.client.cookies.clear()
    web.csrf = None
    assert web.get(first).status_code == 404
    r = web.post(
        second,
        {"full_name": "Người mới", "password": PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert web.get("/").status_code == 200


def test_invitation_email_failure_keeps_link(web, users, outbox):
    def broken(_msg):
        raise OSError("connection refused")

    mail.set_transport(broken)
    web.login(ROLE_EMAILS[OWNER])
    web.get("/members")
    web.post(
        "/members/invite",
        {"email": "moi@nviai.vn", "role": "viewer"},
        follow_redirects=False,
    )
    page = web.get("/members").text
    assert _flashed(page, "Không gửi được email mời")
    assert "/invite/" in page


def test_messages_reject_header_injection(settings, outbox):
    assert mail.valid_email("ke.toan+1@cong-ty.com.vn")
    for bad in ("a@b", "a b@c.vn", "a@b.vn\r\nBcc: x@y.vn", "@b.vn", ""):
        assert not mail.valid_email(bad)
    with pytest.raises(mail.MailError):
        mail.send(mail.Mail(to="a@b.vn\nBcc: x@y.vn", subject="x", text="y"))
    assert outbox == []


def test_cli_send_test_email(engine, outbox, capsys):
    from app.cli import main

    assert main(["send-test-email", "--to", "admin@nviai.vn"]) == 0
    assert outbox[0]["To"] == "admin@nviai.vn"
    assert "SMTP" in outbox[0].get_body(("plain",)).get_content()


def test_smtp_transport_uses_starttls_and_login(settings, monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user, password))

        def send_message(self, msg):
            calls.append(("send", msg["To"]))

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings.smtp_host = "smtp.example.com"
    settings.smtp_username = "no-reply@example.com"
    settings.smtp_password = "app-password"
    mail.set_transport(None)
    mail.send(mail.Mail(to="a@b.vn", subject="Thử", text="Nội dung"))
    assert ("connect", "smtp.example.com", 587, 15) in calls
    assert calls.index(("starttls",)) < calls.index(
        ("login", "no-reply@example.com", "app-password")
    )
    assert calls[-1] == ("send", "a@b.vn")
