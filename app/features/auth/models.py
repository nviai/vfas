"""Users, login sessions, failed login attempts and API tokens."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TenantMixin


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(
        String(200), unique=True
    )  # always lowercase
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str | None] = mapped_column(String(8))
    theme: Mapped[str | None] = mapped_column(String(8))
    last_company_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # None = the address was never confirmed (self sign-up waiting for
    # the verification email).
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )  # sha256(token)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int | None] = mapped_column(Integer)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))


class LoginAttempt(Base):
    """Rate-limited event.

    Covers failed logins, wrong current-password entries, sign-ups and
    company creation.
    """

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(300), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="login", index=True)
    email: Mapped[str] = mapped_column(String(200), default="", index=True)
    ip: Mapped[str] = mapped_column(String(64), default="", index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ApiToken(TenantMixin, Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))  # shown as: vfas_ab12…
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(
        String(100), default="read"
    )  # "read write_draft"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    @property
    def scope_set(self) -> frozenset[str]:
        return frozenset((self.scopes or "").split())
