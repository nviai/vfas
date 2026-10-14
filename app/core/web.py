"""Web infrastructure.

Jinja2 setup, the request context middleware (session/API token,
company), flash messages and error handling.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
    select_autoescape,
)
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.core import money
from app.core.config import APP_DIR, get_settings
from app.core.dates import fmt_date, fmt_datetime, today
from app.core.db import SessionLocal, get_engine, session_scope
from app.core.errors import (
    BusinessError,
    LoginRequiredError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.i18n import DEFAULT_LANGUAGE, LANGUAGES, get_translator
from app.core.registry import SECTIONS, load_features, registry
from app.core.security import (
    CSRF_COOKIE,
    CompanyRequiredError,
    CurrentUser,
    build_current_user,
    load_api_token,
    load_session,
)
from app.core.tenancy import Ctx, bind_tenant

FLASH_COOKIE = "vfas_flash"
LANG_COOKIE = "lang"
THEME_COOKIE = "theme"
THEMES = ("light", "dark", "system")


# ---------------------------------------------------------- formatting


def fmt_qty(v, digits: int = 3) -> str:
    """Format 1234.5 as 1.234,5 (trailing zeros dropped)."""
    if v is None or v == "":
        return ""
    d = Decimal(str(v)).quantize(Decimal(1).scaleb(-digits))
    sign = "-" if d < 0 else ""
    d = abs(d)
    whole, _, frac = f"{d:f}".partition(".")
    whole = f"{int(whole):,}".replace(",", ".")
    frac = frac.rstrip("0")
    return sign + whole + ("," + frac if frac else "")


def fmt_pct(v, digits: int = 1) -> str:
    if v is None:
        return ""
    return fmt_qty(round(Decimal(str(v)), digits), digits) + "%"


# --------------------------------------------------------------- Jinja


def _build_env() -> Environment:
    features = APP_DIR / "features"
    prefix = {
        p.name: FileSystemLoader(p / "templates")
        for p in sorted(features.iterdir())
        if (p / "templates").is_dir()
    }
    env = Environment(
        loader=ChoiceLoader(
            [
                FileSystemLoader(APP_DIR / "core" / "templates"),
                PrefixLoader(prefix),
            ]
        ),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
        extensions=["jinja2.ext.do", "jinja2.ext.loopcontrols"],
    )
    env.filters["money"] = money.format_vnd
    env.filters["money0"] = lambda v: money.format_vnd(v, blank_zero=True)
    env.filters["words"] = money.amount_in_words
    env.filters["date"] = fmt_date
    env.filters["datetime"] = fmt_datetime
    env.filters["qty"] = fmt_qty
    env.filters["pct"] = fmt_pct
    env.filters["tojson_attr"] = lambda v: json.dumps(
        v, ensure_ascii=False, default=str
    )
    return env


def resolve_lang(request: Request) -> str:
    lang = request.cookies.get(LANG_COOKIE)
    user: CurrentUser | None = getattr(request.state, "user", None)
    if lang not in LANGUAGES:
        lang = (user.language if user else None) or DEFAULT_LANGUAGE
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def resolve_theme(request: Request) -> str:
    theme = request.cookies.get(THEME_COOKIE)
    user: CurrentUser | None = getattr(request.state, "user", None)
    if theme not in THEMES:
        theme = (user.theme if user else None) or "system"
    return theme if theme in THEMES else "system"


def _active(path: str, href: str, match: str | None) -> bool:
    target = (match or href).split("?")[0]
    if target == "/":
        return path == "/"
    return path == target or path.startswith(target.rstrip("/") + "/")


def build_nav(user: CurrentUser | None, path: str, translate) -> list[dict]:
    if user is None or user.role is None:
        return []
    load_features()
    items_by_section: dict[str, list[dict]] = {}
    candidates = []
    for item in registry.nav:
        if item.perm and not user.can(item.perm):
            continue
        d = {
            "label": translate(item.label),
            "href": item.href,
            "icon": item.icon,
            "active": False,
            "badge": None,
        }
        items_by_section.setdefault(item.section, []).append(d)
        if _active(path, item.href, item.match):
            candidates.append(
                (len((item.match or item.href).split("?")[0]), d)
            )
    if candidates:
        candidates.sort(key=lambda x: -x[0])
        candidates[0][1]["active"] = True
    out = []
    for key, label, icon in SECTIONS:
        items = items_by_section.get(key)
        if items:
            out.append(
                {
                    "key": key,
                    "label": translate(label),
                    "icon": icon,
                    "items": items,
                }
            )
    return out


def build_actions(user: CurrentUser | None, translate) -> list[dict]:
    if user is None or user.role is None:
        return []
    load_features()
    return [
        {
            "label": translate(a.label),
            "href": a.href,
            "icon": a.icon,
            "shortcut": a.shortcut,
            "group": translate(a.group),
        }
        for a in registry.actions
        if not a.perm or user.can(a.perm)
    ]


_ASSET_VERSION: str | None = None


def asset_version() -> str:
    """Cache-busting string for CSS/JS.

    Changes whenever the static files are rebuilt.
    """
    global _ASSET_VERSION
    if _ASSET_VERSION is None:
        static = APP_DIR / "static"
        mtimes = [
            p.stat().st_mtime for p in (static / "css").glob("*.css")
        ] + [p.stat().st_mtime for p in (static / "js").glob("*.js")]
        _ASSET_VERSION = format(int(max(mtimes, default=0)), "x")
    return _ASSET_VERSION


def _companies_for(user: CurrentUser | None) -> list[dict]:
    if user is None:
        return []
    from app.core.registry import ROLE_LABELS
    from app.features.org.service import user_companies

    with session_scope() as db:
        return [
            {
                "id": c.id,
                "name": c.display_name,
                "tax_code": c.tax_code,
                "role_label": ROLE_LABELS.get(m.role, m.role),
                "current": c.id == user.company_id,
            }
            for c, m in user_companies(db, user.id)
        ]


def base_context(request: Request) -> dict:
    user: CurrentUser | None = getattr(request.state, "user", None)
    lang = resolve_lang(request)
    _ = get_translator(lang)
    settings = get_settings()

    def get_flashes() -> list[dict]:
        request.state.flash_consumed = True
        return [
            {
                "kind": f.get("level", "info"),
                "level": f.get("level", "info"),
                "message": f.get("message", ""),
            }
            for f in getattr(request.state, "flashes", [])
        ]

    company = getattr(request.state, "company", None)
    ai_enabled = False
    if user is not None and company is not None and user.can("ai.use"):
        ai_enabled = bool(
            (company.settings or {}).get("ai", {}).get("enabled")
        )
    return {
        "user": user,
        "company": company,
        "companies": _companies_for(user)
        if user is not None and not is_htmx(request)
        else [],
        "nav": build_nav(user, request.url.path, _),
        "quick_actions": build_actions(user, _),
        "product_name": settings.product_name,
        "ai_enabled": ai_enabled,
        "csrf_token": getattr(request.state, "csrf_token", "") or "",
        "lang": lang,
        "theme": resolve_theme(request),
        "_": _,
        "get_flashes": get_flashes,
        "can": (lambda perm: bool(user and user.can(perm))),
        "is_htmx": is_htmx(request),
        "path": request.url.path,
        "today": today(),
        "now": datetime.now(timezone.utc),
        "allow_signup": settings.allow_signup,
        "asset_version": asset_version(),
    }


templates = Jinja2Templates(env=_build_env())


def render(
    request: Request,
    name: str,
    context: dict | None = None,
    status_code: int = 200,
    headers: dict | None = None,
) -> HTMLResponse:
    """Render a template with shared and page-specific variables.

    Page variables override the shared ones (base_context) on name
    clashes.
    """
    ctx = base_context(request)
    ctx.update(context or {})
    return templates.TemplateResponse(
        request, name, ctx, status_code=status_code, headers=headers
    )


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def client_ip(request: Request) -> str | None:
    """Return the client IP address.

    Does NOT read X-Forwarded-For directly (it can be spoofed): uvicorn
    replaces `client` with the real IP only when the request comes from
    a trusted proxy (`--forwarded-allow-ips` / FORWARDED_ALLOW_IPS).
    """
    return request.client.host if request.client else None


def is_https(request: Request) -> bool:
    return request.url.scheme == "https"


def cookie_secure(request: Request | None = None) -> bool:
    """Secure flag for cookies.

    Enabled by configuration or automatically for https requests.
    """
    return get_settings().cookie_secure or (
        request is not None and is_https(request)
    )


def actor(request: Request) -> CurrentUser | None:
    """Current user with the IP attached, for audit logging."""
    user = getattr(request.state, "user", None)
    if user is not None:
        user.ip = client_ip(request)
    return user


def get_ctx(request: Request) -> Ctx:
    """Action context of the request (logged in + company selected)."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise LoginRequiredError()
    if user.company_id is None:
        raise CompanyRequiredError()
    via = (
        "api" if getattr(request.state, "auth_via", None) == "token" else "web"
    )
    return Ctx(
        company_id=user.company_id, user=user, via=via, ip=client_ip(request)
    )


def get_tenant_db(request: Request):
    """Dependency: DB session bound to the user's company.

    Enables the automatic tenant filter.
    """
    get_engine()
    db = SessionLocal()
    user = getattr(request.state, "user", None)
    if user is not None and user.company_id is not None:
        bind_tenant(db, user.company_id)
    try:
        yield db
    finally:
        db.close()


def safe_next(url: str | None, default: str = "/") -> str:
    """Allow only same-site redirects."""
    if not url:
        return default
    parsed = urlparse(url)
    if (
        parsed.scheme
        or parsed.netloc
        or not url.startswith("/")
        or url.startswith("//")
        or "\\" in url
    ):
        return default
    return url


# --------------------------------------------------------------- flash


def _flash_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().resolved_secret(), salt="flash")


def redirect(
    url: str,
    message: str | None = None,
    level: str = "success",
    request: Request | None = None,
) -> Response:
    """Redirect with 303 and a flash message.

    Uses HX-Redirect for HTMX requests.
    """
    if request is not None and is_htmx(request):
        resp: Response = Response(
            status_code=204, headers={"HX-Redirect": url}
        )
    else:
        resp = RedirectResponse(url, status_code=303)
    if message:
        add_flash(resp, message, level, request=request)
    return resp


def add_flash(
    response: Response,
    message: str,
    level: str = "success",
    request: Request | None = None,
) -> None:
    value = _flash_serializer().dumps([{"message": message, "level": level}])
    response.set_cookie(
        FLASH_COOKIE,
        value,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(request),
    )


def _read_flashes(request: Request) -> list[dict]:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return []
    try:
        data = _flash_serializer().loads(raw)
        return data if isinstance(data, list) else []
    except BadSignature:
        return []


# ---------------------------------------------------------- middleware


def _load_from_session(token: str, ip: str | None):
    from app.features.org.models import Company

    with session_scope() as db:
        found = load_session(db, token)
        if found is None:
            return None
        sess, user = found
        cu = build_current_user(
            db, user, sess.company_id, ip=ip, session_id=sess.id
        )
        company = db.get(Company, cu.company_id) if cu.company_id else None
        if company is not None:
            db.expunge(company)
        return cu, sess.csrf_token, company


def _load_from_api_token(raw: str, ip: str | None):
    from app.features.org.models import Company

    with session_scope() as db:
        found = load_api_token(db, raw)
        if found is None:
            return None
        tok, user = found
        cu = build_current_user(
            db, user, tok.company_id, ip=ip, api_scopes=tok.scope_set
        )
        if cu.company_id is None:
            return None
        tok.last_used_at = datetime.now(timezone.utc)
        company = db.get(Company, cu.company_id)
        db.expunge(company)
        return cu, company


CSP = (
    "default-src 'self'; img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-eval'; font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'self'"
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Load the user, company, CSRF token and flashes; add headers.

    The user comes from the session cookie or a Bearer API token.
    Security headers are added to every response.
    """

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        request.state.user = None
        request.state.company = None
        request.state.csrf_token = None
        request.state.auth_via = None
        new_anon_token = None
        if not request.url.path.startswith("/static"):
            ip = client_ip(request)
            auth = request.headers.get("authorization", "")
            # API tokens are only for /api/ – web routes always use the
            # session cookie (+ CSRF).
            if auth.lower().startswith(
                "bearer "
            ) and request.url.path.startswith("/api/"):
                loaded = await run_in_threadpool(
                    _load_from_api_token, auth[7:].strip(), ip
                )
                if loaded is None:
                    return JSONResponse(
                        {
                            "error": {
                                "message": "Invalid or expired API token",
                                "type": "invalid_request_error",
                                "code": "invalid_api_key",
                            }
                        },
                        status_code=401,
                    )
                request.state.user, request.state.company = loaded
                request.state.auth_via = "token"
            else:
                token = request.cookies.get(settings.session_cookie)
                if token:
                    loaded = await run_in_threadpool(
                        _load_from_session, token, ip
                    )
                    if loaded:
                        (
                            request.state.user,
                            request.state.csrf_token,
                            request.state.company,
                        ) = loaded
                        request.state.auth_via = "session"
                if request.state.user is None:
                    anon = request.cookies.get(CSRF_COOKIE)
                    if not anon:
                        anon = new_anon_token = secrets.token_urlsafe(32)
                    request.state.csrf_token = anon
            request.state.flashes = _read_flashes(request)

        response = await call_next(request)

        if new_anon_token:
            response.set_cookie(
                CSRF_COOKIE,
                new_anon_token,
                httponly=True,
                samesite="strict",
                secure=cookie_secure(request),
            )
        if getattr(request.state, "flash_consumed", False) and getattr(
            request.state, "flashes", None
        ):
            sets_flash = any(
                h.startswith(f"{FLASH_COOKIE}=")
                for h in response.headers.getlist("set-cookie")
            )
            if not sets_flash:
                response.delete_cookie(FLASH_COOKIE)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        response.headers.setdefault(
            "Cross-Origin-Opener-Policy", "same-origin"
        )
        if is_https(request):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


# -------------------------------------------------------------- errors


def _users_exist() -> bool:
    from app.features.auth.service import users_exist

    with session_scope() as db:
        return users_exist(db)


def _wants_json(request: Request) -> bool:
    return (
        request.url.path.startswith("/api/")
        or getattr(request.state, "auth_via", None) == "token"
    )


def _json_error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": code, "code": code}},
        status_code=status,
    )


def _error_page(
    request: Request, status: int, title: str, message: str
) -> Response:
    if _wants_json(request):
        return _json_error(
            status,
            message,
            {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                422: "business_error",
            }.get(status, "error"),
        )
    if is_htmx(request):
        return render(
            request,
            "partials/alert.html",
            {"level": "error", "kind": "error", "message": message},
            status_code=status,
            headers={"HX-Retarget": "#flash-area", "HX-Reswap": "innerHTML"},
        )
    return render(
        request,
        "errors/error.html",
        {"status": status, "title": title, "message": message},
        status_code=status,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoginRequiredError)
    async def _login_required(request: Request, _exc: LoginRequiredError):
        if _wants_json(request):
            return _json_error(401, "Authentication required", "unauthorized")
        has_users = await run_in_threadpool(_users_exist)
        url = "/login?next=" + quote(
            request.url.path
            + (f"?{request.url.query}" if request.url.query else "")
        )
        if not has_users:
            url = "/setup"
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": url})
        return RedirectResponse(url, status_code=303)

    @app.exception_handler(CompanyRequiredError)
    async def _company_required(request: Request, _exc: CompanyRequiredError):
        if _wants_json(request):
            return _json_error(403, "No company selected", "forbidden")
        url = "/select-company"
        if is_htmx(request):
            return Response(status_code=204, headers={"HX-Redirect": url})
        return RedirectResponse(url, status_code=303)

    @app.exception_handler(PermissionDeniedError)
    async def _forbidden(request: Request, exc: PermissionDeniedError):
        _ = get_translator(resolve_lang(request))
        return _error_page(request, 403, _("Không có quyền"), _(exc.message))

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError):
        _ = get_translator(resolve_lang(request))
        return _error_page(request, 404, _("Không tìm thấy"), _(exc.message))

    @app.exception_handler(BusinessError)
    async def _business(request: Request, exc: BusinessError):
        if _wants_json(request):
            body = {
                "error": {
                    "message": exc.message,
                    "type": "business_error",
                    "code": exc.code,
                    "details": exc.details,
                }
            }
            return JSONResponse(
                json.loads(json.dumps(body, default=str)), status_code=422
            )
        if is_htmx(request):
            return _error_page(request, 422, "", exc.message)
        ref = urlparse(request.headers.get("referer", ""))
        back = safe_next(
            (ref.path + (f"?{ref.query}" if ref.query else "")) or None, "/"
        )
        return redirect(back, exc.message, "error")

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        """Name only the invalid fields, never echo submitted data."""
        fields = sorted(
            {
                ".".join(str(x) for x in e.get("loc", ())[1:])
                for e in exc.errors()
            }
        )
        if _wants_json(request):
            return JSONResponse(
                {
                    "error": {
                        "message": "Dữ liệu gửi lên không hợp lệ",
                        "type": "invalid_request_error",
                        "code": "validation",
                        "fields": fields,
                    }
                },
                status_code=422,
            )
        _ = get_translator(resolve_lang(request))
        return _error_page(
            request,
            422,
            _("Dữ liệu không hợp lệ"),
            _("Dữ liệu gửi lên không hợp lệ")
            + (f": {', '.join(fields)}" if fields else ""),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        _ = get_translator(resolve_lang(request))
        title = {
            404: _("Không tìm thấy"),
            405: _("Phương thức không hợp lệ"),
            413: _("File quá lớn"),
        }.get(exc.status_code, _("Lỗi"))
        return _error_page(
            request,
            exc.status_code,
            title,
            str(exc.detail) if exc.status_code != 404 else title,
        )


async def form_data(request: Request) -> dict:
    """Dependency: form data as a dict.

    Values are strings; files stay as UploadFile.
    """
    form = await request.form()
    return {k: v for k, v in form.multi_items()}


async def form_multi(request: Request):
    """Dependency: the full form (keeps repeated values, for grids)."""
    return await request.form()
