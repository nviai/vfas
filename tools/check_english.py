"""Check that developer-facing text is written in English.

Flags Vietnamese words (letters with Vietnamese diacritics) in:

- Python comments, docstrings and logging messages;
- Jinja ``{# ... #}`` and HTML ``<!-- ... -->`` comments;
- JavaScript and CSS comments.

User-facing text (template content, ``_()`` messages, errors shown to
users) stays Vietnamese and is not checked. Text inside quotes or
backticks is ignored, so a comment may still quote a UI string or a
legal name, e.g. ``# label shown as "Phiếu thu"``.

Usage::

    python tools/check_english.py            # scan app, tests, tools,
                                             # migrations
    python tools/check_english.py PATH ...   # scan given files/dirs
"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = ("app", "tests", "tools", "migrations")
SKIP_PARTS = {"vendor", "__pycache__", "locales"}
SKIP_FILES = {"app/static/css/app.css"}

VIETNAMESE = re.compile(
    "[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]",
    re.IGNORECASE,
)
QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`|\'[^\'\n]*\'')
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
LOGGERS = {"log", "logger", "logging", "_log", "_logger"}

HTML_COMMENT = re.compile(r"\{#.*?#\}|<!--.*?-->", re.DOTALL)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"(?:^|[\s;{}()])//[^\n]*")


def has_vietnamese(text: str) -> bool:
    return bool(VIETNAMESE.search(QUOTED.sub("", text)))


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def check_python(path: Path, text: str) -> list[tuple[int, str]]:
    issues: list[tuple[int, str]] = []
    tokens = tokenize.generate_tokens(io.StringIO(text).readline)
    for tok in tokens:
        if tok.type == tokenize.COMMENT and has_vietnamese(tok.string):
            issues.append((tok.start[0], "comment"))
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc and has_vietnamese(doc):
                line = node.body[0].lineno if node.body else 1
                issues.append((line, "docstring"))
        elif isinstance(node, ast.Call) and _is_log_call(node):
            for arg in node.args[:1]:
                for part in _string_parts(arg):
                    if has_vietnamese(part):
                        issues.append((node.lineno, "log message"))
                        break
    return issues


def _is_log_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr in LOG_METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id in LOGGERS
    )


def _string_parts(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            v.value
            for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
    return []


def check_regex(text: str, *patterns: re.Pattern) -> list[tuple[int, str]]:
    issues = []
    for pattern in patterns:
        for m in pattern.finditer(text):
            if has_vietnamese(m.group(0)):
                issues.append((_line_of(text, m.start()), "comment"))
    return issues


def check_file(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return check_python(path, text)
    if path.suffix == ".html":
        return check_regex(text, HTML_COMMENT)
    if path.suffix == ".js":
        return check_regex(text, BLOCK_COMMENT, LINE_COMMENT)
    if path.suffix == ".css":
        return check_regex(text, BLOCK_COMMENT)
    return []


def iter_files(paths: list[str]):
    for raw in paths:
        p = (ROOT / raw) if not Path(raw).is_absolute() else Path(raw)
        files = [p] if p.is_file() else sorted(p.rglob("*"))
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            if (
                f.suffix in {".py", ".html", ".js", ".css"}
                and not SKIP_PARTS.intersection(f.parts)
                and rel not in SKIP_FILES
            ):
                yield f, rel


def main(argv: list[str]) -> int:
    total = 0
    for f, rel in iter_files(argv or list(DEFAULT_PATHS)):
        for line, kind in check_file(f):
            print(f"{rel}:{line}: Vietnamese text in {kind}")
            total += 1
    if total:
        print(
            f"{total} issue(s): write comments, docstrings and log "
            "messages in English (see CONTRIBUTING.md)."
        )
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
