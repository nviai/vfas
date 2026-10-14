"""Web AI assistant: chat drawer (SSE), full page, provider settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_engine
from app.core.errors import BusinessError
from app.core.i18n import get_translator
from app.core.security import CurrentUser, get_member, require
from app.core.tenancy import bind_tenant
from app.core.web import (
    form_data,
    get_ctx,
    get_tenant_db,
    is_htmx,
    redirect,
    render,
    resolve_lang,
)
from app.features.ai import service
from app.features.ai.client import LLMError, client_for
from app.features.ai.config import PRESETS, get_config, save_config
from app.features.org.models import Company

router = APIRouter()


def _chat_ctx(
    db: Session, request: Request, conversation_id: int | None = None
) -> dict:
    ctx = get_ctx(request)
    _ = get_translator(resolve_lang(request))
    cfg = service.config_for(db, ctx)
    reason = service.unavailable_reason(cfg, ctx)
    messages = []
    if conversation_id:
        conv = service.get_conversation(db, ctx, conversation_id)
        messages = service.conversation_views(db, conv)
    return {
        "conversation_id": conversation_id,
        "messages": messages,
        "conversations": service.conversation_list_view(
            db, ctx, conversation_id
        ),
        "post_url": "/ai/chat",
        "model_label": cfg.label if cfg.ready else None,
        "disabled_reason": _(reason) if reason else None,
        "settings_url": "/settings/ai" if ctx.can("ai.manage") else None,
    }


@router.get("/ai/panel")
def ai_panel(
    request: Request,
    _u: CurrentUser = Depends(require("ai.use")),
    db: Session = Depends(get_tenant_db),
):
    return render(
        request, "partials/ai_chat.html", {"chat": _chat_ctx(db, request)}
    )


@router.get("/ai")
def ai_page(
    request: Request,
    c: int | None = None,
    _u: CurrentUser = Depends(require("ai.use")),
    db: Session = Depends(get_tenant_db),
):
    return render(
        request,
        "ai/page.html",
        {
            "chat": {**_chat_ctx(db, request, c), "embedded": True},
            "breadcrumbs": [("Trợ lý AI", None)],
        },
    )


@router.get("/ai/conversations/{conversation_id}")
def ai_conversation(
    request: Request,
    conversation_id: int,
    _u: CurrentUser = Depends(require("ai.use")),
    db: Session = Depends(get_tenant_db),
):
    conv = service.get_conversation(db, get_ctx(request), conversation_id)
    return render(
        request,
        "ai/_history.html",
        {"msgs": service.conversation_views(db, conv)},
    )


@router.post("/ai/conversations/{conversation_id}/delete")
def ai_conversation_delete(
    request: Request,
    conversation_id: int,
    _u: CurrentUser = Depends(require("ai.use")),
    db: Session = Depends(get_tenant_db),
):
    service.delete_conversation(db, get_ctx(request), conversation_id)
    return redirect("/ai", "Đã xóa hội thoại", request=request)


@router.post("/ai/chat")
def ai_chat(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(get_member),
):
    ctx = get_ctx(request)
    try:
        conversation_id = int(form.get("conversation_id") or 0) or None
    except ValueError:
        conversation_id = None
    content = str(form.get("content") or "")

    def stream():
        get_engine()
        db = SessionLocal()
        bind_tenant(db, ctx.company_id)
        try:
            yield from service.chat_stream(db, ctx, content, conversation_id)
        except Exception:  # report unexpected errors, don't drop the stream
            import logging

            logging.getLogger(__name__).exception("AI chat failed")
            db.rollback()
            yield service.sse(
                "error",
                {
                    "message": "Trợ lý gặp lỗi không mong muốn. "
                    "Vui lòng thử lại."
                },
            )
        finally:
            db.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------- settings


def _settings_ctx(
    db: Session,
    request: Request,
    errors: dict | None = None,
    form: dict | None = None,
) -> dict:
    ctx = get_ctx(request)
    _ = get_translator(resolve_lang(request))
    company = db.get(Company, ctx.company_id)
    cfg = get_config(company)
    raw = (company.settings or {}).get("ai") or {}
    today_usage = service.usage_today(db, ctx)
    return {
        "cfg": cfg,
        "raw": raw,
        "form": form or {},
        "errors": errors or {},
        "presets": PRESETS,
        "preset_options": [(k, _(v["label"])) for k, v in PRESETS.items()],
        "presets_json": {
            k: {
                "base_url": v["base_url"],
                "model": v["model"],
                "needs_key": v["needs_key"],
            }
            for k, v in PRESETS.items()
        },
        "usage_today": today_usage.requests if today_usage else 0,
        "breadcrumbs": [("Hệ thống", None), ("Trợ lý AI", None)],
    }


@router.get("/settings/ai")
def ai_settings(
    request: Request,
    _u: CurrentUser = Depends(require("ai.manage")),
    db: Session = Depends(get_tenant_db),
):
    return render(request, "ai/settings.html", _settings_ctx(db, request))


@router.post("/settings/ai")
def ai_settings_save(
    request: Request,
    form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("ai.manage")),
    db: Session = Depends(get_tenant_db),
):
    try:
        save_config(
            db,
            get_ctx(request),
            {
                "enabled": form.get("enabled") in ("1", "on", "true"),
                "preset": form.get("preset"),
                "base_url": form.get("base_url"),
                "model": form.get("model"),
                "api_key": form.get("api_key"),
                "clear_key": form.get("clear_key") in ("1", "on", "true"),
                "temperature": form.get("temperature"),
                "max_rounds": form.get("max_rounds"),
                "daily_limit": form.get("daily_limit"),
            },
        )
    except BusinessError as e:
        db.rollback()
        return render(
            request,
            "ai/settings.html",
            _settings_ctx(db, request, {"form": e.message}, form),
            status_code=400,
        )
    return redirect("/settings/ai", "Đã lưu cấu hình trợ lý AI")


@router.post("/settings/ai/test")
def ai_settings_test(
    request: Request,
    _u: CurrentUser = Depends(require("ai.manage")),
    db: Session = Depends(get_tenant_db),
):
    cfg = get_config(db.get(Company, get_ctx(request).company_id))
    ok, message = False, ""
    if not (cfg.base_url and cfg.model):
        message = "Chưa đủ địa chỉ API và model"
    else:
        try:
            res = client_for(cfg).complete(
                [{"role": "user", "content": "Trả lời đúng một từ: OK"}],
                max_tokens=10,
            )
            reply = (res["message"].get("content") or "").strip()[:80]
            ok, message = (
                True,
                f"Kết nối thành công tới {cfg.label}. "
                f"Phản hồi: “{reply or '(trống)'}”",
            )
        except LLMError as e:
            message = e.message
    if not is_htmx(request):
        return redirect("/settings/ai", message, "success" if ok else "error")
    return render(
        request, "ai/_test_result.html", {"ok": ok, "message": message}
    )
