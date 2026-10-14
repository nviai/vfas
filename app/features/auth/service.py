"""User accounts: creation, login, password changes and API tokens."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import BusinessError, NotFoundError
from app.core.security import (
    API_SCOPES,
    destroy_user_sessions,
    dummy_verify,
    hash_password,
    hash_token,
    login_blocked,
    login_failed,
    login_reset,
    needs_rehash,
    new_api_token,
    validate_password_strength,
    verify_password,
)
from app.features.auth.models import ApiToken, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def users_exist(db: Session) -> bool:
    return (db.scalar(select(func.count(User.id))) or 0) > 0


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def create_user(
    db: Session,
    email: str,
    password: str,
    full_name: str = "",
    *,
    is_superadmin: bool = False,
    email_verified: bool = True,
    commit: bool = True,
) -> User:
    """Create an account.

    `email_verified=False` for self sign-up: the address is confirmed
    later through the verification email.
    """
    email = normalize_email(email)
    if not mail.valid_email(email):
        raise BusinessError("Email không hợp lệ")
    err = validate_password_strength(password, email)
    if err:
        raise BusinessError(err)
    if get_user_by_email(db, email) is not None:
        raise BusinessError("Email này đã có tài khoản")
    u = User(
        email=email,
        full_name=(full_name or "").strip() or email.split("@")[0],
        password_hash=hash_password(password),
        is_active=True,
        is_superadmin=is_superadmin,
        created_at=_now(),
        email_verified_at=_now() if email_verified else None,
    )
    db.add(u)
    db.flush()
    write_audit(
        db,
        None,
        action="user.create",
        entity_type="user",
        entity_id=u.id,
        user=u,
        summary=f"Tạo tài khoản {email}",
    )
    if commit:
        db.commit()
    return u


def authenticate(
    db: Session, email: str, password: str, ip: str | None
) -> User:
    """Log a user in.

    Raises BusinessError with a generic message on failure, so it does
    not reveal whether the email exists.
    """
    email = normalize_email(email)
    if login_blocked(db, email, ip):
        raise BusinessError(
            "Đăng nhập sai quá nhiều lần. Vui lòng thử lại sau 15 phút."
        )
    user = get_user_by_email(db, email)
    if user is None:
        # Keep the response time the same as for an existing email.
        dummy_verify(password)
    if (
        user is None
        or not user.is_active
        or not verify_password(user.password_hash, password or "")
    ):
        login_failed(db, email, ip)
        raise BusinessError("Email hoặc mật khẩu không đúng")
    login_reset(db, email, ip)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = _now()
    write_audit(
        db,
        None,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        user=user,
        ip=ip,
        summary=f"{user.email} đăng nhập",
    )
    return user


def change_password(
    db: Session,
    user: User,
    current: str,
    new: str,
    *,
    keep_session: str | None = None,
    ip: str | None = None,
) -> None:
    """Change the user's password.

    A wrong current password counts as a failed login, which blocks
    password guessing through a stolen session.
    """
    if login_blocked(db, user.email, ip):
        raise BusinessError(
            "Nhập sai quá nhiều lần. Vui lòng thử lại sau 15 phút."
        )
    if not verify_password(user.password_hash, current or ""):
        login_failed(db, user.email, ip)
        raise BusinessError("Mật khẩu hiện tại không đúng")
    set_password(db, user, new, keep_session=keep_session)


def set_password(
    db: Session,
    user: User,
    new: str,
    *,
    keep_session: str | None = None,
    by=None,
) -> None:
    err = validate_password_strength(new, user.email)
    if err:
        raise BusinessError(err)
    user.password_hash = hash_password(new)
    destroy_user_sessions(db, user.id, except_id=keep_session)
    write_audit(
        db,
        None,
        action="user.password",
        entity_type="user",
        entity_id=user.id,
        user=by or user,
        summary=f"Đổi mật khẩu {user.email}",
    )
    db.commit()


def update_profile(
    db: Session,
    user: User,
    *,
    full_name: str | None = None,
    language: str | None = None,
    theme: str | None = None,
) -> User:
    if full_name is not None and full_name.strip():
        user.full_name = full_name.strip()[:200]
    if language in ("vi", "en"):
        user.language = language
    if theme in ("light", "dark", "system"):
        user.theme = theme
    db.commit()
    return user


# ------------------------------------------- email verification, reset

VERIFY_DAYS = 3
RESET_MINUTES = 60


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().resolved_secret(), salt=salt)


def _password_fingerprint(user: User) -> str:
    # Part of the reset token: changing the password (including through
    # the link itself) invalidates every reset link issued before.
    return hashlib.sha256(user.password_hash.encode()).hexdigest()[:16]


def email_verification_token(user: User) -> str:
    return _serializer("verify-email").dumps({"u": user.id, "e": user.email})


def verify_email_token(db: Session, token: str) -> User:
    """Mark the account in a verification token as verified."""
    data: dict = {}
    try:
        data = _serializer("verify-email").loads(
            token or "", max_age=VERIFY_DAYS * 86400
        )
        user = db.get(User, int(data["u"]))
    except (BadSignature, KeyError, TypeError, ValueError):
        user = None
    if user is None or user.email != data.get("e"):
        raise NotFoundError("Link xác minh không hợp lệ hoặc đã hết hạn")
    mark_email_verified(db, user)
    return user


def mark_email_verified(
    db: Session, user: User, *, commit: bool = True
) -> None:
    if user.email_verified_at is not None:
        return
    user.email_verified_at = _now()
    write_audit(
        db,
        None,
        action="user.verify_email",
        entity_type="user",
        entity_id=user.id,
        user=user,
        summary=f"Xác minh email {user.email}",
    )
    if commit:
        db.commit()


def password_reset_token(user: User) -> str:
    return _serializer("password-reset").dumps(
        {"u": user.id, "p": _password_fingerprint(user)}
    )


def user_from_reset_token(db: Session, token: str) -> User:
    data: dict = {}
    try:
        data = _serializer("password-reset").loads(
            token or "", max_age=RESET_MINUTES * 60
        )
        user = db.get(User, int(data["u"]))
    except (BadSignature, KeyError, TypeError, ValueError):
        user = None
    if (
        user is None
        or not user.is_active
        or data.get("p") != _password_fingerprint(user)
    ):
        raise NotFoundError(
            "Link đặt lại mật khẩu không hợp lệ, đã dùng hoặc đã hết hạn"
        )
    return user


def reset_password(db: Session, user: User, new: str) -> None:
    """Set a new password from a reset link.

    The link proves control of the mailbox, so the address also counts
    as verified. Every session of the user is signed out.
    """
    mark_email_verified(db, user, commit=False)
    set_password(db, user, new)


def send_verification_email(user: User, base_url: str, lang: str) -> None:
    link = f"{base_url}/verify-email/{email_verification_token(user)}"
    mail.send(
        mail.render_mail(
            "auth/email/verify.html",
            user.email,
            lang,
            name=user.full_name or user.email,
            link=link,
            days=VERIFY_DAYS,
        ),
        background=True,
    )


def send_password_reset(user: User, base_url: str, lang: str) -> None:
    link = f"{base_url}/reset-password/{password_reset_token(user)}"
    mail.send(
        mail.render_mail(
            "auth/email/reset.html",
            user.email,
            lang,
            name=user.full_name or user.email,
            email=user.email,
            link=link,
            minutes=RESET_MINUTES,
        ),
        background=True,
    )


# ------------------------------------------------------------ API token


def create_api_token(
    db: Session, ctx, name: str, scopes: list[str], days: int | None = None
) -> tuple[ApiToken, str]:
    name = (name or "").strip()[:100]
    if not name:
        raise BusinessError("Đặt tên cho token (vd: n8n, Open WebUI)")
    scopes = [s for s in scopes if s in API_SCOPES] or ["read"]
    raw = new_api_token()
    tok = ApiToken(
        company_id=ctx.company_id,
        user_id=ctx.user_id,
        name=name,
        prefix=raw[:12],
        token_hash=hash_token(raw),
        scopes=" ".join(sorted(set(scopes))),
        created_at=_now(),
        expires_at=(_now() + timedelta(days=days)) if days else None,
    )
    db.add(tok)
    db.flush()
    write_audit(
        db,
        ctx,
        action="api_token.create",
        entity_type="api_token",
        entity_id=tok.id,
        summary=f"Tạo API token '{name}' ({tok.scopes})",
    )
    db.commit()
    return tok, raw


def list_api_tokens(db: Session, ctx) -> list[ApiToken]:
    return list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.company_id == ctx.company_id)
            .order_by(ApiToken.created_at.desc())
        )
    )


def revoke_api_token(db: Session, ctx, token_id: int) -> None:
    tok = db.get(ApiToken, token_id)
    if tok is None or tok.company_id != ctx.company_id:
        raise NotFoundError()
    tok.revoked_at = _now()
    write_audit(
        db,
        ctx,
        action="api_token.revoke",
        entity_type="api_token",
        entity_id=tok.id,
        summary=f"Thu hồi API token '{tok.name}'",
    )
    db.commit()


# ---------------------------------------- first-run setup code (/setup)


def setup_token(db: Session) -> str | None:
    """Return the code required by /setup while no user exists yet.

    The code is SETUP_TOKEN or a generated one, stored in
    DATA_DIR/.setup_token. Returns None once a user exists (the /setup
    page is closed).
    """
    import secrets as _secrets

    from app.core.config import get_settings

    settings = get_settings()
    if users_exist(db):
        # An admin already exists (via /setup or the CLI): remove any
        # leftover setup code.
        try:
            (settings.data_dir / ".setup_token").unlink(missing_ok=True)
        except OSError:
            pass
        return None
    if settings.setup_token:
        return settings.setup_token
    settings.ensure_dirs()
    path = settings.data_dir / ".setup_token"
    if not path.exists():
        path.write_text(_secrets.token_urlsafe(18))
        path.chmod(0o600)
    return path.read_text().strip()


def check_setup_token(db: Session, given: str | None) -> bool:
    import hmac as _hmac

    expected = setup_token(db)
    return bool(expected) and _hmac.compare_digest(
        str(given or "").strip(), expected
    )
