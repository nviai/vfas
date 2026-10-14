"""Static UI checks.

Every icon used exists in the icon set and all templates parse.
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
PATTERNS = [
    r'icon\(\s*"([a-z0-9-]+)"',
    r"icon\(\s*'([a-z0-9-]+)'",
    r'icon_name\s*=\s*"([a-z0-9-]+)"',
    r'icon_right\s*=\s*"([a-z0-9-]+)"',
    r'"icon"\s*:\s*"([a-z0-9-]+)"',
    r'\bicon\s*=\s*"([a-z0-9-]+)"',
    r'icon_button\(\s*"([a-z0-9-]+)"',
]


def test_all_icons_exist():
    text = (APP / "core" / "templates" / "icons.html").read_text()
    have = set(re.findall(r'^\s+"([a-z0-9-]+)":', text, re.M))
    missing = {}
    for f in list(APP.rglob("*.html")) + list(APP.rglob("*.py")):
        t = f.read_text(errors="ignore")
        for p in PATTERNS:
            for m in re.finditer(p, t):
                if m.group(1) not in have:
                    missing.setdefault(m.group(1), set()).add(
                        str(f.relative_to(APP))
                    )
    assert not missing, missing


def test_templates_parse(app):
    from app.core.web import templates

    env = templates.env
    bad = {}
    for name in env.list_templates(extensions=["html"]):
        try:
            env.get_template(name)
        except Exception as e:  # noqa: BLE001
            bad[name] = repr(e)
    assert not bad, bad
