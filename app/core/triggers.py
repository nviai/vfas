"""DB triggers that protect accounting data.

Shared by the Alembic migrations and the tests.

- audit_logs: INSERT only.
- journal_entries: posted/cancelled entries cannot be deleted and their
  financial fields cannot change; the only allowed transition from
  posted is to cancelled; draft → posted requires at least one line.
- journal_lines, vat_records: can change only while the parent entry is
  still a draft.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.features.journal.models import LOCKED_FIELDS

CHILD_TABLES = ("journal_lines", "vat_records")


def _sqlite() -> list[str]:
    locked = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in LOCKED_FIELDS)
    stmts = [
        """CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update
           BEFORE UPDATE ON audit_logs
           BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete
           BEFORE DELETE ON audit_logs
           BEGIN SELECT RAISE(ABORT, 'audit_logs is append-only'); END""",
        """CREATE TRIGGER IF NOT EXISTS trg_je_no_delete
           BEFORE DELETE ON journal_entries
           WHEN OLD.status <> 'draft'
           BEGIN
             SELECT RAISE(ABORT, 'posted/cancelled entry cannot be deleted');
           END""",
        f"""CREATE TRIGGER IF NOT EXISTS trg_je_locked_fields
           BEFORE UPDATE ON journal_entries
           WHEN OLD.status <> 'draft' AND ({locked})
           BEGIN
             SELECT RAISE(ABORT,
               'locked fields of a posted entry cannot change');
           END""",
        """CREATE TRIGGER IF NOT EXISTS trg_je_status_flow
           BEFORE UPDATE OF status ON journal_entries
           WHEN (OLD.status = 'cancelled' AND NEW.status <> 'cancelled')
             OR (OLD.status = 'posted' AND NEW.status = 'draft')
           BEGIN
             SELECT RAISE(ABORT, 'invalid entry status transition');
           END""",
        """CREATE TRIGGER IF NOT EXISTS trg_je_post_has_lines
           BEFORE UPDATE OF status ON journal_entries
           WHEN OLD.status = 'draft' AND NEW.status = 'posted'
             AND (SELECT COUNT(*) FROM journal_lines
                  WHERE entry_id = NEW.id) = 0
           BEGIN
             SELECT RAISE(ABORT, 'entry without lines cannot be posted');
           END""",
    ]
    for t in CHILD_TABLES:
        for op, ref in (
            ("INSERT", "NEW"),
            ("UPDATE", "OLD"),
            ("DELETE", "OLD"),
        ):
            stmts.append(
                f"""CREATE TRIGGER IF NOT EXISTS trg_{t}_{op.lower()}_guard
                    BEFORE {op} ON {t}
                    WHEN (SELECT status FROM journal_entries
                          WHERE id = {ref}.entry_id) <> 'draft'
                    BEGIN
                      SELECT RAISE(ABORT,
                        'lines of a posted entry cannot change');
                    END"""
            )
    return stmts


def _postgres() -> list[str]:
    locked = " OR ".join(
        f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in LOCKED_FIELDS
    )
    stmts = [
        """CREATE OR REPLACE FUNCTION audit_logs_append_only()
           RETURNS trigger AS $$
           BEGIN RAISE EXCEPTION 'audit_logs is append-only'; END;
           $$ LANGUAGE plpgsql""",
        "DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs",
        """CREATE TRIGGER trg_audit_logs_append_only
           BEFORE UPDATE OR DELETE ON audit_logs
           FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()""",
        f"""CREATE OR REPLACE FUNCTION journal_entries_guard()
           RETURNS trigger AS $$
           BEGIN
             IF TG_OP = 'DELETE' THEN
               IF OLD.status <> 'draft' THEN
                 RAISE EXCEPTION 'posted/cancelled entry cannot be deleted';
               END IF;
               RETURN OLD;
             END IF;
             IF OLD.status <> 'draft' AND ({locked}) THEN
               RAISE EXCEPTION
                 'locked fields of a posted entry cannot change';
             END IF;
             IF (OLD.status = 'cancelled' AND NEW.status <> 'cancelled')
                OR (OLD.status = 'posted' AND NEW.status = 'draft') THEN
               RAISE EXCEPTION 'invalid entry status transition';
             END IF;
             IF OLD.status = 'draft' AND NEW.status = 'posted'
                AND NOT EXISTS (SELECT 1 FROM journal_lines
                                WHERE entry_id = NEW.id) THEN
               RAISE EXCEPTION 'entry without lines cannot be posted';
             END IF;
             RETURN NEW;
           END; $$ LANGUAGE plpgsql""",
        "DROP TRIGGER IF EXISTS trg_journal_entries_guard ON journal_entries",
        """CREATE TRIGGER trg_journal_entries_guard
           BEFORE UPDATE OR DELETE ON journal_entries
           FOR EACH ROW EXECUTE FUNCTION journal_entries_guard()""",
        """CREATE OR REPLACE FUNCTION entry_children_guard()
           RETURNS trigger AS $$
           DECLARE st text;
           BEGIN
             IF TG_OP = 'INSERT' THEN
               SELECT status INTO st FROM journal_entries
                 WHERE id = NEW.entry_id;
             ELSE
               SELECT status INTO st FROM journal_entries
                 WHERE id = OLD.entry_id;
             END IF;
             IF st IS NOT NULL AND st <> 'draft' THEN
               RAISE EXCEPTION 'lines of a posted entry cannot change';
             END IF;
             IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
             RETURN NEW;
           END; $$ LANGUAGE plpgsql""",
    ]
    for t in CHILD_TABLES:
        stmts.append(f"DROP TRIGGER IF EXISTS trg_{t}_guard ON {t}")
        stmts.append(f"""CREATE TRIGGER trg_{t}_guard
                         BEFORE INSERT OR UPDATE OR DELETE ON {t}
                         FOR EACH ROW
                         EXECUTE FUNCTION entry_children_guard()""")
    return stmts


def trigger_statements(dialect: str) -> list[str]:
    if dialect == "sqlite":
        return _sqlite()
    if dialect == "postgresql":
        return _postgres()
    return []


def drop_statements(dialect: str) -> list[str]:
    if dialect == "sqlite":
        names = [
            "trg_audit_logs_no_update",
            "trg_audit_logs_no_delete",
            "trg_je_no_delete",
            "trg_je_locked_fields",
            "trg_je_status_flow",
            "trg_je_post_has_lines",
        ]
        names += [
            f"trg_{t}_{op}_guard"
            for t in CHILD_TABLES
            for op in ("insert", "update", "delete")
        ]
        return [f"DROP TRIGGER IF EXISTS {n}" for n in names]
    if dialect == "postgresql":
        out = [
            f"DROP TRIGGER IF EXISTS trg_{t}_guard ON {t}"
            for t in CHILD_TABLES
        ]
        out += [
            "DROP TRIGGER IF EXISTS trg_journal_entries_guard"
            " ON journal_entries",
            "DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs",
            "DROP FUNCTION IF EXISTS entry_children_guard()",
            "DROP FUNCTION IF EXISTS journal_entries_guard()",
            "DROP FUNCTION IF EXISTS audit_logs_append_only()",
        ]
        return out
    return []


def install_triggers(conn: Connection) -> None:
    for sql in trigger_statements(conn.dialect.name):
        conn.execute(text(sql))
