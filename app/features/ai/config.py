"""Per-company AI assistant settings.

Covers provider presets, the API key (stored encrypted) and usage
limits. Stored in ``company.settings["ai"]`` as ``{enabled, preset,
base_url, model, api_key_enc, temperature, max_rounds, daily_limit}``.
Empty values fall back to the ``AI_DEFAULT_*`` environment variables,
then to the preset defaults.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core import crypto
from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import BusinessError
from app.features.org.models import Company

# Common protocol: OpenAI Chat Completions
# (POST {base_url}/chat/completions).
PRESETS: dict[str, dict] = {
    "anthropic": {
        "label": "Anthropic Claude",
        "base_url": "https://api.anthropic.com/v1/",
        "model": "claude-opus-5-5",
        "needs_key": True,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "needs_key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "",
        "needs_key": True,
    },
    "ollama": {
        "label": "Ollama (máy chủ riêng)",
        "base_url": "http://localhost:11434/v1",
        "model": "",
        "needs_key": False,
    },
    "custom": {
        "label": "Tùy chỉnh (tương thích OpenAI)",
        "base_url": "",
        "model": "",
        "needs_key": False,
    },
}
DEFAULT_PRESET = "anthropic"
DEFAULT_MAX_ROUNDS = 8
DEFAULT_DAILY_LIMIT = 200
# None = do not send ``temperature`` (some newer models reject it).
DEFAULT_TEMPERATURE = None


@dataclass
class AiConfig:
    enabled: bool
    preset: str
    base_url: str
    model: str
    api_key: str | None
    temperature: float | None
    max_rounds: int
    daily_limit: int
    has_key: bool = False
    # Using the platform's default API key.
    platform_key: bool = False
    # Let the AI read payroll data (sends personal data to the AI
    # provider).
    allow_payroll_tools: bool = False
    # A key is stored but cannot be decrypted (SECRET_KEY has changed).
    key_error: bool = False

    @property
    def label(self) -> str:
        p = PRESETS.get(self.preset, {}).get("label", self.preset)
        return f"{p} · {self.model}" if self.model else p

    @property
    def ready(self) -> bool:
        needs_key = PRESETS.get(self.preset, {}).get("needs_key", False)
        return bool(
            self.enabled
            and self.base_url
            and self.model
            and not self.key_error
            and (self.api_key or not needs_key)
        )


def _num(value, default, cast, lo, hi):
    try:
        v = cast(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _temperature(value) -> float | None:
    """Return the temperature, or None (blank) to leave it out."""
    if value is None or str(value).strip() == "":
        return None
    return _num(value, DEFAULT_TEMPERATURE, float, 0.0, 2.0)


def raw_settings(company: Company) -> dict:
    return dict((company.settings or {}).get("ai") or {})


def _same_url(a: str, b: str) -> bool:
    return (
        bool(a)
        and bool(b)
        and a.strip().rstrip("/").lower() == b.strip().rstrip("/").lower()
    )


def get_config(company: Company) -> AiConfig:
    """Return the effective settings.

    The platform's default API key (AI_DEFAULT_API_KEY) is ONLY sent to
    AI_DEFAULT_BASE_URL, with the model and limits set by the platform –
    a company cannot point that key at another server.
    """
    s = get_settings()
    raw = raw_settings(company)
    preset = (
        raw.get("preset") if raw.get("preset") in PRESETS else DEFAULT_PRESET
    )
    p = PRESETS[preset]
    base_url = (
        raw.get("base_url") or s.ai_default_base_url or p["base_url"]
    ).strip()
    model = (raw.get("model") or s.ai_default_model or p["model"]).strip()
    max_rounds = _num(
        raw.get("max_rounds", DEFAULT_MAX_ROUNDS),
        DEFAULT_MAX_ROUNDS,
        int,
        1,
        20,
    )
    daily_limit = _num(
        raw.get("daily_limit", DEFAULT_DAILY_LIMIT),
        DEFAULT_DAILY_LIMIT,
        int,
        0,
        100000,
    )
    key_error = False
    platform_key = False
    key = None
    if raw.get("api_key_enc"):
        key = crypto.decrypt(raw.get("api_key_enc"))
        key_error = key is None
    elif s.ai_default_api_key and _same_url(base_url, s.ai_default_base_url):
        key = s.ai_default_api_key
        platform_key = True
        model = s.ai_default_model or model
        limit = max(1, s.ai_default_daily_limit)
        daily_limit = min(daily_limit, limit) if daily_limit else limit
        max_rounds = min(max_rounds, DEFAULT_MAX_ROUNDS)
    return AiConfig(
        enabled=bool(raw.get("enabled")),
        preset=preset,
        base_url=base_url,
        model=model,
        api_key=key,
        temperature=_temperature(raw.get("temperature")),
        max_rounds=max_rounds,
        daily_limit=daily_limit,
        has_key=bool(raw.get("api_key_enc")),
        platform_key=platform_key,
        key_error=key_error,
        allow_payroll_tools=bool(raw.get("allow_payroll_tools")),
    )


# ------------------------------------------------ address safety (SSRF)


def private_urls_allowed() -> bool:
    """Return whether private (internal) server addresses are allowed.

    Self-hosted installs (sign-up closed) may call internal servers
    (Ollama...). SaaS installs block them unless the environment
    variable is set.
    """
    if os.environ.get("AI_ALLOW_PRIVATE_URLS") == "1":
        return True
    return not get_settings().allow_signup


def check_base_url(url: str) -> str | None:
    """Return an error message if the URL is not allowed, else None."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Địa chỉ API phải bắt đầu bằng http:// hoặc https://"
    if private_urls_allowed():
        return None
    from app.features.ai import client

    if client.test_transport_active():
        return None
    if parsed.scheme != "https":
        return "Chỉ chấp nhận địa chỉ https://"
    try:
        infos = socket.getaddrinfo(
            parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror:
        return "Không phân giải được tên miền của địa chỉ API"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        # Only accept public Internet addresses (blocks private,
        # loopback, link-local, CGNAT 100.64/10, metadata...).
        if not ip.is_global or ip.is_multicast:
            return "Không được dùng địa chỉ mạng nội bộ cho trợ lý AI"
    return None


# --------------------------------------------------------------- saving


def _public(raw: dict) -> dict:
    out = {k: v for k, v in raw.items() if k != "api_key_enc"}
    out["has_key"] = bool(raw.get("api_key_enc"))
    return out


def save_config(db: Session, ctx, data: dict) -> AiConfig:
    """Validate and save the AI settings.

    ``data`` keys: enabled, preset, base_url, model, api_key (empty =
    keep the current key), clear_key, temperature (blank = not sent),
    max_rounds, daily_limit.
    """
    company = db.get(Company, ctx.company_id)
    before = raw_settings(company)
    preset = (
        data.get("preset") if data.get("preset") in PRESETS else DEFAULT_PRESET
    )
    base_url = (data.get("base_url") or "").strip()
    model = (data.get("model") or "").strip()
    enabled = bool(data.get("enabled"))
    raw = {
        "enabled": enabled,
        "preset": preset,
        "base_url": base_url,
        "model": model[:200],
        "temperature": _temperature(data.get("temperature")),
        "max_rounds": _num(
            data.get("max_rounds"), DEFAULT_MAX_ROUNDS, int, 1, 20
        ),
        "daily_limit": _num(
            data.get("daily_limit"), DEFAULT_DAILY_LIMIT, int, 0, 100000
        ),
        "allow_payroll_tools": bool(data.get("allow_payroll_tools")),
        "api_key_enc": before.get("api_key_enc"),
    }
    new_key = (data.get("api_key") or "").strip()
    if data.get("clear_key"):
        raw["api_key_enc"] = None
    elif new_key:
        raw["api_key_enc"] = crypto.encrypt(new_key)
    effective_url = (
        base_url
        or get_settings().ai_default_base_url
        or PRESETS[preset]["base_url"]
    )
    if enabled:
        err = check_base_url(effective_url)
        if err:
            raise BusinessError(err)
        if not (
            model
            or get_settings().ai_default_model
            or PRESETS[preset]["model"]
        ):
            raise BusinessError("Nhập tên model")
        if (
            PRESETS[preset]["needs_key"]
            and not raw["api_key_enc"]
            and not get_settings().ai_default_api_key
        ):
            raise BusinessError("Nhập API key của nhà cung cấp")
    settings = dict(company.settings or {})
    settings["ai"] = raw
    company.settings = settings
    write_audit(
        db,
        ctx,
        action="ai.settings",
        entity_type="company",
        entity_id=company.id,
        summary="Cập nhật cấu hình trợ lý AI"
        + (" (đổi API key)" if new_key or data.get("clear_key") else ""),
        before=_public(before),
        after=_public(raw),
    )
    db.commit()
    return get_config(company)
