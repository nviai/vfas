"""Registry of e-invoice provider adapters."""

from __future__ import annotations

import importlib

from app.core.config import get_settings
from app.features.einvoice.providers.base import EInvoiceProvider

# code -> "module:Class"
_ADAPTERS = {
    "viettel": "viettel:ViettelProvider",
    "misa": "misa:MisaProvider",
    "vnpt": "vnpt:VnptProvider",
    "bkav": "bkav:BkavProvider",
    "simulator": "simulator:SimulatorProvider",
}


def provider_class(code: str) -> type[EInvoiceProvider] | None:
    target = _ADAPTERS.get(code or "")
    if target is None:
        return None
    if code == "simulator" and not get_settings().einvoice_simulator:
        return None
    module, cls = target.split(":")
    mod = importlib.import_module(f"app.features.einvoice.providers.{module}")
    return getattr(mod, cls)


def available() -> list[type[EInvoiceProvider]]:
    out = []
    for code in _ADAPTERS:
        cls = provider_class(code)
        if cls is not None:
            out.append(cls)
    return out
