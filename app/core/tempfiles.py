"""Clean up temporary files/directories left by import sessions.

Covers cash book Excel files, bank statements, XML invoices and opening
balances. An import session stores its files in `settings.imports_dir`
for preview before confirmation; if abandoned sessions are never
removed, repeated uploads fill the disk (especially when many companies
share one server).
"""

from __future__ import annotations

import time
from pathlib import Path

from app.core.config import get_settings

MAX_AGE_HOURS = 24
_last_run = 0.0


def cleanup_imports(
    max_age_hours: float = MAX_AGE_HOURS, *, base: Path | None = None
) -> int:
    """Remove files and directories older than max_age_hours.

    Walks the temporary import directory recursively. Returns the
    number of removed entries.
    """
    base = base or get_settings().imports_dir
    if not base.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    # Children first, then parents
    for p in sorted(base.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        try:
            if p.is_symlink() or p.is_file():
                if p.lstat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            elif (
                p.is_dir()
                and p.stat().st_mtime < cutoff
                and not any(p.iterdir())
            ):
                p.rmdir()
                removed += 1
        except OSError:
            pass
    return removed


def cleanup_imports_throttled(min_interval_seconds: float = 600) -> None:
    """Call before creating each new import session.

    Actually runs at most once every 10 minutes.
    """
    global _last_run
    now = time.time()
    if now - _last_run < min_interval_seconds:
        return
    _last_run = now
    cleanup_imports()
