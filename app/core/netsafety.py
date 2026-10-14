"""Outbound URL checks (SSRF protection) for addresses set by tenants.

On a SaaS install (ALLOW_SIGNUP=true) a company must not make the
server call internal addresses: only https URLs whose host resolves to
public Internet addresses are accepted. Self-hosted installs may call
servers on their own network.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import get_settings


def internal_allowed() -> bool:
    return not get_settings().allow_signup


def public_https_error(url: str) -> str | None:
    """Return why `url` may not be called (None = allowed)."""
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or not parsed.hostname:
        return "Địa chỉ phải bắt đầu bằng https://"
    if internal_allowed():
        return None
    try:
        infos = socket.getaddrinfo(
            parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP
        )
    except socket.gaierror:
        return "Không phân giải được tên miền của địa chỉ"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if not ip.is_global or ip.is_multicast:
            return "Không được dùng địa chỉ mạng nội bộ"
    return None
