"""Encrypt secrets stored in the DB (AI provider API keys, ...).

The key is derived from SECRET_KEY.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


def _key(secret: str) -> Fernet:
    # The "nvk-secrets|" prefix is kept from the old name: changing it
    # would make stored AI API keys undecryptable.
    digest = hashlib.sha256(("nvk-secrets|" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _fernet() -> MultiFernet:
    """Encrypt with the current key.

    Decryption also tries the old key (SECRET_KEY_OLD) during key
    rotation.
    """
    settings = get_settings()
    keys = [_key(settings.resolved_secret())]
    if settings.secret_key_old:
        keys.append(_key(settings.secret_key_old))
    return MultiFernet(keys)


def encrypt(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def mask(value: str | None) -> str:
    """Display a secret as sk-…abcd."""
    if not value:
        return ""
    return (value[:3] + "…" + value[-4:]) if len(value) > 10 else "…"
