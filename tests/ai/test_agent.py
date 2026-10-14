"""OpenAI-style agent tests.

Tool loop, permissions, drafts, tool errors, provider errors, safe
Markdown.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.core.audit import AuditLog
from app.core.registry import ACCOUNTANT, CASHIER, CHIEF, VIEWER, registry
from app.features.ai import markdown
from app.features.ai.agent import Agent, execute_tool, tool_schemas
from app.features.ai.client import LLMClient
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from tests.conftest import post


def _agent(db, ctx, max_rounds=4):
    return Agent(
        db,
        ctx,
        LLMClient("https://llm.test/v1", "fake-model", "sk-x"),
        max_rounds=max_rounds,
    )


def _run(agent, history):
    return list(agent.run(history))


@pytest.fixture()
def draft_tool():
    """Temporary write tool for testing the draft flow.

    Mirrors create_draft_journal_entry of the general ledger group.
    """
    from app.core.registry import ai_tool

    @ai_tool(
        name="test_create_draft",
        description="Create a draft journal entry",
        perm="journal.create",
        write=True,
        label="Lập nháp thử",
        parameters={
            "type": "object",
            "properties": {"amount": {"type": "integer"}},
            "required": ["amount"],
        },
    )
    def _tool(db, ctx, amount: int):
        e = js.create_entry(
            db,
            ctx,
            js.EntryIn(
                "PK",
                date(2024, 5, 2),
                [js.LineIn("6422", "1111", int(amount))],
                description="Chi phí do AI lập",
            ),
        )
        return {"draft_id": e.id, "url": e.url, "summary": f"Nháp #{e.id}"}

    @ai_tool(name="test_boom", description="Always fails", perm=None)
    def _boom(db, ctx):
        raise RuntimeError("broken")

    yield
    registry.ai_tools.pop("test_create_draft", None)
    registry.ai_tools.pop("test_boom", None)


def test_tool_loop_answers_with_ledger_data(db, ctxs, fake_llm):
    ctx = ctxs[CHIEF]
    post(db, ctx, date(2024, 1, 5), "1111", "4111", 10_000_000, "PT")
    fake_llm.responses = [
        {"tool_calls": [("get_account_balance", {"account": "1111"})]},
        {"text": "Tồn quỹ tiền mặt là **10.000.000 đ**."},
    ]
    events = _run(
        _agent(db, ctx), [{"role": "user", "content": "Tồn quỹ bao nhiêu?"}]
    )
    tools = [e for e in events if e["type"] == "tool"]
    assert [t["status"] for t in tools] == ["running", "done"]
    assert "10.000.000" in tools[-1]["summary"]
    final = events[-1]
    assert final["type"] == "final" and "10.000.000" in final["text"]
    # Turn 1: system prompt + tools; turn 2: includes the tool result
    first, second = fake_llm.requests
    assert (
        first["messages"][0]["role"] == "system"
        and "TT133" in first["messages"][0]["content"]
    )
    assert first["stream"] is True and any(
        t["function"]["name"] == "get_account_balance" for t in first["tools"]
    )
    tool_msg = [m for m in second["messages"] if m["role"] == "tool"][0]
    assert '"balance": 10000000' in tool_msg["content"]
    assert fake_llm.headers[0]["authorization"] == "Bearer sk-x"


def test_tools_filtered_by_permission(db, ctxs, fake_llm):
    names = {t["function"]["name"] for t in tool_schemas(ctxs[CASHIER])}
    assert "get_account_balance" not in names and "get_company_info" in names
    res, ok, _ = execute_tool(
        db, ctxs[CASHIER], "get_account_balance", '{"account": "1111"}'
    )
    assert not ok and "quyền" in res["error"]
    names_viewer = {t["function"]["name"] for t in tool_schemas(ctxs[VIEWER])}
    assert "search_entries" in names_viewer


def test_write_tool_only_creates_draft_and_is_audited(
    db, ctxs, fake_llm, draft_tool
):
    ctx = ctxs[ACCOUNTANT]
    fake_llm.responses = [
        {"tool_calls": [("test_create_draft", {"amount": 250000})]},
        {"text": "Đã lập nháp, vui lòng kiểm tra."},
    ]
    events = _run(
        _agent(db, ctx), [{"role": "user", "content": "Lập phiếu chi 250k"}]
    )
    done = [
        e for e in events if e["type"] == "tool" and e["status"] == "done"
    ][0]
    assert done["link"] and done["link"].startswith("/entries/")
    e = db.scalars(select(JournalEntry)).one()
    assert e.status == "draft" and e.number is None
    logs = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [(log.action, log.via) for log in logs[-2:]] == [
        ("entry.create", "ai"),
        ("ai.tool", "ai"),
    ]
    # A viewer can neither see nor run write tools
    assert "test_create_draft" not in {
        t["function"]["name"] for t in tool_schemas(ctxs[VIEWER])
    }


def test_tool_errors_do_not_crash(db, ctxs, fake_llm, draft_tool):
    fake_llm.responses = [
        {
            "tool_calls": [
                ("khong_ton_tai", {}),
                ("test_boom", {}),
                ("get_account_balance", "{không phải json"),
            ]
        },
        {"tool_calls": [("get_account_balance", {"account": "9999"})]},
        {"text": "Không lấy được số liệu."},
    ]
    events = _run(_agent(db, ctxs[CHIEF]), [{"role": "user", "content": "?"}])
    statuses = [
        e["status"]
        for e in events
        if e["type"] == "tool" and e["status"] != "running"
    ]
    assert statuses == ["error", "error", "error", "error"]
    assert events[-1]["type"] == "final"
    tool_msgs = [
        m for m in fake_llm.requests[1]["messages"] if m["role"] == "tool"
    ]
    assert (
        "Không có công cụ" in tool_msgs[0]["content"]
        and "RuntimeError" in tool_msgs[1]["content"]
    )
    assert "JSON" in tool_msgs[2]["content"]


def test_max_rounds_forces_final_answer(db, ctxs, fake_llm):
    fake_llm.responses = [{"tool_calls": [("get_company_info", {})]}] * 3 + [
        {"text": "Trả lời cuối."}
    ]
    events = _run(
        _agent(db, ctxs[CHIEF], max_rounds=3),
        [{"role": "user", "content": "lặp"}],
    )
    assert (
        events[-1]["type"] == "final" and "Trả lời cuối" in events[-1]["text"]
    )
    assert len(fake_llm.requests) == 4 and "tools" not in fake_llm.requests[-1]


def test_provider_error(db, ctxs, fake_llm):
    fake_llm.responses = [{"status": 401, "error": "invalid x-api-key"}]
    events = _run(_agent(db, ctxs[CHIEF]), [{"role": "user", "content": "hi"}])
    assert events[-1]["type"] == "error" and "401" in events[-1]["message"]


def test_non_streaming_provider_supported(db, ctxs):
    from app.features.ai import client as ai_client
    from tests.ai.fake_llm import FakeLLM

    FakeLLM(
        [
            {"tool_calls": [("get_company_info", {})]},
            {"text": "Công ty NVIAI."},
        ],
        stream=False,
    ).install()
    try:
        events = _run(
            _agent(db, ctxs[CHIEF]),
            [{"role": "user", "content": "Tên công ty?"}],
        )
    finally:
        ai_client.set_transport(None)
    assert events[-1]["type"] == "final" and "NVIAI" in events[-1]["text"]


def test_markdown_is_safe():
    html = markdown.render(
        "**Đậm** <script>alert(1)</script> [x](javascript:alert(1)) "
        "[PT](/cash/5)\n\n"
        "- một\n- hai\n\n| TK | Số dư |\n|---|---:|\n| 1111 | 1.000 |"
    )
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "javascript:" not in html and '<a href="/cash/5">PT</a>' in html
    assert (
        "<strong>Đậm</strong>" in html
        and "<ul><li>một</li><li>hai</li></ul>" in html
    )
    assert '<td class="num">1.000</td>' in html
