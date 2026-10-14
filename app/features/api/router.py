"""Public API /api/v1 (Bearer token), OpenAI-compatible endpoints and
the token management page.

Authentication: the core middleware reads
`Authorization: Bearer vfas_…` (request.state.auth_via == "token").
Permissions = the token creator's role in the company ∩ the token
scopes (read: .view/.export, ai.use; write_draft: .create).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from app.core.config import get_settings
from app.core.dates import today
from app.core.db import SessionLocal, get_db, get_engine, ulower
from app.core.errors import (
    BusinessError,
    LoginRequiredError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.registry import can_entry, load_features, registry
from app.core.security import (
    API_SCOPES,
    CurrentUser,
    rate_limited,
    record_event,
    require,
)
from app.core.tenancy import Ctx, bind_tenant
from app.core.web import form_data, get_ctx, get_tenant_db, redirect, render
from app.features.accounts.models import Account
from app.features.ai import service as ai_service
from app.features.ai.agent import execute_tool, tool_schemas
from app.features.ai.client import LLMError
from app.features.api import schemas as s
from app.features.auth import service as auth
from app.features.journal import queries
from app.features.journal import service as js
from app.features.journal.models import JournalEntry
from app.features.org.models import Company

MODEL_ID = "vfas-agent"
RATE_LIMIT = 60  # requests / minute / token
bearer = HTTPBearer(
    auto_error=False,
    description="API token dạng vfas_… tạo tại Hệ thống → API & tích hợp",
)

router = APIRouter()


# ---------------------------------------- authentication, rate limiting


def api_user(
    request: Request, _cred=Depends(bearer), db: Session = Depends(get_db)
) -> CurrentUser:
    """Return the user of the API token.

    Limits each token to RATE_LIMIT requests per minute (counted in
    the DB, so it holds across multiple workers).
    """
    user = getattr(request.state, "user", None)
    if user is None or getattr(request.state, "auth_via", None) != "token":
        raise LoginRequiredError()
    key = (
        "tok:"
        + hashlib.sha256(
            request.headers.get("authorization", "").encode()
        ).hexdigest()[:40]
    )
    if rate_limited(db, "api", RATE_LIMIT, timedelta(minutes=1), email=key):
        raise HTTPException(429, "Quá nhiều yêu cầu, thử lại sau 1 phút")
    record_event(db, "api", email=key)
    return user


def need(user: CurrentUser, perm: str) -> None:
    if not user.can(perm):
        raise PermissionDeniedError(f"Token không có quyền {perm}")


api = APIRouter(
    prefix="/api/v1",
    tags=["API v1"],
    dependencies=[Depends(api_user)],
    responses={
        401: {"model": s.ErrorResponse},
        403: {"model": s.ErrorResponse},
        422: {"model": s.ErrorResponse},
        429: {"model": s.ErrorResponse},
    },
)


def _date(value: str | None, default: date | None = None) -> date | None:
    from app.core.dates import parse_date

    if not value:
        return default
    try:
        return parse_date(value)
    except ValueError as e:
        raise BusinessError(f"Ngày không hợp lệ: {value}") from e


# ------------------------------------------------------------ resources


@api.get(
    "/me", response_model=s.MeOut, summary="Người dùng và công ty của token"
)
def me(
    request: Request,
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    c = db.get(Company, user.company_id)
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
        },
        "company": {
            "id": c.id,
            "name": c.name,
            "tax_code": c.tax_code or None,
        },
        "role": user.role,
        "scopes": sorted(user.api_scopes or []),
    }


@api.get(
    "/accounts",
    response_model=list[s.AccountOut],
    summary="Hệ thống tài khoản",
)
def accounts(
    q: str = "",
    postable: bool | None = None,
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    need(user, "catalog.view")
    from app.features.accounts.service import list_accounts

    return [
        {
            "code": a.code,
            "name": a.name,
            "parent_code": a.parent_code,
            "level": a.level,
            "kind": a.kind,
            "normal_balance": a.normal,
            "postable": a.is_postable,
            "partner_mode": a.partner_mode,
            "needs_bank": a.needs_bank,
            "active": a.is_active,
        }
        for a in list_accounts(db, user.company_id, q=q, postable=postable)
    ]


def _partner_out(p) -> dict:
    return {
        "id": p.id,
        "code": p.code,
        "name": p.name,
        "is_customer": p.is_customer,
        "is_supplier": p.is_supplier,
        "is_employee": p.is_employee,
        "tax_code": p.tax_code or None,
        "address": p.address or None,
        "phone": p.phone or None,
        "email": p.email or None,
        "active": p.is_active,
    }


@api.get(
    "/partners",
    response_model=list[s.PartnerOut],
    summary="Khách hàng, nhà cung cấp, nhân viên",
)
def partners(
    q: str = "",
    role: str | None = Query(None, pattern="^(customer|supplier|employee)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    need(user, "catalog.view")
    from app.features.partners.service import search

    return [
        _partner_out(p)
        for p in search(
            db,
            user.company_id,
            q,
            role=role,
            active=None,
            limit=limit,
            offset=offset,
        )
    ]


@api.post(
    "/partners",
    response_model=s.PartnerOut,
    status_code=201,
    summary="Thêm đối tượng",
)
def partner_create(
    request: Request,
    body: s.PartnerIn,
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    need(user, "catalog.create")
    from app.features.partners.service import PartnerInput, create

    p = create(db, get_ctx(request), PartnerInput(**body.model_dump()))
    return _partner_out(p)


@api.get(
    "/items",
    response_model=list[s.ItemOut],
    summary="Vật tư, hàng hóa, dịch vụ",
)
def items(
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    need(user, "catalog.view")
    from app.features.items.service import search

    return [
        {
            "id": it.id,
            "code": it.code,
            "name": it.name,
            "unit": it.unit,
            "kind": it.kind,
            "vat_rate": it.vat_rate,
            "sale_price": str(it.sale_price or 0),
            "purchase_price": str(it.purchase_price or 0),
            "active": it.is_active,
        }
        for it in search(
            db, user.company_id, q, active=None, limit=limit, offset=offset
        )
    ]


def _entry_out(e: JournalEntry, with_lines: bool = False) -> dict:
    d = {
        "id": e.id,
        "doc_type": e.doc_type,
        "number": e.number,
        "date": e.date,
        "doc_date": e.doc_date,
        "status": e.status,
        "description": e.description,
        "contact_name": e.contact_name,
        "partner_id": e.partner_id,
        "total": e.total,
        "url": e.url,
    }
    if with_lines:
        d["lines"] = [
            {
                "line_no": ln.line_no,
                "debit_account": ln.debit_account,
                "credit_account": ln.credit_account,
                "amount": ln.amount,
                "description": ln.description,
                "debit_partner_id": ln.debit_partner_id,
                "credit_partner_id": ln.credit_partner_id,
            }
            for ln in e.lines
        ]
    return d


@api.get("/entries", response_model=s.EntryList, summary="Danh sách chứng từ")
def entries(
    doc_type: str | None = None,
    status: str | None = Query(None, pattern="^(draft|posted|cancelled)$"),
    start: str | None = None,
    end: str | None = None,
    q: str = "",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    need(user, "journal.view")
    stmt = select(JournalEntry).where(
        JournalEntry.company_id == user.company_id
    )
    if doc_type:
        stmt = stmt.where(JournalEntry.doc_type == doc_type.upper())
    if status:
        stmt = stmt.where(JournalEntry.status == status)
    if start:
        stmt = stmt.where(JournalEntry.date >= _date(start))
    if end:
        stmt = stmt.where(JournalEntry.date <= _date(end))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                ulower(JournalEntry.number).like(like),
                ulower(JournalEntry.description).like(like),
            )
        )
    rows = db.scalars(
        stmt.order_by(JournalEntry.date.desc(), JournalEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return {
        "data": [
            _entry_out(e) for e in rows if can_entry(user, e.doc_type, "view")
        ],
        "limit": limit,
        "offset": offset,
    }


@api.get(
    "/entries/{entry_id}",
    response_model=s.EntryOut,
    summary="Chi tiết chứng từ",
)
def entry_get(
    request: Request,
    entry_id: int,
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    e = js.get_entry(db, get_ctx(request), entry_id)
    if not can_entry(user, e.doc_type, "view"):
        raise PermissionDeniedError()
    return _entry_out(e, with_lines=True)


@api.post(
    "/entries",
    response_model=s.EntryOut,
    status_code=201,
    summary="Tạo chứng từ NHÁP (không ghi sổ qua API)",
)
def entry_create(
    request: Request,
    body: s.EntryIn,
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    doc_type = body.doc_type.upper()
    if doc_type not in registry.doc_types:
        raise BusinessError(f"Loại chứng từ {doc_type} không hợp lệ")
    if not can_entry(user, doc_type, "create"):
        raise PermissionDeniedError(
            f"Token không có quyền tạo chứng từ {doc_type}"
        )
    inp = js.EntryIn(
        doc_type=doc_type,
        date=body.date,
        doc_date=body.doc_date,
        description=body.description,
        contact_name=body.contact_name,
        contact_address=body.contact_address,
        partner_id=body.partner_id,
        ref_no=body.ref_no,
        source="api",
        lines=[js.LineIn(**ln.model_dump()) for ln in body.lines],
    )
    e = js.create_entry(db, get_ctx(request), inp)
    return _entry_out(e, with_lines=True)


@api.get(
    "/reports/balance",
    response_model=s.BalanceOut,
    summary="Số dư tài khoản đến ngày",
)
def report_balance(
    account: str,
    date: str | None = None,
    partner_id: int | None = None,
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    need(user, "journal.view")
    if not db.scalar(
        select(Account.id).where(
            Account.company_id == user.company_id, Account.code == account
        )
    ):
        raise NotFoundError(f"Không có tài khoản {account}")
    d = _date(date, today())
    sm = queries.sums(
        db, user.company_id, account, end=d, partner_id=partner_id
    )
    return {
        "account": account,
        "date": d,
        "partner_id": partner_id,
        "total_debit": sm.debit,
        "total_credit": sm.credit,
        "balance": sm.net,
    }


@api.get(
    "/reports/trial-balance",
    response_model=s.TrialBalanceOut,
    summary="Bảng cân đối tài khoản (chi tiết)",
)
def report_trial_balance(
    start: str,
    end: str,
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    need(user, "reports.view")
    d1, d2 = _date(start), _date(end)
    if d1 > d2:
        raise BusinessError("Từ ngày phải trước đến ngày")
    names = dict(
        db.execute(
            select(Account.code, Account.name).where(
                Account.company_id == user.company_id
            )
        ).all()
    )
    rows = queries.account_sums(db, user.company_id, d1, d2)
    return {
        "start": d1,
        "end": d2,
        "rows": [
            {
                "account": r.account,
                "name": names.get(r.account),
                "opening": r.opening,
                "debit": r.debit,
                "credit": r.credit,
                "closing": r.closing,
            }
            for r in rows
        ],
        "total_debit": sum(r.debit for r in rows),
        "total_credit": sum(r.credit for r in rows),
    }


# ---------------------------------------- OpenAI-style function calling


def _api_ctx(request: Request) -> Ctx:
    ctx = get_ctx(request)
    return Ctx(company_id=ctx.company_id, user=ctx.user, via="api", ip=ctx.ip)


@api.get(
    "/tools",
    summary="Danh sách công cụ theo định dạng `tools` của OpenAI",
    description=(
        "Mỗi phần tử dùng trực tiếp làm `tools` khi gọi Chat Completions; "
        "thực thi bằng "
        "POST /api/v1/tools/{name}. Chỉ gồm công cụ token được phép dùng."
    ),
)
def tools_list(request: Request, _u: CurrentUser = Depends(api_user)):
    return {"object": "list", "data": tool_schemas(_api_ctx(request))}


@api.post(
    "/tools/{name}",
    response_model=s.ToolCallOut,
    summary="Thực thi một công cụ",
    description=(
        "Body là object JSON tham số (đúng `parameters` của công cụ). "
        "Công cụ ghi chỉ tạo nháp."
    ),
)
def tools_call(
    request: Request,
    name: str,
    args: dict = Body(default_factory=dict),
    _u: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    load_features()
    ctx = _api_ctx(request)
    tool = registry.ai_tools.get(name)
    if tool is None:
        raise NotFoundError(f"Không có công cụ '{name}'")
    if tool.perm and not ctx.can(tool.perm):
        raise PermissionDeniedError(
            f"Token không có quyền dùng công cụ {name}"
        )
    result, ok, _tool = execute_tool(db, ctx, name, args, via="api")
    return {"name": name, "ok": ok, "result": result}


# ------------------------------------------------- OpenAI compatibility


@api.get("/models", summary="Danh sách model (tương thích OpenAI)")
def models(_u: CurrentUser = Depends(api_user)):
    return {
        "object": "list",
        "data": [
            {
                "id": MODEL_ID,
                "object": "model",
                "created": 1767225600,
                "owned_by": get_settings().product_name,
            }
        ],
    }


def _chunk(
    cid: str,
    created: int,
    model: str,
    delta: dict,
    finish: str | None = None,
    usage=None,
) -> str:
    body = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        body["usage"] = usage
    return "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"


@api.post(
    "/chat/completions",
    summary="Chat với trợ lý kế toán (tương thích OpenAI Chat Completions)",
    description=(
        "Trỏ `base_url` của SDK/ứng dụng OpenAI về `…/api/v1`, "
        "`api_key` = API token, "
        f"`model` = `{MODEL_ID}`. Trợ lý tự gọi công cụ trên sổ sách "
        "theo quyền của token; "
        "chỉ trả về câu trả lời cuối (stream hoặc không)."
    ),
)
def chat_completions(
    request: Request,
    body: s.ChatCompletionRequest,
    user: CurrentUser = Depends(api_user),
    db: Session = Depends(get_tenant_db),
):
    need(user, "ai.use")
    ctx = get_ctx(request)
    messages = [m.model_dump() for m in body.messages]
    model = body.model or MODEL_ID
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    try:
        agent, history = ai_service.prepare_api_run(db, ctx, messages)
    except ai_service.AiUnavailableError as e:
        return JSONResponse(
            {
                "error": {
                    "message": e.message,
                    "type": "ai_unavailable",
                    "code": "ai_unavailable",
                }
            },
            status_code=403,
        )
    except LLMError as e:
        return JSONResponse(
            {
                "error": {
                    "message": e.message,
                    "type": "upstream_error",
                    "code": "upstream_error",
                }
            },
            status_code=502,
        )

    if not body.stream:
        text, tools, usage, error = [], [], None, None
        for ev in ai_service.run_api_agent(db, ctx, agent, history):
            if ev["type"] == "token":
                text.append(ev["text"])
            elif ev["type"] == "tool" and ev["status"] != "running":
                tools.append(
                    {k: ev[k] for k in ("name", "status", "summary", "link")}
                )
            elif ev["type"] == "final":
                usage = ev.get("usage")
            elif ev["type"] == "error":
                error = ev["message"]
        if error:
            return JSONResponse(
                {
                    "error": {
                        "message": error,
                        "type": "upstream_error",
                        "code": "upstream_error",
                    }
                },
                status_code=502,
            )
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "".join(text).strip(),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": usage
            or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "x_tool_runs": tools,
        }

    include_usage = bool((body.stream_options or {}).get("include_usage"))
    company_id = ctx.company_id

    def stream():
        get_engine()
        sdb = SessionLocal()
        bind_tenant(sdb, company_id)
        agent.db = sdb
        try:
            yield _chunk(
                cid, created, model, {"role": "assistant", "content": ""}
            )
            for ev in ai_service.run_api_agent(sdb, ctx, agent, history):
                if ev["type"] == "token":
                    yield _chunk(cid, created, model, {"content": ev["text"]})
                elif ev["type"] == "final":
                    yield _chunk(cid, created, model, {}, "stop")
                    if include_usage:
                        body_u = {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [],
                            "usage": ev.get("usage"),
                        }
                        yield "data: " + json.dumps(body_u) + "\n\n"
                elif ev["type"] == "error":
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "error": {
                                    "message": ev["message"],
                                    "type": "upstream_error",
                                }
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                    break
            yield "data: [DONE]\n\n"
        finally:
            sdb.close()

    db.close()
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


router.include_router(api)


# ------------------------------------------ token management page (web)


def _public_base(request: Request) -> str:
    return (
        get_settings().public_url.rstrip("/")
        or str(request.base_url).rstrip("/")
    ) + "/api/v1"


def _tokens_ctx(
    request: Request,
    db: Session,
    new_token: str | None = None,
    errors: dict | None = None,
) -> dict:
    from datetime import datetime, timezone

    tokens = auth.list_api_tokens(db, get_ctx(request))
    now = datetime.now(timezone.utc)
    expired = {
        t.id
        for t in tokens
        if t.expires_at is not None
        and (
            t.expires_at
            if t.expires_at.tzinfo
            else t.expires_at.replace(tzinfo=timezone.utc)
        )
        < now
    }
    return {
        "tokens": tokens,
        "expired_ids": expired,
        "new_token": new_token,
        "errors": errors or {},
        "api_base": _public_base(request),
        "model_id": MODEL_ID,
        "scopes": API_SCOPES,
        "breadcrumbs": [("Hệ thống", None), ("API & tích hợp", None)],
    }


@router.get("/settings/api-tokens", include_in_schema=False)
def tokens_page(
    request: Request,
    _u: CurrentUser = Depends(require("api.manage")),
    db: Session = Depends(get_tenant_db),
):
    return render(request, "api/tokens.html", _tokens_ctx(request, db))


@router.post("/settings/api-tokens", include_in_schema=False)
async def tokens_create(
    request: Request,
    _u: CurrentUser = Depends(require("api.manage")),
    db: Session = Depends(get_tenant_db),
):
    form = await request.form()
    scopes = [v for v in form.getlist("scopes") if v in API_SCOPES] or ["read"]
    try:
        days = int(form.get("days") or 0) or None
    except ValueError:
        days = None
    try:
        _tok, raw = auth.create_api_token(
            db, get_ctx(request), str(form.get("name") or ""), scopes, days
        )
    except BusinessError as e:
        db.rollback()
        return render(
            request,
            "api/tokens.html",
            _tokens_ctx(request, db, errors={"name": e.message}),
            status_code=400,
        )
    # The raw token is shown only once; never put it in a cookie/flash.
    return render(
        request,
        "api/tokens.html",
        _tokens_ctx(request, db, new_token=raw),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/settings/api-tokens/{token_id}/revoke", include_in_schema=False)
def tokens_revoke(
    request: Request,
    token_id: int,
    _form: dict = Depends(form_data),
    _u: CurrentUser = Depends(require("api.manage")),
    db: Session = Depends(get_tenant_db),
):
    auth.revoke_api_token(db, get_ctx(request), token_id)
    return redirect(
        "/settings/api-tokens", "Đã thu hồi token", request=request
    )
