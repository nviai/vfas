#!/usr/bin/env python3
"""Render the /ui-kit pages to static HTML.

The output can be viewed or screenshotted with headless Chrome without
running the app (no database needed).

    python tools/render_kit.py OUT_DIR
    python -m http.server 8765 --directory OUT_DIR

OUT_DIR layout:
    static -> app/static              (symlink)
    ui-kit/<page>/index.html          light theme
    dark/ui-kit/<page>/index.html     dark theme
    ui-kit/lookup/<kind>/index.html   fake JSON for comboboxes
    ui-kit/search/index.html, ui-kit/ai-panel/index.html
    m/<page>.html, m/dark-<page>.html
                                      390x844 iframe simulating a phone
                                      (headless Chrome has a minimum
                                      window width)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.features.ui_kit import fake  # noqa: E402

FRAME = (
    '<!doctype html><html><head><meta charset="utf-8">'
    "<title>{title}</title>\n"
    "<style>body{{margin:0;background:#d9dde3;display:flex;"
    "justify-content:center;padding:16px}}\n"
    "iframe{{width:390px;height:844px;border:0;border-radius:24px;"
    "box-shadow:0 10px 40px rgba(0,0,0,.25);background:#fff}}</style>\n"
    '</head><body><iframe src="{src}"></iframe></body></html>'
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    link = out / "static"
    if not link.exists():
        link.symlink_to(ROOT / "app" / "static")
    env = fake.build_env()
    pages = ["index"] + [p[0] for p in fake.PAGES] + ["ai-panel"]
    failed = []
    for theme, base in (("light", out), ("dark", out / "dark")):
        for page in pages:
            try:
                html = fake.render(page, theme=theme, env=env)
            except Exception as exc:  # noqa: BLE001
                failed.append((page, theme, exc))
                continue
            target = (
                base
                / "ui-kit"
                / ("" if page == "index" else page)
                / "index.html"
            )
            write(target, html)
    for kind, items in fake.LOOKUPS.items():
        write(
            out / "ui-kit" / "lookup" / kind / "index.html",
            json.dumps(items, ensure_ascii=False),
        )
    write(
        out / "ui-kit" / "search" / "index.html",
        json.dumps(fake.SEARCH_RESULTS, ensure_ascii=False),
    )
    for page in pages:
        slug = "" if page == "index" else page + "/"
        write(
            out / "m" / f"{page}.html",
            FRAME.format(title=page, src=f"/ui-kit/{slug}"),
        )
        write(
            out / "m" / f"dark-{page}.html",
            FRAME.format(title=page, src=f"/dark/ui-kit/{slug}"),
        )
    for page, theme, exc in failed:
        print(
            f"LỖI {page} ({theme}): {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
    print(f"Đã render {len(pages) * 2 - len(failed)} trang vào {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
