"""Cross-module data safety.

1. Scan EVERY GET route with an id parameter: a user of company A (no
   data) tries ids of company B (full demo data) -> no route returns
   B's content (404/403/redirect, or 200 without B's data).
2. Some write actions using B's ids are rejected.
3. Two threads posting at the same time cannot overdraw the cash fund
   (posting is serialized per company).
"""

from __future__ import annotations

import re
import threading
from datetime import date

import pytest
from sqlalchemy import select
from starlette.routing import Route

from app.core.registry import OWNER
from app.core.security import build_current_user
from app.core.tenancy import Ctx
from tests.conftest import PASSWORD, make_company, make_user

INT_PARAM = re.compile(r"\{(\w+)\}")
SKIP_PREFIX = (
    "/static",
    "/ui-kit",
    "/api/docs",
    "/api/openapi",
    "/lang/",
    "/theme/",
    "/invite/",
)


@pytest.fixture()
def two_companies(db):
    from app.demo import build_demo

    owner_b = make_user(db, "chu.b@demo.vn", "Chủ B")
    comp_b = make_company(
        db, owner_b, name="CÔNG TY BÊ TÔNG BÍ MẬT B", tax_code="0109999999"
    )
    ctx_b = Ctx(
        company_id=comp_b.id, user=build_current_user(db, owner_b, comp_b.id)
    )
    build_demo(db, ctx_b)
    owner_a = make_user(db, "chu.a@demo.vn", "Chủ A")
    comp_a = make_company(
        db, owner_a, name="Công ty A rỗng", tax_code="0108888888"
    )
    return comp_a, comp_b


def _get_paths(app) -> list[str]:
    """Return every GET path.

    Newer FastAPI wraps sub-routers in _IncludedRouter, so read
    original_router.routes.
    """
    out = []

    def walk(routes, prefix=""):
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:
                walk(
                    inner.routes, prefix + (getattr(inner, "prefix", "") or "")
                )
            elif isinstance(r, Route) and "GET" in (r.methods or ()):
                out.append(
                    prefix + r.path
                    if not r.path.startswith(prefix)
                    else r.path
                )

    walk(app.routes)
    return sorted(set(out))


def _markers(db, comp_b) -> list[str]:
    from app.features.partners.models import Partner

    names = [
        p.name
        for p in db.scalars(
            select(Partner).where(Partner.company_id == comp_b.id)
        ).all()
    ]
    return [comp_b.name, "0109999999"] + [n for n in names if len(n) > 8]


def test_no_cross_company_read_via_url(web, db, two_companies, app):
    comp_a, comp_b = two_companies
    markers = _markers(db, comp_b)
    web.login("chu.a@demo.vn")
    ids = [1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 300, 400]
    leaks = []
    checked = 0
    for path in _get_paths(app):
        params = INT_PARAM.findall(path)
        if len(params) != 1 or path.startswith(SKIP_PREFIX):
            continue
        for i in ids:
            url = path.replace("{" + params[0] + "}", str(i))
            r = web.client.get(url, follow_redirects=False)
            checked += 1
            if r.status_code >= 500:
                leaks.append((url, r.status_code, "server error"))
                continue
            if r.status_code != 200:
                continue
            ctype = r.headers.get("content-type", "")
            if (
                "pdf" in ctype
                or "spreadsheet" in ctype
                or "octet-stream" in ctype
            ):
                leaks.append((url, 200, ctype))
                continue
            body = r.text
            hit = next((m for m in markers if m in body), None)
            if hit:
                leaks.append((url, 200, hit))
    assert checked > 200
    assert not leaks, leaks[:20]
    # Positive control: company B's owner opening the same URLs sees B's
    # data (the leak detector really works).
    from app.features.partners.models import Partner

    pid = db.scalars(
        select(Partner.id).where(Partner.company_id == comp_b.id)
    ).first()
    web.login("chu.b@demo.vn")
    seen = sum(
        1
        for path in _get_paths(app)
        if len(INT_PARAM.findall(path)) == 1
        and not path.startswith(SKIP_PREFIX)
        for i in (pid, 1, 2, 3)
        if any(
            m
            in web.client.get(
                path.replace("{" + INT_PARAM.findall(path)[0] + "}", str(i)),
                follow_redirects=False,
            ).text
            for m in markers
        )
    )
    assert seen >= 10, seen


def test_no_cross_company_write(web, db, two_companies):
    from app.features.journal.models import JournalEntry
    from app.features.partners.models import Partner

    _comp_a, comp_b = two_companies
    e = db.scalars(
        select(JournalEntry).where(
            JournalEntry.company_id == comp_b.id,
            JournalEntry.status == "posted",
        )
    ).first()
    draft = db.scalars(
        select(JournalEntry).where(
            JournalEntry.company_id == comp_b.id,
            JournalEntry.status == "draft",
        )
    ).first()
    p = db.scalars(
        select(Partner).where(Partner.company_id == comp_b.id)
    ).first()
    web.login("chu.a@demo.vn")
    for url, data in [
        (f"/entries/{e.id}/cancel", {"reason": "x"}),
        (f"/entries/{draft.id}/post", {}),
        (f"/entries/{draft.id}/delete", {}),
        (f"/entries/{e.id}/info", {"description": "hack"}),
        (f"/partners/{p.id}", {"name": "hack"}),
    ]:
        r = web.post(url, data, follow_redirects=False)
        assert r.status_code in (403, 404), (url, r.status_code)
    db.expire_all()
    assert (
        db.get(JournalEntry, e.id).status == "posted"
        and db.get(Partner, p.id).name != "hack"
    )
    assert db.get(JournalEntry, draft.id).status == "draft"


def test_concurrent_payments_never_overdraw(db, company, ctx):
    """Balance 10M; 6 threads post 3M payments at once.

    At most 3 payments get posted and the balance never goes negative.
    """
    from app.core.db import SessionLocal
    from app.features.journal import queries
    from app.features.journal import service as js
    from tests.conftest import post

    post(db, ctx, date(2024, 1, 2), "1111", "4111", 10_000_000, "PT")
    drafts = [
        js.create_entry(
            db,
            ctx,
            js.EntryIn(
                "PC",
                date(2024, 1, 10),
                [js.LineIn("6422", "1111", 3_000_000)],
                description=f"Chi {i}",
            ),
        ).id
        for i in range(6)
    ]
    results: list[str] = []
    barrier = threading.Barrier(len(drafts))

    def worker(entry_id: int) -> None:
        s = SessionLocal()
        try:
            barrier.wait()
            e = s.get(js.JournalEntry, entry_id)
            js.post_entry(s, Ctx(company_id=company.id, user=ctx.user), e)
            results.append("ok")
        except Exception as ex:  # noqa: BLE001
            results.append(type(ex).__name__)
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in drafts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert results.count("ok") == 3, results
    assert all(r in ("ok", "NegativeBalanceError") for r in results), results
    db.expire_all()
    assert queries.balance(db, company.id, "1111") == 1_000_000
    assert queries.first_negative(db, company.id, "1111") is None


_ = (OWNER, PASSWORD)
