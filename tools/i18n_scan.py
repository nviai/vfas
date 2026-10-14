"""Find translatable strings that lack an English translation.

Scans templates and registry labels.

python tools/i18n_scan.py            # list missing strings per file
python tools/i18n_scan.py --json F   # write {string: ""} for missing
                                     # strings to file F
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
STR = r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\''
CALL = re.compile(r"(?<![\w.])(?:_|tr|ui\.tr)\(\s*(?:" + STR + r")")


def template_strings() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in APP.rglob("*.html"):
        rel = f.relative_to(APP).as_posix()
        if (
            "/print/" in rel
            or rel.startswith("core/templates/ui_kit/")
            or "/ui_kit/" in rel
        ):
            continue
        text = f.read_text(encoding="utf-8")
        for m in CALL.finditer(text):
            s = m.group(1) if m.group(1) is not None else m.group(2)
            s = (
                s.encode()
                .decode("unicode_escape")
                .encode("latin-1")
                .decode("utf-8")
                if "\\" in s
                else s
            )
            if s and not s.startswith("{{"):
                out.setdefault(s, set()).add(rel)
    return out


def registry_strings() -> dict[str, set[str]]:
    sys.path.insert(0, str(ROOT))
    from app.core.registry import (
        ROLE_LABELS,
        SECTIONS,
        load_features,
        registry,
    )

    load_features()
    out: dict[str, set[str]] = {}

    def add(s, src):
        if s:
            out.setdefault(s, set()).add(src)

    for _k, label, _i in SECTIONS:
        add(label, "registry:sections")
    for n in registry.nav:
        add(n.label, "registry:nav")
    for a in registry.actions:
        add(a.label, "registry:actions")
        add(a.group, "registry:actions")
    for d in registry.doc_types.values():
        add(d.label, "registry:doc_types")
    for label in ROLE_LABELS.values():
        add(label, "registry:roles")
    for c in registry.checks:
        add(c.title, "registry:checks")
    from app.features.journal.models import STATUS_LABELS

    for label in STATUS_LABELS.values():
        add(label, "journal:status")
    return out


def catalog() -> dict[str, str]:
    sys.path.insert(0, str(ROOT))
    from app.core.i18n import load_catalog

    load_catalog.cache_clear()
    return load_catalog("en")


def missing() -> dict[str, set[str]]:
    cat = catalog()
    found = template_strings()
    for k, v in registry_strings().items():
        found.setdefault(k, set()).update(v)
    return {s: src for s, src in found.items() if s not in cat}


if __name__ == "__main__":
    miss = missing()
    if len(sys.argv) > 2 and sys.argv[1] == "--json":
        Path(sys.argv[2]).write_text(
            json.dumps(
                {k: "" for k in sorted(miss)}, ensure_ascii=False, indent=1
            )
        )
    by_file: dict[str, list[str]] = {}
    for s, srcs in miss.items():
        for src in srcs:
            by_file.setdefault(src, []).append(s)
    for f in sorted(by_file):
        print(f"{f}: {len(by_file[f])}")
    print("TOTAL", len(miss))
