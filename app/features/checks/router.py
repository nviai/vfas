"""Data checks: run every check registered by the modules.

Checks are registered through ``registry.data_check``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.core.registry import CheckIssue, load_features, registry
from app.core.security import CurrentUser, require
from app.core.web import get_ctx, get_tenant_db, render

router = APIRouter()
log = logging.getLogger(__name__)


def run_checks(db, ctx, *, codes: set[str] | None = None) -> list[dict]:
    """Run the checks the user is allowed to see.

    Return a list of ``{code, title, issues, error}`` dicts.
    """
    load_features()
    out = []
    for chk in registry.checks:
        if codes and chk.code not in codes:
            continue
        if chk.perm and not ctx.can(chk.perm):
            continue
        try:
            issues: list[CheckIssue] = list(chk.func(db, ctx) or [])
            out.append(
                {
                    "code": chk.code,
                    "title": chk.title,
                    "issues": issues,
                    "error": None,
                }
            )
        except Exception as e:  # one failing check must not break the page
            log.exception("Check %s failed", chk.code)
            db.rollback()
            out.append(
                {
                    "code": chk.code,
                    "title": chk.title,
                    "issues": [],
                    "error": str(e),
                }
            )
    return out


@router.get("/checks")
def checks_page(
    request: Request,
    _u: CurrentUser = Depends(require("checks.view")),
    db=Depends(get_tenant_db),
):
    results = run_checks(db, get_ctx(request))
    total = sum(len(r["issues"]) for r in results)
    errors = sum(
        1 for r in results for i in r["issues"] if i.severity == "error"
    )
    return render(
        request,
        "checks/index.html",
        {
            "results": results,
            "total": total,
            "errors": errors,
            "breadcrumbs": [("Kiểm tra dữ liệu", None)],
        },
    )
