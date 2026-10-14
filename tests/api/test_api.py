"""Public API /api/v1.

Token auth, scopes, company isolation, OpenAI tools, chat completions.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.registry import (
    ACCOUNTANT,
    CHIEF,
    OWNER,
    VIEWER,
    ai_tool,
    registry,
)
from app.core.security import build_current_user
from app.core.tenancy import Ctx
from app.features.auth.service import create_api_token
from app.features.journal.models import JournalEntry
from tests.ai.fake_llm import enable_ai, parse_sse
from tests.conftest import ROLE_EMAILS, make_company, make_user, post


def token(db, ctx, scopes=("read",)):
    return create_api_token(db, ctx, "test", list(scopes))[1]


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def api(web):
    return web.client


def test_auth_required(api, db, ctxs):
    assert api.get("/api/v1/me").status_code == 401
    r = api.get("/api/v1/me", headers=auth("vfas_khong-dung"))
    assert (
        r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key"
    )
    tok = token(db, ctxs[CHIEF])
    me = api.get("/api/v1/me", headers=auth(tok)).json()
    assert (
        me["company"]["tax_code"] == "0000000001"
        and me["role"] == "chief_accountant"
        and me["scopes"] == ["read"]
    )


def test_session_cookie_is_not_api_auth(web, users):
    web.login(ROLE_EMAILS[OWNER])
    assert web.get("/api/v1/me").status_code == 401


def test_read_endpoints(api, db, ctxs):
    ctx = ctxs[CHIEF]
    post(db, ctx, date(2024, 1, 2), "1111", "4111", 9_000_000, "PT", "Góp vốn")
    post(
        db,
        ctx,
        date(2024, 1, 3),
        "6422",
        "1111",
        1_000_000,
        "PC",
        "Văn phòng phẩm",
    )
    h = auth(token(db, ctx))
    accounts = api.get("/api/v1/accounts?q=111", headers=h).json()
    assert any(a["code"] == "1111" for a in accounts)
    entries = api.get("/api/v1/entries?doc_type=PC", headers=h).json()["data"]
    assert len(entries) == 1 and entries[0]["number"] == "PC2024-0001"
    detail = api.get(f"/api/v1/entries/{entries[0]['id']}", headers=h).json()
    assert detail["lines"][0]["debit_account"] == "6422"
    bal = api.get(
        "/api/v1/reports/balance?account=1111&date=2024-12-31", headers=h
    ).json()
    assert bal["balance"] == 8_000_000
    tb = api.get(
        "/api/v1/reports/trial-balance?start=2024-01-01&end=2024-12-31",
        headers=h,
    ).json()
    assert tb["total_debit"] == tb["total_credit"] == 10_000_000
    assert api.get("/api/v1/partners", headers=h).status_code == 200
    assert api.get("/api/v1/items", headers=h).status_code == 200


def test_scopes_and_drafts(api, db, ctxs, web):
    ctx = ctxs[CHIEF]
    body = {
        "doc_type": "PK",
        "date": "2024-02-01",
        "description": "Trích trước chi phí",
        "lines": [
            {
                "debit_account": "6422",
                "credit_account": "335",
                "amount": 1500000,
            }
        ],
    }
    ro = auth(token(db, ctx, ["read"]))
    assert (
        api.post("/api/v1/entries", json=body, headers=ro).status_code == 403
    )
    assert (
        api.post(
            "/api/v1/partners", json={"name": "KH API"}, headers=ro
        ).status_code
        == 403
    )
    rw = auth(token(db, ctx, ["read", "write_draft"]))
    r = api.post("/api/v1/entries", json=body, headers=rw)
    assert r.status_code == 201, r.text
    e = r.json()
    assert (
        e["status"] == "draft"
        and e["number"] is None
        and e["lines"][0]["amount"] == 1500000
    )
    assert db.get(JournalEntry, e["id"]).source == "api"
    # Tokens cannot post vouchers (not even through web routes)
    assert api.post(f"/entries/{e['id']}/post", headers=rw).status_code == 403
    assert db.get(JournalEntry, e["id"]).status == "draft"
    r = api.post(
        "/api/v1/partners",
        json={"name": "KH API", "is_customer": True},
        headers=rw,
    )
    assert r.status_code == 201 and r.json()["code"].startswith("KH")
    bad = dict(
        body,
        lines=[{"debit_account": "111", "credit_account": "335", "amount": 1}],
    )
    r = api.post("/api/v1/entries", json=bad, headers=rw)
    assert r.status_code == 422 and "tổng hợp" in r.json()["error"]["message"]
    # Viewer: a write_draft token still cannot create drafts
    viewer_rw = auth(token(db, ctxs[VIEWER], ["read", "write_draft"]))
    assert (
        api.post("/api/v1/entries", json=body, headers=viewer_rw).status_code
        == 403
    )


def test_company_isolation(api, db, ctxs):
    owner2 = make_user(db, "chu2@khac.vn")
    c2 = make_company(db, owner2, name="Công ty khác", tax_code="0101010101")
    ctx2 = Ctx(company_id=c2.id, user=build_current_user(db, owner2, c2.id))
    other = post(db, ctx2, date(2024, 1, 2), "1111", "4111", 1_000, "PT")
    h = auth(token(db, ctxs[CHIEF]))
    assert api.get(f"/api/v1/entries/{other.id}", headers=h).status_code == 404
    assert api.get("/api/v1/entries", headers=h).json()["data"] == []
    assert (
        api.get("/api/v1/me", headers=auth(token(db, ctx2))).json()["company"][
            "name"
        ]
        == "Công ty khác"
    )


def test_tools_openai_format(api, db, ctxs):
    post(db, ctxs[CHIEF], date(2024, 1, 2), "1111", "4111", 3_000_000, "PT")
    h = auth(token(db, ctxs[CHIEF]))
    data = api.get("/api/v1/tools", headers=h).json()["data"]
    tool = next(
        t for t in data if t["function"]["name"] == "get_account_balance"
    )
    assert tool["type"] == "function" and tool["function"]["parameters"][
        "required"
    ] == ["account"]
    r = api.post(
        "/api/v1/tools/get_account_balance",
        json={"account": "1111"},
        headers=h,
    ).json()
    assert r["ok"] and r["result"]["balance"] == 3_000_000
    r = api.post(
        "/api/v1/tools/get_account_balance",
        json={"account": "1111", "la": 1},
        headers=h,
    ).json()
    assert not r["ok"] and "Tham số" in r["result"]["error"]
    assert (
        api.post("/api/v1/tools/khong_co", json={}, headers=h).status_code
        == 404
    )


def test_write_tool_requires_scope(api, db, ctxs):
    @ai_tool(
        name="test_api_write",
        description="x",
        perm="journal.create",
        write=True,
    )
    def _t(db, ctx):
        return {"summary": "ok"}

    try:
        ro = auth(token(db, ctxs[CHIEF], ["read"]))
        assert "test_api_write" not in {
            t["function"]["name"]
            for t in api.get("/api/v1/tools", headers=ro).json()["data"]
        }
        assert (
            api.post(
                "/api/v1/tools/test_api_write", json={}, headers=ro
            ).status_code
            == 403
        )
        rw = auth(token(db, ctxs[CHIEF], ["read", "write_draft"]))
        assert api.post(
            "/api/v1/tools/test_api_write", json={}, headers=rw
        ).json()["ok"]
        from app.core.audit import AuditLog

        log = db.scalars(
            select(AuditLog).where(AuditLog.action == "ai.tool")
        ).one()
        assert log.via == "api"
    finally:
        registry.ai_tools.pop("test_api_write", None)


def test_models_and_chat_completions(api, db, ctxs, fake_llm):
    h = auth(token(db, ctxs[CHIEF]))
    models = api.get("/api/v1/models", headers=h).json()
    assert (
        models["object"] == "list" and models["data"][0]["id"] == "vfas-agent"
    )
    req = {
        "model": "vfas-agent",
        "messages": [{"role": "user", "content": "Tồn quỹ?"}],
    }
    r = api.post("/api/v1/chat/completions", json=req, headers=h)
    assert (
        r.status_code == 403 and r.json()["error"]["code"] == "ai_unavailable"
    )
    enable_ai(db, ctxs[OWNER])
    post(db, ctxs[CHIEF], date(2024, 1, 2), "1111", "4111", 7_000_000, "PT")
    fake_llm.responses = [
        {"tool_calls": [("get_account_balance", {"account": "1111"})]},
        {"text": "Tồn quỹ là 7.000.000 đ."},
    ]
    body = api.post(
        "/api/v1/chat/completions",
        json={
            **req,
            "messages": [
                {"role": "system", "content": "Trả lời ngắn."},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Tồn quỹ?"}],
                },
            ],
        },
        headers=h,
    ).json()
    assert body["object"] == "chat.completion" and body["id"].startswith(
        "chatcmpl-"
    )
    choice = body["choices"][0]
    assert (
        choice["message"]["role"] == "assistant"
        and "7.000.000" in choice["message"]["content"]
    )
    assert (
        choice["finish_reason"] == "stop"
        and body["usage"]["total_tokens"] == 30
    )
    sys_msgs = [
        m["content"]
        for m in fake_llm.requests[0]["messages"]
        if m["role"] == "system"
    ]
    assert len(sys_msgs) == 2 and "Trả lời ngắn." in sys_msgs[1]
    # stream
    fake_llm.responses = [{"text": "Xin chào kế toán."}]
    r = api.post(
        "/api/v1/chat/completions",
        json={
            **req,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        headers=h,
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith(
        "text/event-stream"
    )
    events = parse_sse(r.text)
    assert events[-1][1] == {"raw": "[DONE]"}
    chunks = [d for _, d in events[:-1]]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if c["choices"]
    )
    assert text == "Xin chào kế toán."
    assert any(
        c["choices"] and c["choices"][0]["finish_reason"] == "stop"
        for c in chunks
    )
    assert chunks[-1]["usage"]["total_tokens"] == 15


def test_rate_limit(api, db, ctxs, monkeypatch):
    import app.features.api.router as api_router

    monkeypatch.setattr(api_router, "RATE_LIMIT", 2)
    h = auth(token(db, ctxs[CHIEF]))
    assert api.get("/api/v1/me", headers=h).status_code == 200
    assert api.get("/api/v1/me", headers=h).status_code == 200
    r = api.get("/api/v1/me", headers=h)
    assert r.status_code == 429 and "Quá nhiều" in r.json()["error"]["message"]


def test_openapi_only_lists_api(api):
    spec = api.get("/api/openapi.json").json()
    paths = set(spec["paths"])
    assert "/api/v1/chat/completions" in paths and "/api/v1/entries" in paths
    assert all(p.startswith("/api/v1/") for p in paths), paths
    assert api.get("/api/docs").status_code == 200


def test_token_management_page(web, db, users):
    web.login(ROLE_EMAILS[OWNER])
    assert web.get("/settings/api-tokens").status_code == 200
    r = web.post(
        "/settings/api-tokens",
        {"name": "n8n", "scopes": ["read", "write_draft"], "days": "30"},
    )
    assert (
        r.status_code == 200
        and "vfas_" in r.text
        and r.headers.get("cache-control") == "no-store"
    )
    raw = r.text.split('value="vfas_', 1)[1].split('"', 1)[0]
    assert web.client.get("/api/v1/me", headers=auth("vfas_" + raw)).json()[
        "scopes"
    ] == ["read", "write_draft"]
    from app.features.auth.models import ApiToken

    tok = db.scalars(select(ApiToken)).one()
    assert (
        web.post(
            f"/settings/api-tokens/{tok.id}/revoke", follow_redirects=False
        ).status_code
        == 303
    )
    assert (
        web.client.get("/api/v1/me", headers=auth("vfas_" + raw)).status_code
        == 401
    )
    web.login(ROLE_EMAILS[ACCOUNTANT])
    assert web.get("/settings/api-tokens").status_code == 403
