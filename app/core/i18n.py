"""Simple i18n: the Vietnamese source string is the key.

English translations live in JSON files. Printed ledgers and vouchers
are always in Vietnamese, so print templates do not use the translate
function.

Translation files: app/core/locales/en.json, then
app/features/<feature>/locales/en.json (the feature wins on duplicate
keys because its translation is closer to the context). Check for
missing translations with: python tools/i18n_scan.py
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache

from app.core.config import APP_DIR

LANGUAGES = {"vi": "Tiếng Việt", "en": "English"}
DEFAULT_LANGUAGE = "vi"


@lru_cache
def load_catalog(lang: str) -> dict[str, str]:
    if lang == "vi":
        return {}
    catalog: dict[str, str] = {}
    files = [APP_DIR / "core" / "locales" / f"{lang}.json"]
    files += sorted((APP_DIR / "features").glob(f"*/locales/{lang}.json"))
    for path in files:
        if path.exists():
            catalog.update(json.loads(path.read_text(encoding="utf-8")))
    return catalog


def get_translator(lang: str) -> Callable[..., str]:
    catalog = load_catalog(lang if lang in LANGUAGES else DEFAULT_LANGUAGE)

    def _(text: str, **kwargs) -> str:
        out = catalog.get(text, text)
        return out.format(**kwargs) if kwargs else out

    return _
