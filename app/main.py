"""FastAPI application entry point."""

from __future__ import annotations

import importlib
import logging
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.body_limit import RequestBodyLimitMiddleware

from app.core.config import APP_DIR, Settings, get_settings, set_settings
from app.core.db import configure_engine
from app.core.registry import feature_names, load_features, tolerant
from app.core.security import csrf_protect
from app.core.web import RequestContextMiddleware, install_error_handlers

# Only routers of these modules appear in the OpenAPI docs (/api/docs).
API_FEATURES = {"api"}
log = logging.getLogger("app")

_SECRET_IN_URL = re.compile(
    r"(/invite/|/reset-password/|/verify-email/(?!resend)"
    r"|[?&](?:token|link)=)[^\s\"&]+"
)


class _RedactSecrets(logging.Filter):
    """Hide secret tokens (invites, resets, setup codes) in the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                _SECRET_IN_URL.sub(r"\1***", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def _announce_setup_token() -> None:
    """Log the setup URL with its token when there are no users yet.

    Only someone who can read the server log can complete the setup.
    """
    from app.core.db import session_scope
    from app.features.auth.service import setup_token

    try:
        with session_scope() as db:
            token = setup_token(db)
    except Exception:  # database not migrated yet
        return
    if token:
        base = get_settings().public_url.rstrip("/") or ""
        log.warning(
            "No administrator account yet. Open %s/setup?token=%s for "
            "the first-time setup (or run: python -m app.cli create-admin).",
            base,
            token,
        )


def _include_feature_routers(app: FastAPI) -> None:
    """Mount the `router` of every feature that has a router.py."""
    for name in feature_names():
        try:
            module = importlib.import_module(f"app.features.{name}.router")
        except ModuleNotFoundError as e:
            if e.name == f"app.features.{name}.router":
                continue
            if not tolerant():
                raise
            import warnings

            warnings.warn(f"Skipping router {name}: {e!r}", stacklevel=2)
            continue
        except Exception as e:
            if not tolerant():
                raise
            import warnings

            warnings.warn(f"Skipping router {name}: {e!r}", stacklevel=2)
            continue
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router, include_in_schema=name in API_FEATURES)


def create_app(
    settings: Settings | None = None, *, migrate: bool = False
) -> FastAPI:
    if settings is not None:
        set_settings(settings)
    settings = get_settings()
    from app.core.config import check_secret

    problem = check_secret(settings)
    if problem:
        raise RuntimeError(problem)
    from app.core.mail import mail_enabled, verification_required

    if settings.allow_signup and not mail_enabled():
        log.warning(
            "ALLOW_SIGNUP=true without SMTP: email addresses are not "
            "verified and password reset is unavailable. Configure "
            "SMTP_HOST before opening public sign-up."
        )
    elif verification_required() and not mail_enabled():
        log.warning(
            "EMAIL_VERIFICATION=required but SMTP is not configured: "
            "new accounts cannot be verified."
        )
    if settings.secret_key and len(settings.secret_key) < 32:
        log.warning(
            "SECRET_KEY is shorter than 32 characters; use a longer key."
        )
    settings.ensure_dirs()
    configure_engine(settings.db_url)
    load_features()
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactSecrets) for f in access.filters):
        access.addFilter(_RedactSecrets())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if migrate:
            from app.core.migrate import upgrade_db

            upgrade_db(settings.db_url)
        _announce_setup_token()
        try:
            from app.core.tempfiles import cleanup_imports

            # Remove temp files left by old data import sessions.
            cleanup_imports()
        except Exception:  # noqa: BLE001 - must not block startup
            log.exception("Temp file cleanup failed")
        yield

    app = FastAPI(
        title=f"{settings.product_name} API",
        version="2.0.0",
        lifespan=lifespan,
        dependencies=[Depends(csrf_protect)],
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    # Limit the request size (attachments / imports) plus headroom for
    # form fields.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_size=(settings.max_upload_mb + 2) * 1024 * 1024,
    )
    app.mount(
        "/static", StaticFiles(directory=APP_DIR / "static"), name="static"
    )
    install_error_handlers(app)
    _include_feature_routers(app)

    @app.get("/api/docs", include_in_schema=False)
    def api_docs():
        """Serve Swagger UI with a page-specific CSP.

        Swagger UI is loaded from a CDN and uses inline scripts.
        """
        from fastapi.openapi.docs import get_swagger_ui_html

        resp = get_swagger_ui_html(
            openapi_url="/api/openapi.json",
            title=f"{settings.product_name} API",
        )
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; frame-ancestors 'self'"
        )
        return resp

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    return app
