"""Multi-company support: action context and automatic tenant filter.

Layer 1 (mandatory): every service filters on
`company_id == ctx.company_id`.
Layer 2 (defense in depth): when `session.info["company_id"]` is set
(bind_tenant), every ORM SELECT automatically adds a company_id
condition for TenantMixin tables, and flush refuses to write objects
that belong to another company.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.core.db import TenantMixin
from app.core.errors import PermissionDeniedError

SYSTEM = "system"


@dataclass
class Ctx:
    """Who is acting, on which company, and through which channel.

    Channels: web/api/ai/cli/system.
    """

    company_id: int
    # CurrentUser; None = system (full permissions)
    user: object | None = None
    via: str = "web"
    ip: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def user_id(self) -> int | None:
        return getattr(self.user, "id", None)

    @property
    def user_name(self) -> str | None:
        if self.user is None:
            return SYSTEM
        return getattr(self.user, "full_name", None) or getattr(
            self.user, "email", None
        )

    def can(self, perm: str) -> bool:
        return self.user is None or bool(self.user.can(perm))

    def require(self, perm: str) -> None:
        if not self.can(perm):
            raise PermissionDeniedError()


def system_ctx(company_id: int, via: str = "system") -> Ctx:
    return Ctx(company_id=company_id, user=None, via=via)


def bind_tenant(db: Session, company_id: int | None) -> None:
    """Bind the session to a company, enabling automatic filtering."""
    if company_id is None:
        db.info.pop("company_id", None)
    else:
        db.info["company_id"] = company_id


@event.listens_for(Session, "do_orm_execute")
def _tenant_filter(state) -> None:
    cid = state.session.info.get("company_id")
    if cid is None or state.execution_options.get("all_tenants"):
        return
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            with_loader_criteria(
                TenantMixin,
                lambda cls: cls.company_id == cid,
                include_aliases=True,
            )
        )


@event.listens_for(Session, "before_flush")
def _tenant_guard(session: Session, _flush_context, _instances) -> None:
    cid = session.info.get("company_id")
    if cid is None:
        return
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, TenantMixin):
            if obj.company_id is None:
                obj.company_id = cid
            elif obj.company_id != cid:
                raise PermissionDeniedError(
                    "Dữ liệu không thuộc công ty đang làm việc"
                )
    for obj in session.deleted:
        if isinstance(obj, TenantMixin) and obj.company_id != cid:
            raise PermissionDeniedError(
                "Dữ liệu không thuộc công ty đang làm việc"
            )
