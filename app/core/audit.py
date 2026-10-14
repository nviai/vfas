"""Append-only audit log.

The table only allows INSERT. UPDATE/DELETE are blocked at two layers:
the ORM events below and DB triggers (core/triggers.py).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL = platform-level event (login, company creation, ...).
    company_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(200))
    via: Mapped[str] = mapped_column(String(16), default="web")
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    summary: Mapped[str | None] = mapped_column(String(500))
    before: Mapped[str | None] = mapped_column(Text)
    after: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))

    @property
    def before_dict(self) -> dict | None:
        return json.loads(self.before) if self.before else None

    @property
    def after_dict(self) -> dict | None:
        return json.loads(self.after) if self.after else None


@event.listens_for(AuditLog, "before_update")
def _no_update(*_args):
    raise RuntimeError("audit_logs is append-only: updates are not allowed")


@event.listens_for(AuditLog, "before_delete")
def _no_delete(*_args):
    raise RuntimeError("audit_logs is append-only: deletes are not allowed")


def _json_default(o: Any):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    return str(o)


def to_json(data: dict | None) -> str | None:
    if data is None:
        return None
    return json.dumps(
        data, ensure_ascii=False, default=_json_default, sort_keys=True
    )


def snapshot(obj, fields) -> dict:
    return {f: getattr(obj, f) for f in fields}


def _actor_label(user) -> str | None:
    """Actor label in the form "Full Name <email>".

    The full name is chosen by the user, so the (fixed) email is always
    included.
    """
    name = (getattr(user, "full_name", None) or "").strip()
    email = (getattr(user, "email", None) or "").strip()
    if name and email and name.lower() != email.lower():
        label = f"{name} <{email}>"
    else:
        label = email or name
    return label[:200] or None


def write_audit(
    db: Session,
    ctx=None,
    *,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    summary: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
    company_id: int | None = None,
    user=None,
    ip: str | None = None,
    via: str | None = None,
) -> AuditLog:
    """Write one log row in the same transaction as the business action.

    `ctx` is an app.core.tenancy.Ctx (company, user, via, ip). For
    platform-level events pass ctx=None together with user/company_id.
    """
    if ctx is not None:
        company_id = company_id if company_id is not None else ctx.company_id
        user = user or ctx.user
        via = via or ctx.via
        ip = ip or ctx.ip
    log = AuditLog(
        company_id=company_id,
        at=datetime.now(timezone.utc),
        user_id=getattr(user, "id", None),
        username=_actor_label(user)
        if user
        else ("system" if ctx is not None else None),
        via=via or "web",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=(summary or "")[:500] or None,
        before=to_json(before),
        after=to_json(after),
        reason=reason,
        ip=ip or getattr(user, "ip", None),
    )
    db.add(log)
    return log
