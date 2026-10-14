"""AI assistant on the web.

Settings, SSE chat per CONTRACTS section 3, conversations, limits,
SSRF protection.
"""

import json
from datetime import date

from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.registry import ACCOUNTANT, CHIEF, OWNER
from app.features.ai.models import AiConversation, AiMessage
from app.features.org.models import Company
from tests.ai.fake_llm import enable_ai, parse_sse
from tests.conftest import ROLE_EMAILS, post


def _chat(web, content, conversation_id=None):
    data = {"content": content}
    if conversation_id:
        data["conversation_id"] = str(conversation_id)
    r = web.post("/ai/chat", data)
    assert r.status_code == 200 and r.headers["content-type"].startswith(
        "text/event-stream"
    )
    return parse_sse(r.text)


def test_chat_sse_flow_and_history(web, db, ctxs, fake_llm):
    enable_ai(db, ctxs[OWNER])
    post(db, ctxs[CHIEF], date(2024, 3, 1), "1111", "4111", 5_000_000, "PT")
    web.login(ROLE_EMAILS[CHIEF])
    fake_llm.responses = [
        {"tool_calls": [("get_account_balance", {"account": "1111"})]},
        {"text": "Tồn quỹ: **5.000.000 đ**"},
    ]
    events = _chat(web, "Tồn quỹ?")
    kinds = [e for e, _ in events]
    assert kinds[0] == "meta" and kinds[-1] == "done" and "token" in kinds
    tool_events = [d for e, d in events if e == "tool"]
    assert (
        tool_events[0]["status"] == "running"
        and tool_events[-1]["status"] == "done"
    )
    assert set(tool_events[-1]) >= {
        "id",
        "name",
        "label",
        "status",
        "summary",
        "link",
    }
    done = events[-1][1]
    assert (
        "<strong>5.000.000 đ</strong>" in done["html"] and done["message_id"]
    )
    cid = events[0][1]["conversation_id"]
    # A follow-up question in the same conversation sends the history
    fake_llm.responses = [{"text": "Không đổi."}]
    events2 = _chat(web, "Còn hôm qua?", cid)
    assert events2[0][1]["conversation_id"] == cid
    roles = [m["role"] for m in fake_llm.requests[-1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    msgs = db.scalars(
        select(AiMessage).where(AiMessage.conversation_id == cid)
    ).all()
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[1].tool_calls[0]["name"] == "get_account_balance"
    r = web.get(f"/ai/conversations/{cid}")
    assert r.status_code == 200 and "5.000.000" in r.text
    assert (
        web.get("/ai").status_code == 200
        and web.get(f"/ai?c={cid}").status_code == 200
    )
    # Other users cannot view the conversation
    web.login(ROLE_EMAILS[ACCOUNTANT])
    assert web.get(f"/ai/conversations/{cid}").status_code == 404
    events3 = _chat(web, "xin chào", cid)
    assert events3[0][0] == "error"


def test_panel_when_disabled_and_enabled(web, db, ctxs, fake_llm):
    web.login(ROLE_EMAILS[OWNER])
    r = web.get("/ai/panel")
    assert r.status_code == 200 and "chưa được bật" in r.text
    events = _chat(web, "hi")
    assert events == [
        ("error", {"message": "Trợ lý AI chưa được bật cho công ty này"})
    ]
    assert 'id="ai-panel"' not in web.get("/").text
    enable_ai(db, ctxs[OWNER])
    assert 'id="ai-panel"' in web.get("/").text
    r = web.get("/ai/panel")
    assert "fake-model" in r.text and "aiChat(" in r.text


def test_daily_limit(web, db, ctxs, fake_llm):
    enable_ai(db, ctxs[OWNER], daily_limit=1)
    web.login(ROLE_EMAILS[CHIEF])
    assert _chat(web, "một")[-1][0] == "done"
    events = _chat(web, "hai")
    assert events[0][0] == "error" and "hết 1 lượt" in events[0][1]["message"]


def test_settings_page_saves_encrypted_key(web, db, ctxs, fake_llm):
    web.login(ROLE_EMAILS[OWNER])
    assert web.get("/settings/ai").status_code == 200
    r = web.post(
        "/settings/ai",
        {
            "enabled": "1",
            "preset": "anthropic",
            "base_url": "",
            "model": "",
            "api_key": "sk-ant-secret-value-123",
            "temperature": "0.3",
            "max_rounds": "6",
            "daily_limit": "50",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    company = db.get(Company, ctxs[OWNER].company_id)
    db.refresh(company)
    raw = company.settings["ai"]
    assert raw["api_key_enc"] and "sk-ant-secret" not in json.dumps(
        company.settings
    )
    from app.features.ai.config import get_config

    cfg = get_config(company)
    assert (
        cfg.api_key == "sk-ant-secret-value-123"
        and cfg.model == "claude-opus-5-5"
    )
    assert cfg.base_url == "https://api.anthropic.com/v1/" and cfg.ready
    log = db.scalars(
        select(AuditLog).where(AuditLog.action == "ai.settings")
    ).one()
    assert "sk-ant" not in (log.after or "") and "api_key_enc" not in (
        log.after or ""
    )
    page = web.get("/settings/ai").text
    assert "sk-ant-secret" not in page
    # Saving without entering a key keeps the old key
    web.post(
        "/settings/ai", {"enabled": "1", "preset": "anthropic", "api_key": ""}
    )
    db.refresh(company)
    assert get_config(company).api_key == "sk-ant-secret-value-123"
    # Connection test (HTMX)
    fake_llm.responses = [{"text": "OK"}]
    r = web.post("/settings/ai/test", headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Kết nối thành công" in r.text
    # Missing model with the custom preset
    r = web.post(
        "/settings/ai",
        {
            "enabled": "1",
            "preset": "custom",
            "base_url": "https://x.test/v1",
            "model": "",
        },
    )
    assert r.status_code == 400 and "model" in r.text
    web.login(ROLE_EMAILS[ACCOUNTANT])
    assert web.get("/settings/ai").status_code == 403


def test_private_urls_blocked_in_saas_mode(db, ctxs, monkeypatch):
    import pytest

    from app.core.errors import BusinessError
    from app.features.ai.config import check_base_url

    assert check_base_url("ftp://x") is not None
    assert "nội bộ" in (check_base_url("https://127.0.0.1/v1") or "")
    assert check_base_url("http://localhost:11434/v1") is not None
    with pytest.raises(BusinessError):
        enable_ai(
            db,
            ctxs[OWNER],
            preset="ollama",
            base_url="http://localhost:11434/v1",
            api_key="",
        )
    monkeypatch.setenv("AI_ALLOW_PRIVATE_URLS", "1")
    assert check_base_url("http://localhost:11434/v1") is None


def test_conversation_delete(web, db, ctxs, fake_llm):
    enable_ai(db, ctxs[OWNER])
    web.login(ROLE_EMAILS[CHIEF])
    cid = _chat(web, "xin chào")[0][1]["conversation_id"]
    r = web.post(f"/ai/conversations/{cid}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert db.get(AiConversation, cid) is None


def test_ai_pages_render_in_english(web, db, ctxs, fake_llm):
    enable_ai(db, ctxs[OWNER])
    web.login(ROLE_EMAILS[OWNER])
    web.get("/lang/en")
    for url in ("/ai", "/ai/panel", "/settings/ai", "/settings/api-tokens"):
        assert web.get(url).status_code == 200, url
