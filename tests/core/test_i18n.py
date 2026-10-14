"""Every UI string has an English translation.

Covers templates, menus, roles, voucher types and checks.
"""

import importlib.util
from pathlib import Path


def _scanner():
    path = Path(__file__).resolve().parents[2] / "tools" / "i18n_scan.py"
    spec = importlib.util.spec_from_file_location("i18n_scan", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_missing_translations(engine):
    missing = _scanner().missing()
    assert not missing, sorted(missing)[:40]


def test_english_pages_render(web, users):
    from tests.conftest import ROLE_EMAILS

    web.login(ROLE_EMAILS["owner"])
    web.get("/lang/en")
    r = web.get("/")
    assert r.status_code == 200 and 'lang="en"' in r.text
    assert "Chart of accounts" in web.get("/accounts").text
