"""Dashboard page (the full version is completed at integration)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.security import CurrentUser, require
from app.core.web import get_tenant_db, render

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    user: CurrentUser = Depends(require("dashboard.view")),
    db=Depends(get_tenant_db),
):
    from app.features.dashboard.service import build

    return render(request, "dashboard/index.html", build(db, user))
