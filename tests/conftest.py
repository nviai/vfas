"""Fixtures shared by all test modules.

- `company`: the NVIAI company, bookkeeping from 12/01/2023, with the
  full TT133 chart of accounts.
- `users`: dict role -> CurrentUser (owner, chief_accountant,
  accountant, cashier, viewer) of `company`.
- `ctxs`: dict role -> Ctx; `ctx` = the chief accountant's Ctx.
- `post(db, ctx, d, debit, credit, amount, ...)`: create and post a
  one-line voucher.
- `web`: TestClient with CSRF handling; `web.login(email)`.

Run on PostgreSQL::

    TEST_DATABASE_URL=postgresql+psycopg://user:pass@host/db pytest
"""

from __future__ import annotations

import os
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, set_settings
from app.core.db import SessionLocal, configure_engine
from app.core.migrate import upgrade_db
from app.core.registry import (
    ACCOUNTANT,
    CASHIER,
    CHIEF,
    OWNER,
    VIEWER,
    load_features,
)
from app.core.security import build_current_user
from app.core.tenancy import Ctx

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
# Parallel development phase: a half-written feature module that fails
# to load is skipped instead of breaking the tests of other modules.
os.environ.setdefault("VFAS_TOLERANT_FEATURES", "0")
PASSWORD = "Ketoan#2026"
ROLE_EMAILS = {
    OWNER: "giamdoc@nviai.vn",
    CHIEF: "ktt@nviai.vn",
    ACCOUNTANT: "ketoan@nviai.vn",
    CASHIER: "thuquy@nviai.vn",
    VIEWER: "kiemtoan@nviai.vn",
}


@pytest.fixture()
def settings(tmp_path):
    s = Settings(
        data_dir=tmp_path / "data",
        secret_key="test-secret-0123456789-abcdefghijklmnop",
        database_url=TEST_DATABASE_URL,
        allow_signup=True,
        setup_token="test-setup-token",
    )
    s.ensure_dirs()
    set_settings(s)
    return s


def _reset_postgres(url: str) -> None:
    from sqlalchemy import create_engine, text

    eng = create_engine(url)
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    eng.dispose()


@pytest.fixture()
def engine(settings):
    if settings.db_url.startswith("postgresql"):
        _reset_postgres(settings.db_url)
    eng = configure_engine(settings.db_url)
    upgrade_db(settings.db_url)
    load_features()
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = SessionLocal()
    yield session
    session.close()


def make_user(
    db, email: str, full_name: str | None = None, *, superadmin: bool = False
):
    from app.features.auth.service import create_user

    return create_user(
        db,
        email,
        PASSWORD,
        full_name or email.split("@")[0].title(),
        is_superadmin=superadmin,
    )


def make_company(
    db,
    owner,
    name: str = "CÔNG TY TNHH CÔNG NGHỆ THÔNG MINH NVIAI",
    tax_code: str = "0000000001",
    start: date = date(2023, 1, 12),
    **kw,
):
    from app.features.org.service import CompanyInput, create_company

    c = create_company(
        db,
        owner,
        CompanyInput(
            name=name,
            tax_code=tax_code,
            start_date=start,
            address=kw.pop("address", "TP. Hồ Chí Minh"),
            **kw,
        ),
    )
    c.legal_rep = "Nguyễn Văn Giám"
    c.chief_accountant = "Trần Thị Kế"
    c.cashier = "Lê Thị Quỹ"
    c.preparer = "Phạm Văn Lập"
    db.commit()
    return c


def add_member(db, company, email: str, role: str):
    from app.features.org.service import add_member_direct

    u = make_user(db, email)
    add_member_direct(db, company.id, u, role)
    return u


def as_current(db, user, company):
    return build_current_user(db, user, company.id)


@pytest.fixture()
def company(db):
    owner = make_user(db, ROLE_EMAILS[OWNER], "Nguyễn Văn Giám")
    return make_company(db, owner)


@pytest.fixture()
def users(db, company):
    from app.features.auth.service import get_user_by_email

    out = {
        OWNER: as_current(
            db, get_user_by_email(db, ROLE_EMAILS[OWNER]), company
        )
    }
    for role in (CHIEF, ACCOUNTANT, CASHIER, VIEWER):
        out[role] = as_current(
            db, add_member(db, company, ROLE_EMAILS[role], role), company
        )
    return out


@pytest.fixture()
def ctxs(users, company):
    return {
        role: Ctx(company_id=company.id, user=u) for role, u in users.items()
    }


@pytest.fixture()
def ctx(ctxs):
    return ctxs[CHIEF]


def post(
    db,
    ctx,
    d: date,
    debit: str,
    credit: str,
    amount: int,
    doc_type: str = "PK",
    description: str = "Test",
    **line_kw,
):
    """Create and post a voucher with a single journal line."""
    from app.features.journal import service as js

    entry_kw = {
        k: line_kw.pop(k)
        for k in ("partner_id", "contact_name", "doc_date", "ref_no")
        if k in line_kw
    }
    inp = js.EntryIn(
        doc_type=doc_type,
        date=d,
        description=description,
        lines=[
            js.LineIn(
                debit_account=debit,
                credit_account=credit,
                amount=amount,
                **line_kw,
            )
        ],
        **entry_kw,
    )
    return js.create_entry(db, ctx, inp, post=True)


# ---------------------------------------------------------------- web

CSRF_RE = re.compile(r'name="csrf_token"\s+value="([^"]+)"')
CSRF_META_RE = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


def csrf_from(html: str) -> str | None:
    m = CSRF_RE.search(html) or CSRF_META_RE.search(html)
    return m.group(1) if m else None


class WebClient:
    """TestClient that carries the current CSRF token."""

    def __init__(self, client: TestClient):
        self.client = client
        self.csrf = None

    def _grab(self, r):
        if r.headers.get("content-type", "").startswith("text/html"):
            token = csrf_from(r.text)
            if token:
                self.csrf = token
        return r

    def get(self, url, **kw):
        return self._grab(self.client.get(url, **kw))

    def post(self, url, data=None, files=None, **kw):
        if self.csrf is None:
            self.get("/login")
        data = dict(data or {}) if not isinstance(data, list) else list(data)
        if isinstance(data, dict):
            data.setdefault("csrf_token", self.csrf)
        else:
            data.append(("csrf_token", self.csrf))
        return self._grab(self.client.post(url, data=data, files=files, **kw))

    def login(self, email, password=PASSWORD):
        self.client.cookies.clear()
        self.csrf = None
        self.get("/login")
        r = self.post(
            "/login",
            {"email": email, "password": password},
            follow_redirects=False,
        )
        assert r.status_code == 303, r.text[:500]
        self.get("/")
        return self


@pytest.fixture()
def app(engine, settings):
    from app.main import create_app

    return create_app(settings)


@pytest.fixture()
def web(app):
    with TestClient(app, base_url="http://testserver") as c:
        yield WebClient(c)
