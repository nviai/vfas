"""Create or upgrade the schema.

Uses Alembic when migrations exist, otherwise create_all + triggers
(development stage).
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, pool

from app.core.config import APP_DIR, get_settings


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(APP_DIR.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_DIR.parent / "migrations"))
    cfg.attributes["configure_logger"] = False
    if url:
        cfg.attributes["url"] = url
    return cfg


def has_migrations() -> bool:
    return any(
        p.suffix == ".py"
        for p in (APP_DIR.parent / "migrations" / "versions").glob("*.py")
    )


def create_schema(url: str) -> None:
    """Create all tables from the models, plus triggers.

    Used by tests and during development before a baseline migration
    exists.
    """
    import app.models  # noqa: F401
    from app.core.db import Base
    from app.core.triggers import install_triggers

    get_settings().ensure_dirs()
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        install_triggers(conn)
    engine.dispose()


def upgrade_db(url: str | None = None) -> None:
    url = url or get_settings().db_url
    if has_migrations():
        command.upgrade(alembic_config(url), "head")
    else:
        create_schema(url)
