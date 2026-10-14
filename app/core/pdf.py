"""Export PDFs from print HTML templates with WeasyPrint.

Renders in two passes: the first counts the pages, the second fills
the page count into the ledger body
("Sổ này có … trang, đánh số từ trang 01 đến trang …").
"""

from __future__ import annotations

from app.core.config import APP_DIR

_STATIC = (APP_DIR / "static").resolve()
_fetcher = None


def _safe_fetcher():
    """Let WeasyPrint load only `data:` URLs and files in app/static.

    No http/https and no files outside that directory.
    """
    global _fetcher
    if _fetcher is not None:
        return _fetcher
    from pathlib import Path
    from urllib.parse import unquote, urlparse

    from weasyprint.urls import URLFetcher

    class SafeFetcher(URLFetcher):
        def fetch(self, url, headers=None):
            scheme = url.split(":", 1)[0].lower()
            if scheme == "file":
                path = Path(unquote(urlparse(url).path)).resolve()
                if not path.is_relative_to(_STATIC):
                    raise ValueError(
                        f"Loading files outside static is not allowed: "
                        f"{url[:80]}"
                    )
            elif scheme != "data":
                raise ValueError(
                    f"Loading external resources in PDFs is not allowed: "
                    f"{url[:80]}"
                )
            return super().fetch(url, headers)

    _fetcher = SafeFetcher(
        allowed_protocols=("data", "file"), allow_redirects=False, timeout=5
    )
    return _fetcher


def _html(source: str):
    from weasyprint import HTML

    return HTML(
        string=source, base_url=str(_STATIC), url_fetcher=_safe_fetcher()
    )


def render_pdf(template_name: str, context: dict) -> bytes:
    from app.core.web import templates

    template = templates.env.get_template(template_name)
    ctx = dict(context)
    ctx["total_pages"] = None
    first_html = template.render(**ctx)
    doc = _html(first_html).render()
    pages = len(doc.pages)
    # Fill in the real page count; if the layout changes the page
    # count (rare), repeat once more.
    for _ in range(2):
        ctx["total_pages"] = pages
        html = template.render(**ctx)
        if html == first_html:
            break
        doc = _html(html).render()
        if len(doc.pages) == pages:
            break
        pages = len(doc.pages)
    return doc.write_pdf()
