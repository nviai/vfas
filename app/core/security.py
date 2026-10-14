"""Authentication and authorization helpers.

Covers the current user, per-company roles, permissions, passwords
(argon2), sessions, API tokens and CSRF.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import LoginRequiredError, PermissionDeniedError
from app.core.registry import (
    DRAFT_SUFFIXES,
    OWNER,
    READ_SUFFIXES,
    ROLE_LABELS,
    roles_for,
)


@dataclass
class CurrentUser:
    id: int
    email: str
    full_name: str
    is_superadmin: bool = False
    company_id: int | None = None
    role: str | None = None  # role in the currently selected company
    language: str | None = None
    theme: str | None = None
    ip: str | None = None
    session_id: str | None = None
    # Scopes (read, write_draft) when authenticated with an API token.
    # None = web session.
    api_scopes: frozenset[str] | None = None
    extra: dict = field(default_factory=dict)

    def can(self, perm: str) -> bool:
        if self.role is None:
            return False
        if self.api_scopes is not None:
            # API token: read (.view/.export, ai.use) and draft creation
            # (.create) only; no posting, no administration.
            if perm.endswith(READ_SUFFIXES) or perm == "ai.use":
                needed = "read"
            elif perm.endswith(DRAFT_SUFFIXES):
                needed = "write_draft"
            else:
                return False
            if needed not in self.api_scopes:
                return False
        if self.role == OWNER:
            return True
        return self.role in roles_for(perm)

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role or "", "")

    @property
    def display_name(self) -> str:
        return self.full_name or self.email

    @property
    def username(self) -> str:  # compatibility with old username usage
        return self.email

    @property
    def initials(self) -> str:
        parts = [
            p
            for p in (self.full_name or self.email).replace("@", " ").split()
            if p
        ]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[-2][0] + parts[-1][0]).upper()


# ----------------------------------------------------------- passwords

# argon2id parameters per the OWASP recommendation (19 MiB, t=2, p=1):
# strong enough without exhausting the RAM of a 1 GB VPS under a burst
# of login requests. Old hashes (default parameters) are rehashed
# automatically when the user logs in (needs_rehash).
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# Limit on concurrent hash operations (~19 MiB each).
_HASH_SLOTS = threading.BoundedSemaphore(4)
_DUMMY_HASH: str | None = None

MIN_PASSWORD = 8
MAX_PASSWORD = 128
# Overly common passwords (tried first in any password guessing).
COMMON_PASSWORDS = frozenset(
    """
12345678 123456789 1234567890 12341234 11111111 00000000 88888888 66666666
87654321 123123123 11223344 password password1 password123 passw0rd p@ssw0rd
p@ssword admin123 admin@123 administrator qwerty123 qwertyuiop 1qaz2wsx
abc12345 abcd1234 iloveyou 1q2w3e4r 1q2w3e4r5t zaq12wsx matkhau matkhau1
matkhau123 matkhau@123 123456aa 12345678a a12345678 aa123456 vietnam1
vietnam123 hanoi123 saigon123 anhyeuem emyeuanh 147258369 147852369 999999999
111111111 demo12345 ketoan123 changeme welcome1 letmein1
""".split()
)


def hash_password(password: str) -> str:
    with _HASH_SLOTS:
        return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    with _HASH_SLOTS:
        try:
            return _hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False


def dummy_verify(password: str) -> None:
    """Run a dummy hash when the email does not exist.

    Keeps the response time from revealing which emails have accounts.
    """
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    verify_password(_DUMMY_HASH, password or "")


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(
    password: str, email: str | None = None
) -> str | None:
    pw = password or ""
    if len(pw) < MIN_PASSWORD:
        return "Mật khẩu phải có ít nhất 8 ký tự"
    if len(pw) > MAX_PASSWORD:
        return "Mật khẩu tối đa 128 ký tự"
    low = pw.lower()
    if low in COMMON_PASSWORDS or len(set(pw)) <= 2:
        return "Mật khẩu quá phổ biến, dễ bị đoán. Hãy chọn mật khẩu khác"
    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 4 and (
            low == local
            or low.startswith(local)
            and len(low) - len(local) <= 4
        ):
            return "Mật khẩu không được giống email"
    return None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# --------------------------------------------------------- rate limits
# Stored in the DB so that all workers share them.

LOGIN_WINDOW = timedelta(minutes=15)
MAX_FAILS_EMAIL_IP = 8  # one email from one IP within 15 minutes
MAX_FAILS_IP = 30  # any email from one IP within 15 minutes
# One email from any IP within 1 hour (blocks distributed guessing; the
# threshold is high so it is hard to abuse for locking out others).
MAX_FAILS_EMAIL = 50


def _norm_email(email: str | None) -> str:
    return (email or "").strip().lower()[:200]


def _count(
    db: Session,
    kind: str,
    since: datetime,
    *,
    email: str | None = None,
    ip: str | None = None,
) -> int:
    from app.features.auth.models import LoginAttempt

    q = select(func.count(LoginAttempt.id)).where(
        LoginAttempt.kind == kind, LoginAttempt.at >= since
    )
    if email is not None:
        q = q.where(LoginAttempt.email == email)
    if ip is not None:
        q = q.where(LoginAttempt.ip == ip)
    return db.scalar(q) or 0


def record_event(
    db: Session,
    kind: str,
    *,
    email: str | None = None,
    ip: str | None = None,
    commit: bool = True,
) -> None:
    from app.features.auth.models import LoginAttempt

    e, i = _norm_email(email), (ip or "")[:64]
    db.add(
        LoginAttempt(
            key=f"{kind}|{e}|{i}"[:300], kind=kind, email=e, ip=i, at=_now()
        )
    )
    db.execute(
        delete(LoginAttempt).where(
            LoginAttempt.at < _now() - timedelta(days=1)
        )
    )
    if commit:
        db.commit()


def rate_limited(
    db: Session,
    kind: str,
    limit: int,
    window: timedelta,
    *,
    email: str | None = None,
    ip: str | None = None,
) -> bool:
    if limit <= 0:
        return False
    return (
        _count(
            db,
            kind,
            _now() - window,
            email=_norm_email(email) if email is not None else None,
            ip=ip if ip is not None else None,
        )
        >= limit
    )


def login_blocked(db: Session, email: str, ip: str | None) -> bool:
    e, i = _norm_email(email), ip or ""
    since = _now() - LOGIN_WINDOW
    return (
        _count(db, "login", since, email=e, ip=i) >= MAX_FAILS_EMAIL_IP
        or _count(db, "login", since, ip=i) >= MAX_FAILS_IP
        or _count(db, "login", _now() - timedelta(hours=1), email=e)
        >= MAX_FAILS_EMAIL
    )


def login_failed(db: Session, email: str, ip: str | None) -> None:
    record_event(db, "login", email=email, ip=ip)


def login_reset(db: Session, email: str, ip: str | None) -> None:
    from app.features.auth.models import LoginAttempt

    db.execute(
        delete(LoginAttempt).where(
            LoginAttempt.kind == "login",
            LoginAttempt.email == _norm_email(email),
            LoginAttempt.ip == (ip or ""),
        )
    )


# ------------------------------------------------------------ sessions


def create_session(
    db: Session,
    user,
    hours: int,
    ip: str | None,
    company_id: int | None = None,
    user_agent: str | None = None,
) -> str:
    from app.features.auth.models import UserSession

    token = secrets.token_urlsafe(32)
    now = _now()
    db.add(
        UserSession(
            id=hash_token(token),
            user_id=user.id,
            company_id=company_id,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(hours=hours),
            ip=ip,
            user_agent=(user_agent or "")[:300],
        )
    )
    db.execute(delete(UserSession).where(UserSession.expires_at < now))
    return token


def load_session(db: Session, token: str | None):
    """Return (UserSession, User) if the token is valid, else None."""
    from app.features.auth.models import User, UserSession

    if not token:
        return None
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.id == hash_token(token))
    ).first()
    if row is None:
        return None
    sess, user = row
    now = _now()
    if _aware(sess.expires_at) < now or not user.is_active:
        return None
    from app.core.config import get_settings

    idle = get_settings().session_idle_minutes
    last = (
        _aware(sess.last_seen_at)
        if sess.last_seen_at
        else _aware(sess.created_at)
    )
    if idle and now - last > timedelta(minutes=idle):
        db.delete(sess)
        return None
    # Write at most once per minute
    if now - last > timedelta(seconds=60):
        sess.last_seen_at = now
    return sess, user


def destroy_session(db: Session, token: str | None) -> None:
    from app.features.auth.models import UserSession

    if token:
        db.execute(
            delete(UserSession).where(UserSession.id == hash_token(token))
        )


def destroy_user_sessions(
    db: Session, user_id: int, except_id: str | None = None
) -> None:
    from app.features.auth.models import UserSession

    q = delete(UserSession).where(UserSession.user_id == user_id)
    if except_id:
        q = q.where(UserSession.id != except_id)
    db.execute(q)


def membership_role(
    db: Session, user_id: int, company_id: int | None
) -> str | None:
    if company_id is None:
        return None
    from app.features.org.models import Company, Membership

    row = db.execute(
        select(Membership.role)
        .join(Company, Company.id == Membership.company_id)
        .where(
            Membership.user_id == user_id,
            Membership.company_id == company_id,
            Membership.is_active.is_(True),
            Company.is_active.is_(True),
        )
    ).first()
    return row[0] if row else None


def build_current_user(
    db: Session,
    user,
    company_id: int | None,
    *,
    ip=None,
    session_id=None,
    api_scopes=None,
) -> CurrentUser:
    role = membership_role(db, user.id, company_id)
    return CurrentUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_superadmin=user.is_superadmin,
        company_id=company_id if role else None,
        role=role,
        language=user.language,
        theme=user.theme,
        ip=ip,
        session_id=session_id,
        api_scopes=api_scopes,
    )


# ---------------------------------------------------------- API tokens

TOKEN_PREFIX = "vfas_"
API_SCOPES = ("read", "write_draft")


def new_api_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def load_api_token(db: Session, raw: str):
    """Return (ApiToken, User) if the token is valid."""
    from app.features.auth.models import ApiToken, User

    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    row = db.execute(
        select(ApiToken, User)
        .join(User, User.id == ApiToken.user_id)
        .where(ApiToken.token_hash == hash_token(raw))
        .execution_options(all_tenants=True)
    ).first()
    if row is None:
        return None
    tok, user = row
    if tok.revoked_at is not None or not user.is_active:
        return None
    if tok.expires_at is not None and _aware(tok.expires_at) < _now():
        return None
    return tok, user


# -------------------------------------------------------- dependencies


def get_current_user_optional(request: Request) -> CurrentUser | None:
    return getattr(request.state, "user", None)


def get_current_user(request: Request) -> CurrentUser:
    """Logged-in user (who may not have selected a company yet)."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise LoginRequiredError()
    return user


class CompanyRequiredError(Exception):
    """Logged in but no company selected or available yet."""


def get_member(request: Request) -> CurrentUser:
    """Logged-in user who is working on a company."""
    user = get_current_user(request)
    if user.company_id is None or user.role is None:
        raise CompanyRequiredError()
    return user


def require(perm: str):
    """Dependency: logged in, company selected and has `perm`."""

    def dep(user: CurrentUser = Depends(get_member)) -> CurrentUser:
        if not user.can(perm):
            raise PermissionDeniedError()
        return user

    return dep


def require_superadmin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not user.is_superadmin or user.api_scopes is not None:
        raise PermissionDeniedError()
    return user


def check(user: CurrentUser, perm: str) -> None:
    if not user.can(perm):
        raise PermissionDeniedError()


# ---------------------------------------------------------------- CSRF

CSRF_COOKIE = "vfas_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def csrf_protect(request: Request) -> None:
    """Global dependency: every write request needs a valid CSRF token.

    Web sessions: the token is bound to the session (DB). Not logged
    in: double-submit cookie. Requests authenticated with an API token
    (Bearer) do not use cookies, so they need no CSRF token.
    """
    if (
        request.method in SAFE_METHODS
        or getattr(request.state, "auth_via", None) == "token"
    ):
        return
    expected = getattr(request.state, "csrf_token", None)
    sent = request.headers.get(CSRF_HEADER)
    if not sent:
        ctype = request.headers.get("content-type", "")
        if ctype.startswith(
            ("application/x-www-form-urlencoded", "multipart/form-data")
        ):
            form = await request.form()
            sent = form.get(CSRF_FIELD)
    if (
        not expected
        or not sent
        or not hmac.compare_digest(str(expected), str(sent))
    ):
        raise PermissionDeniedError(
            "Phiên làm việc đã hết hạn hoặc yêu cầu không hợp lệ (CSRF). "
            "Vui lòng tải lại trang."
        )
