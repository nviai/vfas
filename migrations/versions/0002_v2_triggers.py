"""Data-protection triggers (snapshot as of v2).

The audit log is append-only and posted entries are immutable.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQLITE_UP = [
    (
        "CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_update BEFORE "
        "UPDATE ON audit_logs\n"
        "           BEGIN SELECT RAISE(ABORT, 'audit_logs is "
        "append-only'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_audit_logs_no_delete BEFORE "
        "DELETE ON audit_logs\n"
        "           BEGIN SELECT RAISE(ABORT, 'audit_logs is "
        "append-only'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_je_no_delete BEFORE DELETE ON "
        "journal_entries\n"
        "           WHEN OLD.status <> 'draft'\n"
        "           BEGIN SELECT RAISE(ABORT, 'posted/cancelled entry "
        "cannot be deleted'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_je_locked_fields BEFORE UPDATE "
        "ON journal_entries\n"
        "           WHEN OLD.status <> 'draft' AND (NEW.company_id IS "
        "NOT OLD.company_id OR NEW.doc_type IS NOT OLD.doc_type OR "
        "NEW.number IS NOT OLD.number OR NEW.number_seq IS NOT "
        "OLD.number_seq OR NEW.date IS NOT OLD.date OR NEW.doc_date IS "
        "NOT OLD.doc_date OR NEW.posting_seq IS NOT OLD.posting_seq)\n"
        "           BEGIN SELECT RAISE(ABORT, 'locked fields of a posted "
        "entry cannot change'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_je_status_flow BEFORE UPDATE "
        "OF status ON journal_entries\n"
        "           WHEN (OLD.status = 'cancelled' AND NEW.status <> "
        "'cancelled')\n"
        "             OR (OLD.status = 'posted' AND NEW.status = "
        "'draft')\n"
        "           BEGIN SELECT RAISE(ABORT, 'invalid entry status "
        "transition'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_je_post_has_lines BEFORE "
        "UPDATE OF status ON journal_entries\n"
        "           WHEN OLD.status = 'draft' AND NEW.status = 'posted'\n"
        "             AND (SELECT COUNT(*) FROM journal_lines WHERE "
        "entry_id = NEW.id) = 0\n"
        "           BEGIN SELECT RAISE(ABORT, 'entry without lines "
        "cannot be posted'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_journal_lines_insert_guard "
        "BEFORE INSERT ON journal_lines\n"
        "                    WHEN (SELECT status FROM journal_entries "
        "WHERE id = NEW.entry_id) <> 'draft'\n"
        "                    BEGIN SELECT RAISE(ABORT, 'lines of a "
        "posted entry cannot change'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_journal_lines_update_guard "
        "BEFORE UPDATE ON journal_lines\n"
        "                    WHEN (SELECT status FROM journal_entries "
        "WHERE id = OLD.entry_id) <> 'draft'\n"
        "                    BEGIN SELECT RAISE(ABORT, 'lines of a "
        "posted entry cannot change'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_journal_lines_delete_guard "
        "BEFORE DELETE ON journal_lines\n"
        "                    WHEN (SELECT status FROM journal_entries "
        "WHERE id = OLD.entry_id) <> 'draft'\n"
        "                    BEGIN SELECT RAISE(ABORT, 'lines of a "
        "posted entry cannot change'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_vat_records_insert_guard "
        "BEFORE INSERT ON vat_records\n"
        "                    WHEN (SELECT status FROM journal_entries "
        "WHERE id = NEW.entry_id) <> 'draft'\n"
        "                    BEGIN SELECT RAISE(ABORT, 'lines of a "
        "posted entry cannot change'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_vat_records_update_guard "
        "BEFORE UPDATE ON vat_records\n"
        "                    WHEN (SELECT status FROM journal_entries "
        "WHERE id = OLD.entry_id) <> 'draft'\n"
        "                    BEGIN SELECT RAISE(ABORT, 'lines of a "
        "posted entry cannot change'); END"
    ),
    (
        "CREATE TRIGGER IF NOT EXISTS trg_vat_records_delete_guard "
        "BEFORE DELETE ON vat_records\n"
        "                    WHEN (SELECT status FROM journal_entries "
        "WHERE id = OLD.entry_id) <> 'draft'\n"
        "                    BEGIN SELECT RAISE(ABORT, 'lines of a "
        "posted entry cannot change'); END"
    ),
]

SQLITE_DOWN = [
    "DROP TRIGGER IF EXISTS trg_audit_logs_no_update",
    "DROP TRIGGER IF EXISTS trg_audit_logs_no_delete",
    "DROP TRIGGER IF EXISTS trg_je_no_delete",
    "DROP TRIGGER IF EXISTS trg_je_locked_fields",
    "DROP TRIGGER IF EXISTS trg_je_status_flow",
    "DROP TRIGGER IF EXISTS trg_je_post_has_lines",
    "DROP TRIGGER IF EXISTS trg_journal_lines_insert_guard",
    "DROP TRIGGER IF EXISTS trg_journal_lines_update_guard",
    "DROP TRIGGER IF EXISTS trg_journal_lines_delete_guard",
    "DROP TRIGGER IF EXISTS trg_vat_records_insert_guard",
    "DROP TRIGGER IF EXISTS trg_vat_records_update_guard",
    "DROP TRIGGER IF EXISTS trg_vat_records_delete_guard",
]

PG_UP = [
    (
        "CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS "
        "trigger AS $$\n"
        "           BEGIN RAISE EXCEPTION 'audit_logs is append-only'; "
        "END; $$ LANGUAGE plpgsql"
    ),
    "DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs",
    (
        "CREATE TRIGGER trg_audit_logs_append_only BEFORE UPDATE OR "
        "DELETE ON audit_logs\n"
        "           FOR EACH ROW EXECUTE FUNCTION "
        "audit_logs_append_only()"
    ),
    (
        "CREATE OR REPLACE FUNCTION journal_entries_guard() RETURNS "
        "trigger AS $$\n"
        "           BEGIN\n"
        "             IF TG_OP = 'DELETE' THEN\n"
        "               IF OLD.status <> 'draft' THEN\n"
        "                 RAISE EXCEPTION 'posted/cancelled entry cannot "
        "be deleted';\n"
        "               END IF;\n"
        "               RETURN OLD;\n"
        "             END IF;\n"
        "             IF OLD.status <> 'draft' AND (NEW.company_id IS "
        "DISTINCT FROM OLD.company_id OR NEW.doc_type IS DISTINCT FROM "
        "OLD.doc_type OR NEW.number IS DISTINCT FROM OLD.number OR "
        "NEW.number_seq IS DISTINCT FROM OLD.number_seq OR NEW.date IS "
        "DISTINCT FROM OLD.date OR NEW.doc_date IS DISTINCT FROM "
        "OLD.doc_date OR NEW.posting_seq IS DISTINCT FROM "
        "OLD.posting_seq) THEN\n"
        "               RAISE EXCEPTION 'locked fields of a posted entry "
        "cannot change';\n"
        "             END IF;\n"
        "             IF (OLD.status = 'cancelled' AND NEW.status <> "
        "'cancelled')\n"
        "                OR (OLD.status = 'posted' AND NEW.status = "
        "'draft') THEN\n"
        "               RAISE EXCEPTION 'invalid entry status "
        "transition';\n"
        "             END IF;\n"
        "             IF OLD.status = 'draft' AND NEW.status = 'posted'\n"
        "                AND NOT EXISTS (SELECT 1 FROM journal_lines "
        "WHERE entry_id = NEW.id) THEN\n"
        "               RAISE EXCEPTION 'entry without lines cannot be "
        "posted';\n"
        "             END IF;\n"
        "             RETURN NEW;\n"
        "           END; $$ LANGUAGE plpgsql"
    ),
    "DROP TRIGGER IF EXISTS trg_journal_entries_guard ON journal_entries",
    (
        "CREATE TRIGGER trg_journal_entries_guard BEFORE UPDATE OR "
        "DELETE ON journal_entries\n"
        "           FOR EACH ROW EXECUTE FUNCTION journal_entries_guard()"
    ),
    (
        "CREATE OR REPLACE FUNCTION entry_children_guard() RETURNS "
        "trigger AS $$\n"
        "           DECLARE st text;\n"
        "           BEGIN\n"
        "             IF TG_OP = 'INSERT' THEN\n"
        "               SELECT status INTO st FROM journal_entries WHERE "
        "id = NEW.entry_id;\n"
        "             ELSE\n"
        "               SELECT status INTO st FROM journal_entries WHERE "
        "id = OLD.entry_id;\n"
        "             END IF;\n"
        "             IF st IS NOT NULL AND st <> 'draft' THEN\n"
        "               RAISE EXCEPTION 'lines of a posted entry cannot "
        "change';\n"
        "             END IF;\n"
        "             IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;\n"
        "             RETURN NEW;\n"
        "           END; $$ LANGUAGE plpgsql"
    ),
    "DROP TRIGGER IF EXISTS trg_journal_lines_guard ON journal_lines",
    (
        "CREATE TRIGGER trg_journal_lines_guard BEFORE INSERT OR UPDATE "
        "OR DELETE ON journal_lines\n"
        "                         FOR EACH ROW EXECUTE FUNCTION "
        "entry_children_guard()"
    ),
    "DROP TRIGGER IF EXISTS trg_vat_records_guard ON vat_records",
    (
        "CREATE TRIGGER trg_vat_records_guard BEFORE INSERT OR UPDATE OR "
        "DELETE ON vat_records\n"
        "                         FOR EACH ROW EXECUTE FUNCTION "
        "entry_children_guard()"
    ),
]

PG_DOWN = [
    "DROP TRIGGER IF EXISTS trg_journal_lines_guard ON journal_lines",
    "DROP TRIGGER IF EXISTS trg_vat_records_guard ON vat_records",
    "DROP TRIGGER IF EXISTS trg_journal_entries_guard ON journal_entries",
    "DROP TRIGGER IF EXISTS trg_audit_logs_append_only ON audit_logs",
    "DROP FUNCTION IF EXISTS entry_children_guard()",
    "DROP FUNCTION IF EXISTS journal_entries_guard()",
    "DROP FUNCTION IF EXISTS audit_logs_append_only()",
]


def _statements(up: bool) -> list[str]:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return SQLITE_UP if up else SQLITE_DOWN
    if dialect == "postgresql":
        return PG_UP if up else PG_DOWN
    return []


def upgrade() -> None:
    for sql in _statements(True):
        op.execute(sql)


def downgrade() -> None:
    for sql in _statements(False):
        op.execute(sql)
