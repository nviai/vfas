"""CLI commands.

Create admin/company, reset password, back up -> damage data ->
restore, export one company's data.
"""

import json
import tarfile
from datetime import date

import pytest
from sqlalchemy import func, select

from app import cli
from app.core.db import SessionLocal
from app.features.auth.service import authenticate, get_user_by_email
from app.features.journal import queries
from app.features.journal import service as js
from app.features.journal.models import Attachment, JournalEntry
from app.features.org.models import Company
from tests.conftest import post


def _state(company_id):
    with SessionLocal() as db:
        return (
            db.scalar(select(func.count(JournalEntry.id))),
            queries.balance(db, company_id, "1111"),
            db.scalar(select(func.count(Attachment.id))),
        )


def test_admin_company_and_password(engine):
    assert (
        cli.main(
            [
                "create-admin",
                "--email",
                "Admin@NVIAI.vn",
                "--full-name",
                "Giám đốc",
                "--password",
                "Ketoan#2026",
                "--company",
                "Công ty A",
                "--tax-code",
                "0000000001",
                "--start-date",
                "01/01/2026",
            ]
        )
        == 0
    )
    with SessionLocal() as db:
        u = get_user_by_email(db, "admin@nviai.vn")
        assert u.is_superadmin
        c = db.scalar(select(Company))
        assert (
            c.name == "Công ty A"
            and c.start_date == date(2026, 1, 1)
            and c.tax_code == "0000000001"
        )
    assert (
        cli.main(
            [
                "create-admin",
                "--email",
                "admin@nviai.vn",
                "--password",
                "Ketoan#2026",
            ]
        )
        == 1
    )  # duplicate email
    assert (
        cli.main(
            [
                "create-company",
                "--owner-email",
                "admin@nviai.vn",
                "--name",
                "Công ty B",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            ["create-company", "--owner-email", "khong@co.vn", "--name", "X"]
        )
        == 1
    )
    assert (
        cli.main(
            [
                "reset-password",
                "--email",
                "admin@nviai.vn",
                "--password",
                "matkhau456",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "reset-password",
                "--email",
                "admin@nviai.vn",
                "--password",
                "ngan",
            ]
        )
        == 1
    )
    with SessionLocal() as db:
        assert (
            authenticate(db, "admin@nviai.vn", "matkhau456", None).email
            == "admin@nviai.vn"
        )
        assert db.scalar(select(func.count(Company.id))) == 2


def test_backup_restore_sqlite_and_json(tmp_path, settings, db, ctx, company):
    pt = post(db, ctx, date(2024, 1, 5), "1111", "4111", 10_000_000, "PT")
    pc = post(db, ctx, date(2024, 2, 1), "6422", "1111", 3_000_000, "PC")
    js.add_attachment(db, ctx, pt, "hoadon.pdf", b"%PDF-1.4 test")
    before = _state(company.id)
    out = tmp_path / "bk.tar.gz"
    assert cli.main(["backup", "-o", str(out)]) == 0
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert {"manifest.json", "data.json"} <= set(names) and any(
        n.startswith("attachments") for n in names
    )

    # Damage the data: cancel the payment and delete the attachment file
    # on disk
    js.cancel_entry(db, ctx, pc, "test")
    db.close()
    for f in settings.attachments_dir.rglob("*"):
        if f.is_file():
            f.unlink()
    assert _state(company.id) != before

    assert cli.main(["restore", str(out)]) == 2  # missing --yes
    if settings.db_url.startswith("sqlite"):
        assert cli.main(["restore", str(out), "--yes"]) == 0
        assert _state(company.id) == before
    assert cli.main(["restore", str(out), "--yes", "--json"]) == 0
    assert _state(company.id) == before
    assert (
        len([f for f in settings.attachments_dir.rglob("*") if f.is_file()])
        == 1
    )
    # The trigger still exists after loading the JSON
    with SessionLocal() as s:
        e = s.scalars(
            select(JournalEntry).where(JournalEntry.status == "posted")
        ).first()
        e.date = date(2024, 3, 1)
        try:
            s.flush()
            raise AssertionError("trigger is missing")
        except Exception as ex:  # noqa: BLE001
            assert "locked fields" in str(ex) or "cannot change" in str(ex)


def test_export_company_excludes_secrets(tmp_path, db, ctx, company):
    from app.core import crypto
    from app.features.auth.service import create_api_token
    from app.features.org.service import update_company_settings

    post(db, ctx, date(2024, 1, 5), "1111", "4111", 1_000_000, "PT")
    update_company_settings(
        db,
        ctx,
        "ai",
        {"enabled": True, "api_key_enc": crypto.encrypt("sk-secret-key")},
    )
    create_api_token(db, ctx, "n8n", ["read"])
    out = tmp_path / "c.tar.gz"
    assert cli.main(["export-company", str(company.id), "-o", str(out)]) == 0
    with tarfile.open(out) as tar:
        data = json.loads(tar.extractfile("company.json").read())
    tables = data["tables"]
    assert tables["companies"][0]["name"] == company.name
    assert len(tables["journal_entries"]) == 1
    assert "api_tokens" not in tables
    assert "api_key_enc" not in json.dumps(tables["companies"])
    assert all("password_hash" not in m for m in tables["members"])
    assert {m["email"] for m in tables["members"]} >= {"giamdoc@nviai.vn"}


def test_encrypted_backup_roundtrip(
    tmp_path, settings, db, ctx, company, monkeypatch
):
    import os
    import stat

    post(db, ctx, date(2024, 1, 5), "1111", "4111", 7_000_000, "PT")
    before = _state(company.id)
    monkeypatch.setenv("BACKUP_PASSPHRASE", "mat-khau-sao-luu-dai")
    out = tmp_path / "bk.tar.gz"
    assert cli.main(["backup", "-o", str(out)]) == 0
    enc = tmp_path / "bk.tar.gz.enc"
    assert enc.exists() and not out.exists()
    assert stat.S_IMODE(os.stat(enc).st_mode) == 0o600
    assert (
        enc.read_bytes().startswith(cli.ENC_MAGIC)
        and b"journal_entries" not in enc.read_bytes()
    )
    post(db, ctx, date(2024, 2, 5), "1111", "4111", 1_000, "PT")
    db.close()
    monkeypatch.setenv("BACKUP_PASSPHRASE", "sai")
    with pytest.raises(SystemExit):
        cli.main(["restore", str(enc), "--yes", "--json"])
    monkeypatch.setenv("BACKUP_PASSPHRASE", "mat-khau-sao-luu-dai")
    assert cli.main(["restore", str(enc), "--yes", "--json"]) == 0
    assert _state(company.id) == before
