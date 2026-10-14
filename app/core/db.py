"""SQLAlchemy engine, session and declarative Base."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import (
    BigInteger,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    create_engine,
    event,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    declared_attr,
    mapped_column,
    sessionmaker,
)
from sqlalchemy.sql.functions import GenericFunction

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Money is always an integer number of dong (BIGINT). No floats.
Money = BigInteger
# Quantity and unit price: Decimal (amounts are always rounded to
# whole dong).
Qty = Numeric(18, 3)
Price = Numeric(18, 2)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TenantMixin:
    """A business table that belongs to one company.

    See app/core/tenancy.py for the automatic tenant filter.
    """

    @declared_attr
    def company_id(cls) -> Mapped[int]:
        return mapped_column(
            Integer,
            ForeignKey("companies.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )


class ulower(GenericFunction):  # noqa: N801 - used like a SQL function
    """Vietnamese-aware lower() (`Đ` → `đ`, `Ư` → `ư`, ...).

    SQLite's lower() only handles ASCII letters.
    """

    type = String()
    name = "ulower"
    inherit_cache = True


@compiles(ulower)
def _ulower_default(element, compiler, **kw):
    return f"lower({compiler.process(element.clauses, **kw)})"


@compiles(ulower, "sqlite")
def _ulower_sqlite(element, compiler, **kw):
    return f"py_lower({compiler.process(element.clauses, **kw)})"


def _py_lower(value):
    return value.lower() if isinstance(value, str) else value


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Engine | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()
    dbapi_conn.create_function("py_lower", 1, _py_lower, deterministic=True)


def configure_engine(url: str) -> Engine:
    global _engine
    # Load every model so string-based relationships always resolve.
    import app.models  # noqa: F401

    if _engine is not None:
        _engine.dispose()
    kwargs: dict = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        kwargs.update(pool_size=5, max_overflow=5, pool_pre_ping=True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragmas)
    SessionLocal.configure(bind=engine)
    _engine = engine
    return engine


def get_engine() -> Engine:
    if _engine is None:
        from app.core.config import get_settings

        return configure_engine(get_settings().db_url)
    return _engine


def is_sqlite(session_or_engine) -> bool:
    bind = (
        session_or_engine.get_bind()
        if isinstance(session_or_engine, Session)
        else session_or_engine
    )
    return bind.dialect.name == "sqlite"


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    get_engine()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# --------------------------------------------------------- LIKE search

LIKE_ESCAPE = "\\"


def like_escape(term: str) -> str:
    """Escape LIKE special characters in user input.

    Covers %, _ and the escape character itself.
    """
    return (
        (term or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def contains_pattern(term: str) -> str:
    """Build a "contains" LIKE pattern.

    Use with .like(pattern, escape=LIKE_ESCAPE).
    """
    return f"%{like_escape(term)}%"


def like_contains(column, term: str):
    """column LIKE '%term%'.

    % and _ typed by the user are literal characters, not wildcards.
    """
    return column.like(contains_pattern(term), escape=LIKE_ESCAPE)


def like_prefix(column, term: str):
    """column LIKE 'term%' (term is escaped)."""
    return column.like(f"{like_escape(term)}%", escape=LIKE_ESCAPE)
