"""Event hooks linking journal entries to bank statement lines.

A voucher created from a statement line is matched automatically
when it is posted; cancelling a matched voucher releases the
statement line.
"""

from __future__ import annotations

from app.core.registry import on
from app.features.bank import statements


@on("entry.posted")
def _match_statement(db, ctx, entry) -> None:
    if entry.doc_type in ("BC", "BN", "CK"):
        statements.match_posted_entry(db, ctx, entry)


@on("entry.cancelled")
def _release_statement(db, ctx, entry) -> None:
    statements.release_cancelled_entry(db, ctx, entry)
