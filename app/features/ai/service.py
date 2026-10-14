"""AI conversations, usage limits and assistant runs.

Runs the assistant for the web (SSE) and for the OpenAI-compatible API.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.dates import fmt_datetime, today
from app.core.db import is_sqlite
from app.core.errors import BusinessError, NotFoundError
from app.core.tenancy import Ctx
from app.features.ai import markdown
from app.features.ai.agent import Agent
from app.features.ai.client import LLMError, client_for
from app.features.ai.config import AiConfig, get_config
from app.features.ai.models import AiConversation, AiMessage, AiUsage
from app.features.org.models import Company

HISTORY_LIMIT = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AiUnavailableError(BusinessError):
    pass


def config_for(db: Session, ctx: Ctx) -> AiConfig:
    return get_config(db.get(Company, ctx.company_id))


def unavailable_reason(cfg: AiConfig, ctx: Ctx) -> str | None:
    if not ctx.can("ai.use"):
        return "Bạn không có quyền dùng trợ lý AI"
    if not cfg.enabled:
        return "Trợ lý AI chưa được bật cho công ty này"
    if cfg.key_error:
        return (
            "Không giải mã được API key đã lưu (khóa bí mật của hệ thống "
            "đã đổi). Nhập lại API key."
        )
    if not cfg.ready:
        return "Trợ lý AI chưa cấu hình đủ (địa chỉ, model, API key)"
    return None


# ---------------------------------------------------------------- usage


def usage_today(db: Session, ctx: Ctx) -> AiUsage | None:
    return db.scalar(
        select(AiUsage).where(
            AiUsage.company_id == ctx.company_id,
            AiUsage.user_id == ctx.user_id,
            AiUsage.day == today(),
        )
    )


_QUOTA_LOCK = threading.Lock()
# Maximum concurrent AI requests per company (per process), so that one
# company cannot take up all threads/connections.
MAX_CONCURRENT_PER_COMPANY = 3
_running: dict[int, int] = {}
_running_lock = threading.Lock()


def check_quota(db: Session, ctx: Ctx, cfg: AiConfig) -> None:
    """Check the quota and RESERVE one request right away.

    The counter is incremented before the LLM is called, so parallel
    requests cannot exceed the daily limit.
    """
    with _QUOTA_LOCK:
        q = select(AiUsage).where(
            AiUsage.company_id == ctx.company_id,
            AiUsage.user_id == ctx.user_id,
            AiUsage.day == today(),
        )
        if not is_sqlite(db):
            q = q.with_for_update()
        u = db.scalar(q)
        if cfg.daily_limit and u is not None and u.requests >= cfg.daily_limit:
            db.rollback()
            raise AiUnavailableError(
                f"Đã dùng hết {cfg.daily_limit} lượt hỏi trong ngày. "
                "Thử lại vào ngày mai."
            )
        if u is None:
            u = AiUsage(
                company_id=ctx.company_id,
                user_id=ctx.user_id or 0,
                day=today(),
                requests=0,
                tokens=0,
            )
            db.add(u)
        u.requests += 1
        db.commit()


def record_usage(db: Session, ctx: Ctx, tokens: int = 0) -> None:
    """Add the token count after a run.

    The request itself was already reserved by check_quota.
    """
    u = usage_today(db, ctx)
    if u is not None and tokens:
        u.tokens += int(tokens or 0)
        db.commit()


class _Slot:
    """Hold one of the company's concurrent AI run slots."""

    def __init__(self, company_id: int):
        self.company_id = company_id
        self.ok = False

    def __enter__(self):
        with _running_lock:
            n = _running.get(self.company_id, 0)
            if n >= MAX_CONCURRENT_PER_COMPANY:
                return self
            _running[self.company_id] = n + 1
            self.ok = True
        return self

    def __exit__(self, *exc):
        if self.ok:
            with _running_lock:
                _running[self.company_id] = max(
                    0, _running.get(self.company_id, 1) - 1
                )
        return False


# -------------------------------------------------------- conversations


def list_conversations(
    db: Session, ctx: Ctx, limit: int = 30
) -> list[AiConversation]:
    return list(
        db.scalars(
            select(AiConversation)
            .where(
                AiConversation.company_id == ctx.company_id,
                AiConversation.user_id == ctx.user_id,
            )
            .order_by(AiConversation.updated_at.desc())
            .limit(limit)
        )
    )


def get_conversation(
    db: Session, ctx: Ctx, conversation_id: int
) -> AiConversation:
    c = db.get(AiConversation, conversation_id)
    if c is None or c.company_id != ctx.company_id or c.user_id != ctx.user_id:
        raise NotFoundError("Không tìm thấy hội thoại")
    return c


def delete_conversation(db: Session, ctx: Ctx, conversation_id: int) -> None:
    c = get_conversation(db, ctx, conversation_id)
    db.execute(delete(AiMessage).where(AiMessage.conversation_id == c.id))
    db.delete(c)
    db.commit()


def message_view(m: AiMessage) -> dict:
    if m.role == "user":
        return {"role": "user", "text": m.content}
    return {
        "role": "assistant",
        "text": m.content,
        "html": markdown.render(m.content),
        "tools": m.tool_calls or [],
    }


def conversation_views(db: Session, c: AiConversation) -> list[dict]:
    return [
        message_view(m) for m in c.messages if m.role in ("user", "assistant")
    ]


def conversation_list_view(
    db: Session, ctx: Ctx, active_id: int | None = None
) -> list[dict]:
    return [
        {
            "id": c.id,
            "title": c.title,
            "updated_at": fmt_datetime(c.updated_at),
            "active": c.id == active_id,
        }
        for c in list_conversations(db, ctx)
    ]


def _history(c: AiConversation) -> list[dict]:
    msgs = [
        m
        for m in c.messages
        if m.role in ("user", "assistant") and (m.content or "").strip()
    ]
    return [
        {"role": m.role, "content": m.content} for m in msgs[-HISTORY_LIMIT:]
    ]


# ------------------------------------------------------- web chat (SSE)


def sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def chat_stream(
    db: Session, ctx: Ctx, content: str, conversation_id: int | None
) -> Iterator[str]:
    """Yield SSE events as specified in docs/CONTRACTS.md section 3.

    ``db`` is created by the caller, which closes it once the stream
    has finished.
    """
    content = (content or "").strip()
    if not content:
        yield sse("error", {"message": "Nhập câu hỏi"})
        return
    if len(content) > 8000:
        yield sse("error", {"message": "Câu hỏi quá dài (tối đa 8.000 ký tự)"})
        return
    cfg = config_for(db, ctx)
    reason = unavailable_reason(cfg, ctx)
    if reason:
        yield sse("error", {"message": reason})
        return
    try:
        check_quota(db, ctx, cfg)
        conv = (
            get_conversation(db, ctx, conversation_id)
            if conversation_id
            else None
        )
    except (BusinessError, NotFoundError) as e:
        yield sse("error", {"message": e.message})
        return
    if conv is None:
        conv = AiConversation(
            company_id=ctx.company_id,
            user_id=ctx.user_id,
            title=content[:80],
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(conv)
        db.flush()
    db.add(
        AiMessage(
            company_id=ctx.company_id,
            conversation_id=conv.id,
            role="user",
            content=content,
            created_at=_now(),
        )
    )
    conv.updated_at = _now()
    db.commit()
    yield sse("meta", {"conversation_id": conv.id})
    db.refresh(conv)
    history = _history(conv)
    try:
        client = client_for(cfg)
    except LLMError as e:
        yield sse("error", {"message": e.message})
        return
    agent = Agent(
        db,
        ctx,
        client,
        max_rounds=cfg.max_rounds,
        allow_payroll=cfg.allow_payroll_tools,
    )
    with _Slot(ctx.company_id) as slot:
        if not slot.ok:
            yield sse(
                "error",
                {
                    "message": "Công ty đang có nhiều câu hỏi AI chạy "
                    "cùng lúc. Thử lại sau ít giây."
                },
            )
            return
        yield from _chat_events(db, ctx, agent, history, conv)


def _chat_events(
    db: Session, ctx: Ctx, agent, history: list[dict], conv
) -> Iterator[str]:
    for ev in agent.run(history):
        if ev["type"] == "token":
            yield sse("token", {"text": ev["text"]})
        elif ev["type"] == "tool":
            yield sse(
                "tool",
                {
                    k: ev[k]
                    for k in (
                        "id",
                        "name",
                        "label",
                        "status",
                        "summary",
                        "link",
                    )
                },
            )
        elif ev["type"] == "error":
            db.rollback()
            yield sse("error", {"message": ev["message"]})
            return
        elif ev["type"] == "final":
            text = ev["text"].strip() or "(Không có nội dung trả lời)"
            usage = ev.get("usage") or {}
            msg = AiMessage(
                company_id=ctx.company_id,
                conversation_id=conv.id,
                role="assistant",
                content=text,
                tool_calls=ev["tools"] or None,
                tokens=usage.get("total_tokens") or None,
                created_at=_now(),
            )
            db.add(msg)
            conv = db.get(AiConversation, conv.id)
            conv.updated_at = _now()
            db.commit()
            record_usage(db, ctx, usage.get("total_tokens") or 0)
            yield sse(
                "done",
                {
                    "message_id": msg.id,
                    "conversation_id": conv.id,
                    "html": markdown.render(text),
                },
            )


# ------------------------------------ API runs (conversation not saved)


def normalize_messages(messages: list[dict]) -> tuple[list[dict], list[str]]:
    """Split OpenAI-style client messages into history and instructions.

    Returns ``(user/assistant history, extra system instructions)``.
    """
    history: list[dict] = []
    extra: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        content = (content or "").strip() if isinstance(content, str) else ""
        if role in ("system", "developer"):
            if content:
                extra.append(content[:4000])
        elif role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})
    return history[-HISTORY_LIMIT * 2 :], extra


def prepare_api_run(
    db: Session, ctx: Ctx, messages: list[dict]
) -> tuple[Agent, list[dict]]:
    """Prepare an agent run for /api/v1/chat/completions.

    Runs the checks first and raises AiUnavailableError, BusinessError
    or LLMError when the run is not possible.
    """
    cfg = config_for(db, ctx)
    reason = unavailable_reason(cfg, ctx)
    if reason:
        raise AiUnavailableError(reason)
    check_quota(db, ctx, cfg)
    history, extra = normalize_messages(messages)
    if not history or history[-1]["role"] != "user":
        raise BusinessError("Cần ít nhất một tin nhắn role=user ở cuối")
    client = client_for(cfg)
    return Agent(
        db,
        ctx,
        client,
        max_rounds=cfg.max_rounds,
        extra_system=extra,
        allow_payroll=cfg.allow_payroll_tools,
    ), history


def run_api_agent(
    db: Session, ctx: Ctx, agent: Agent, history: list[dict]
) -> Iterator[dict]:
    """Run a prepared agent and record the tokens used when done."""
    with _Slot(ctx.company_id) as slot:
        if not slot.ok:
            yield {
                "type": "error",
                "message": "Công ty đang có nhiều câu hỏi AI chạy "
                "cùng lúc. Thử lại sau.",
            }
            return
        for ev in agent.run(history):
            if ev["type"] == "final":
                record_usage(
                    db, ctx, (ev.get("usage") or {}).get("total_tokens") or 0
                )
            yield ev
