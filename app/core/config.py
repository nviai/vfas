"""Application settings, read from environment variables or .env."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    product_name: str = "VFAS"
    # Empty = SQLite in DATA_DIR.
    # PostgreSQL: postgresql+psycopg://user:pass@host/db
    database_url: str = ""
    data_dir: Path = Path("./data")
    secret_key: str = ""
    # Previous key during SECRET_KEY rotation: stored secrets (AI API
    # keys) can still be decrypted with it.
    secret_key_old: str = ""
    session_cookie: str = "vfas_session"
    session_hours: int = 12
    # Log out automatically after this many idle minutes (0 = off).
    session_idle_minutes: int = 120
    # Send cookies over HTTPS only. When False, it is still enabled
    # automatically for requests arriving over https (behind a trusted
    # proxy).
    cookie_secure: bool = False
    # Token required by the /setup page (first-time setup). Empty =
    # generated at startup, written to the log and saved to
    # DATA_DIR/.setup_token.
    setup_token: str = ""
    # SaaS: max sign-ups per IP per hour; max companies a user may
    # create themselves (0 = unlimited).
    signup_per_ip_hour: int = 5
    max_companies_per_user: int = 0
    timezone: str = "Asia/Ho_Chi_Minh"
    max_upload_mb: int = 10
    # SaaS: allow self sign-up of an account + new company. Keep False
    # for a single-company self-hosted install.
    allow_signup: bool = False
    # Public URL (used in member invitation links). Empty = taken from
    # the request.
    public_url: str = ""
    # Outgoing email (SMTP). Empty SMTP_HOST = email disabled:
    # invitation links are shown on screen only and password reset
    # is unavailable.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # starttls (port 587) | ssl (port 465) | none (local relay only)
    smtp_security: str = "starttls"
    smtp_timeout: int = 15
    # Sender, e.g. "VFAS <no-reply@example.com>". Empty = SMTP_USERNAME.
    mail_from: str = ""
    # auto: required when ALLOW_SIGNUP=true and SMTP is configured;
    # required | off.
    email_verification: str = "auto"
    # Offer the simulated e-invoice provider (no legal value). For
    # demos and tests only – never on a real installation.
    einvoice_simulator: bool = False
    # Show the /ui-kit page to every logged-in user (default:
    # superadmin only).
    debug_ui: bool = False
    # Optional AI assistant defaults for companies without their own
    # configuration.
    ai_default_base_url: str = ""
    ai_default_api_key: str = ""
    ai_default_model: str = ""
    # Daily question limit per user when using the platform default API
    # key (companies cannot raise it).
    ai_default_daily_limit: int = 100

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'ketoan.db').resolve()}"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def imports_dir(self) -> Path:
        return self.data_dir / "imports"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.attachments_dir, self.imports_dir):
            d.mkdir(parents=True, exist_ok=True)

    def resolved_secret(self) -> str:
        """Return SECRET_KEY from the environment.

        If unset, generate one once and store it in DATA_DIR (mode 600,
        created atomically).
        """
        if self.secret_key:
            return self.secret_key
        self.ensure_dirs()
        path = self.data_dir / ".secret_key"
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(secrets.token_urlsafe(48))
            except FileExistsError:
                pass  # another process just created it
        return path.read_text().strip()


# Sample values from the docs: using one verbatim means a public key.
SAMPLE_SECRETS = {
    "doi-chuoi-nay",
    "change-me",
    "changeme",
    "secret",
    "your-secret-key",
}


def check_secret(settings: Settings) -> str | None:
    """Return an error message if SECRET_KEY is unsafe (None = OK)."""
    key = settings.secret_key
    if not key:
        return None  # a random key is generated in DATA_DIR
    if key.strip().lower() in SAMPLE_SECRETS:
        return (
            "SECRET_KEY đang là giá trị mẫu – hãy sinh khóa mới: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if len(key) < 32 and settings.allow_signup:
        return (
            "SECRET_KEY phải dài ít nhất 32 ký tự khi chạy SaaS "
            "(ALLOW_SIGNUP=true)"
        )
    return None


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    global _settings
    _settings = settings
