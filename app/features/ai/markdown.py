"""Minimal Markdown to safe HTML for the assistant's answers.

All HTML is escaped first, then only these tags are produced: p, br,
h3, h4, ul, ol, li, strong, em, code, pre, table, thead, tbody, tr, th,
td, a (only internal "/..." or http(s) links, with rel="noopener").
"""

from __future__ import annotations

import html
import re

_LINK = re.compile(r"\[([^\]\n]{1,200})\]\(([^)\s]{1,500})\)")
_CODE = re.compile(r"`([^`\n]{1,500})`")
_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])")
_OL = re.compile(r"^\s*(\d{1,3})[.)]\s+(.*)$")
_UL = re.compile(r"^\s*[-*•]\s+(.*)$")
_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _safe_href(url: str) -> str | None:
    u = html.unescape(url).strip()
    if any(c in u for c in "\\\t\r\n") or any(ord(c) < 32 for c in u):
        return None  # browsers read "/\\evil.com" as //evil.com
    if u.startswith("/") and not u.startswith("//"):
        return u
    if u.lower().startswith(("https://", "http://")):
        return u
    return None


def _inline(text: str) -> str:
    """Render inline Markdown; ``text`` is already HTML-escaped."""
    codes: list[str] = []

    def keep_code(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    out = _CODE.sub(keep_code, text)

    def link(m):
        href = _safe_href(m.group(2))
        if href is None:
            return m.group(1)
        ext = (
            ""
            if href.startswith("/")
            else ' target="_blank" rel="noopener noreferrer"'
        )
        return (
            f'<a href="{html.escape(href, quote=True)}"{ext}>{m.group(1)}</a>'
        )

    out = _LINK.sub(link, out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _ITALIC.sub(r"<em>\1</em>", out)
    out = re.sub(
        r"\x00(\d+)\x00",
        lambda m: f"<code>{codes[int(m.group(1))]}</code>",
        out,
    )
    return out


def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def render(md: str | None) -> str:
    if not md:
        return ""
    # Strip control characters (\x00 is used as a temporary marker
    # inside the converter – input containing it breaks rendering).
    md = _CONTROL.sub("", md.replace("\r\n", "\n").replace("\r", "\n"))
    if not md:
        return ""
    lines = html.escape(md).split("\n")
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            flush_para()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            i += 1
            continue
        if not line.strip():
            flush_para()
            i += 1
            continue
        m = _HEAD.match(line)
        if m:
            flush_para()
            level = 3 if len(m.group(1)) <= 2 else 4
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        if (
            "|" in line
            and i + 1 < len(lines)
            and _TABLE_SEP.match(lines[i + 1])
        ):
            flush_para()
            head = _cells(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            num = re.compile(r"^[-+]?[\d.,]+(\s?(đ|%|VND))?$")
            numeric = [
                bool(rows)
                and all(num.match(r[k]) for r in rows if k < len(r) and r[k])
                and any(k < len(r) and r[k] for r in rows)
                for k in range(len(head))
            ]

            def cell(tag, k, c):
                cls = ' class="num"' if k < len(numeric) and numeric[k] else ""
                return f"<{tag}{cls}>{_inline(c)}</{tag}>"

            parts = [
                "<table><thead><tr>"
                + "".join(cell("th", k, c) for k, c in enumerate(head))
                + "</tr></thead><tbody>"
            ]
            for r in rows:
                parts.append(
                    "<tr>"
                    + "".join(cell("td", k, c) for k, c in enumerate(r))
                    + "</tr>"
                )
            parts.append("</tbody></table>")
            out.append("".join(parts))
            continue
        if _UL.match(line) or _OL.match(line):
            flush_para()
            ordered = bool(_OL.match(line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines) and (
                _OL.match(lines[i]) if ordered else _UL.match(lines[i])
            ):
                mm = _OL.match(lines[i]) if ordered else _UL.match(lines[i])
                item = mm.group(2) if ordered else mm.group(1)
                items.append(f"<li>{_inline(item)}</li>")
                i += 1
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        para.append(line.strip())
        i += 1
    flush_para()
    return "".join(out)
