"""/ui-kit routes.

Enabled only when DEBUG_UI=1 or the user is a superadmin. Serves fake
data and never touches the database.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException

from app.features.ui_kit import fake

router = APIRouter(prefix="/ui-kit", include_in_schema=False)
_env = None


def _guard(request: Request) -> None:
    if os.environ.get("DEBUG_UI") == "1":
        return
    user = getattr(request.state, "user", None)
    if user is not None and getattr(user, "is_superadmin", False):
        return
    raise HTTPException(status_code=404)


def _render(request: Request, page: str) -> HTMLResponse:
    global _env
    _guard(request)
    if _env is None:
        _env = fake.build_env()
    theme = request.cookies.get("theme") or "light"
    lang = request.cookies.get("lang") or "vi"
    try:
        return HTMLResponse(
            fake.render(page, theme=theme, lang=lang, env=_env)
        )
    except KeyError:
        raise HTTPException(status_code=404) from None


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _render(request, "index")


@router.get("/lookup/{kind}")
def lookup(request: Request, kind: str, q: str = ""):
    _guard(request)
    items = fake.LOOKUPS.get(kind, [])
    if q:
        needle = q.lower()
        items = [
            i
            for i in items
            if needle in f"{i['label']} {i.get('hint', '')}".lower()
        ]
    return JSONResponse(items)


@router.get("/search")
def search(request: Request, q: str = ""):
    _guard(request)
    return JSONResponse(fake.SEARCH_RESULTS)


@router.get("/ai-panel", response_class=HTMLResponse)
def ai_panel(request: Request):
    return _render(request, "ai-panel")


@router.get("/{page}", response_class=HTMLResponse)
def page(request: Request, page: str):
    return _render(request, page)
