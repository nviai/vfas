from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends, Request

from app.core.config import get_settings
from app.core.regimes import regime_of
from app.core.security import CurrentUser, get_current_user
from app.core.web import render

router = APIRouter()


def app_version() -> str:
    try:
        return version("vfas")
    except PackageNotFoundError:
        return "2.0.0"


@router.get("/about")
def about(request: Request, _u: CurrentUser = Depends(get_current_user)):
    return render(
        request,
        "about/index.html",
        {
            "version": app_version(),
            "regime": regime_of(getattr(request.state, "company", None)),
            "product": get_settings().product_name,
            "breadcrumbs": [("Giới thiệu", None)],
        },
    )
