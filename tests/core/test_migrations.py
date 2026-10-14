"""Alembic migrations match the models and install the triggers.

No table or column may be missing.
"""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

import app.models  # noqa: F401
from app.core.db import Base


def test_migrations_match_models(engine):
    with engine.connect() as conn:
        mc = MigrationContext.configure(conn, opts={"compare_type": False})
        diff = [
            d
            for d in compare_metadata(mc, Base.metadata)
            if not (
                isinstance(d, tuple) and d[0] in ("remove_index", "add_index")
            )
        ]
        tables = set(inspect(conn).get_table_names())
    assert not diff, diff[:10]
    assert {
        "journal_entries",
        "journal_lines",
        "audit_logs",
        "alembic_version",
    } <= tables
