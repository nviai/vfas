"""Admin commands: python -m app.cli <command>

migrate                        Create/upgrade the database schema
create-admin                   Create an admin account (superadmin),
                               optionally with a company
create-company                 Create a company for a user (the owner)
reset-password                 Reset a user's password
backup [-o FILE]               Back up the whole system (database +
                               attachments) to one .tar.gz file
restore FILE --yes             Restore from a backup file (overwrites
                               the current data)
export-company ID [-o FILE]    Export all data of one company
                               (JSON + attachments)
demo [--email E --password P]  Create a sample company with about one
                               year of data to try the app
"""

from __future__ import annotations

import argparse
import getpass
import io
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Date, DateTime, Integer, Numeric, inspect, select, text

from app.core.config import get_settings
from app.core.db import configure_engine, session_scope

BACKUP_FORMAT = 2


def _engine():
    settings = get_settings()
    settings.ensure_dirs()
    return configure_engine(settings.db_url)


def cmd_migrate(_args) -> int:
    from app.core.migrate import upgrade_db

    _engine()
    upgrade_db(get_settings().db_url)
    print("Đã cập nhật schema.")
    return 0


def _ask_password(given: str | None) -> str:
    if given:
        return given
    pw = getpass.getpass("Mật khẩu: ")
    if pw != getpass.getpass("Nhập lại mật khẩu: "):
        raise SystemExit("Mật khẩu nhập lại không khớp")
    return pw


def _parse_date(value: str | None) -> date | None:
    from app.core.dates import parse_date

    return parse_date(value) if value else None


def cmd_create_admin(args) -> int:
    from app.core.errors import BusinessError
    from app.features.auth.service import create_user
    from app.features.org.service import CompanyInput, create_company

    _engine()
    password = _ask_password(args.password)
    with session_scope() as db:
        try:
            user = create_user(
                db,
                args.email,
                password,
                args.full_name or "",
                is_superadmin=not args.no_superadmin,
                commit=False,
            )
            db.flush()
            if args.company:
                create_company(
                    db,
                    user,
                    CompanyInput(
                        name=args.company,
                        tax_code=args.tax_code or "",
                        start_date=_parse_date(args.start_date),
                    ),
                )
        except BusinessError as e:
            print(f"Lỗi: {e.message}", file=sys.stderr)
            return 1
        print(
            f"Đã tạo người dùng {user.email}"
            + (f" và công ty {args.company}" if args.company else "")
            + "."
        )
    return 0


def cmd_create_company(args) -> int:
    from app.core.errors import BusinessError
    from app.features.auth.service import get_user_by_email
    from app.features.org.service import CompanyInput, create_company

    _engine()
    with session_scope() as db:
        user = get_user_by_email(db, args.owner_email)
        if user is None:
            print("Không tìm thấy người dùng", file=sys.stderr)
            return 1
        try:
            c = create_company(
                db,
                user,
                CompanyInput(
                    name=args.name,
                    tax_code=args.tax_code or "",
                    start_date=_parse_date(args.start_date),
                ),
            )
        except BusinessError as e:
            print(f"Lỗi: {e.message}", file=sys.stderr)
            return 1
        print(f"Đã tạo công ty #{c.id} {c.name}.")
    return 0


def cmd_demo(args) -> int:
    """Create (or reuse) a user plus a demo company.

    The demo company gets data from January 1 of last year to today.
    """
    from app.core.errors import BusinessError
    from app.core.migrate import upgrade_db
    from app.core.registry import load_features
    from app.core.security import build_current_user
    from app.core.tenancy import Ctx
    from app.demo import build_demo
    from app.features.auth.service import (
        create_user,
        get_user_by_email,
        users_exist,
    )
    from app.features.org.service import CompanyInput, create_company

    _engine()
    upgrade_db(get_settings().db_url)
    load_features()
    with session_scope() as db:
        user = get_user_by_email(db, args.email)
        created = False
        if user is None:
            try:
                user = create_user(
                    db,
                    args.email,
                    args.password,
                    args.full_name,
                    is_superadmin=not users_exist(db),
                    commit=False,
                )
            except BusinessError as e:
                print(f"Lỗi: {e.message}", file=sys.stderr)
                return 1
            db.flush()
            created = True
        company = create_company(
            db,
            user,
            CompanyInput(
                name=args.company,
                short_name=args.short_name,
                tax_code=args.tax_code,
                address="Tầng 5, 123 Nguyễn Văn Linh, phường Tân Phong, "
                "Quận 7, TP. Hồ Chí Minh",
            ),
        )
        company.legal_rep, company.chief_accountant = (
            "Nguyễn Văn Bình",
            "Trần Thị Hồng Nhung",
        )
        company.preparer, company.cashier = "Trần Thị Hồng Nhung", "Lê Thị Thu"
        db.commit()
        ctx = Ctx(
            company_id=company.id,
            user=build_current_user(db, user, company.id),
            via="cli",
        )
        try:
            summary = build_demo(db, ctx)
        except BusinessError as e:
            print(f"Lỗi khi tạo dữ liệu mẫu: {e.message}", file=sys.stderr)
            return 1
        print(
            f"Đã tạo công ty demo #{company.id} {company.name}: "
            f"{summary['total']} chứng từ "
            f"({summary['start']:%d/%m/%Y} – {summary['end']:%d/%m/%Y})."
        )
        if created:
            print(f"Đăng nhập: {user.email} / {args.password}")
        else:
            print(
                f"Đăng nhập bằng tài khoản có sẵn {user.email} "
                "rồi chọn công ty demo."
            )
    return 0


def cmd_reset_password(args) -> int:
    from app.core.errors import BusinessError
    from app.features.auth.service import get_user_by_email, set_password

    _engine()
    password = _ask_password(args.password)
    with session_scope() as db:
        user = get_user_by_email(db, args.email)
        if user is None:
            print("Không tìm thấy người dùng", file=sys.stderr)
            return 1
        try:
            set_password(db, user, password)
        except BusinessError as e:
            print(f"Lỗi: {e.message}", file=sys.stderr)
            return 1
    print(f"Đã đặt lại mật khẩu cho {args.email}.")
    return 0


def cmd_send_test_email(args) -> int:
    from app.core import mail

    if not mail.mail_enabled():
        print("Chưa cấu hình SMTP_HOST", file=sys.stderr)
        return 1
    try:
        mail.send(mail.render_mail("email/test.html", args.to, "vi"))
    except mail.MailError as e:
        print(f"Gửi email thất bại: {e}", file=sys.stderr)
        return 1
    print(f"Đã gửi email thử tới {args.to}.")
    return 0


def cmd_einvoice_poll(_args) -> int:
    from app.features.einvoice import service as einvoice

    _engine()
    with session_scope() as db:
        counts = einvoice.poll(db)
    print(
        f"Đã kiểm tra {counts['checked']} hóa đơn: "
        f"{counts['issued']} đã cấp mã, {counts['failed']} lỗi."
    )
    return 0


# --------------------------------------------------------------- backup


def _json_default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o))


def dump_tables(conn) -> dict:
    """Dump every table as JSON (independent of the database type)."""
    import app.models  # noqa: F401
    from app.core.db import Base

    data = {}
    for table in Base.metadata.sorted_tables:
        rows = (
            conn.execute(table.select().order_by(*table.primary_key.columns))
            .mappings()
            .all()
        )
        data[table.name] = [dict(r) for r in rows]
    rev = None
    if inspect(conn).has_table("alembic_version"):
        rev = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar()
    return {"alembic_revision": rev, "tables": data}


# ----------------------------------------- backup encryption (optional)

# Keep the old prefix: changing it would make existing encrypted
# backups unreadable.
ENC_MAGIC = b"NVKENC1\n"


def _passphrase() -> str:
    import os

    return os.environ.get("BACKUP_PASSPHRASE", "")


def _fernet_from(passphrase: str, salt: bytes):
    import base64

    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        passphrase.encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def protect_file(path: Path) -> Path:
    """Set mode 600 on a backup file and encrypt it if configured.

    When BACKUP_PASSPHRASE is set, the file is encrypted (scrypt +
    Fernet) into <name>.enc.
    """
    import os

    pw = _passphrase()
    if pw:
        salt = os.urandom(16)
        data = _fernet_from(pw, salt).encrypt(path.read_bytes())
        enc = path.with_name(path.name + ".enc")
        enc.write_bytes(ENC_MAGIC + salt + data)
        path.unlink()
        path = enc
    os.chmod(path, 0o600)
    return path


def open_archive(archive: Path, tmpdir: str) -> Path:
    """Return the path of a readable tar.gz file.

    Encrypted backups are decrypted into the temp directory first.
    """
    raw = archive.read_bytes()
    if not raw.startswith(ENC_MAGIC):
        return archive
    pw = _passphrase()
    if not pw:
        raise SystemExit(
            "File sao lưu đã mã hóa – đặt biến môi trường "
            "BACKUP_PASSPHRASE để giải mã"
        )
    from cryptography.fernet import InvalidToken

    salt = raw[len(ENC_MAGIC) : len(ENC_MAGIC) + 16]
    try:
        data = _fernet_from(pw, salt).decrypt(raw[len(ENC_MAGIC) + 16 :])
    except InvalidToken:
        raise SystemExit(
            "Sai BACKUP_PASSPHRASE hoặc file sao lưu bị hỏng"
        ) from None
    out = Path(tmpdir) / "backup.tar.gz"
    out.write_bytes(data)
    return out


def create_backup(output: Path) -> Path:
    settings = get_settings()
    engine = _engine()
    output.parent.mkdir(parents=True, exist_ok=True)
    with engine.connect() as conn:
        dump = dump_tables(conn)
    manifest = {
        "format": BACKUP_FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": engine.dialect.name,
        "alembic_revision": dump["alembic_revision"],
        "counts": {k: len(v) for k, v in dump["tables"].items()},
    }
    with (
        tempfile.TemporaryDirectory() as tmp,
        tarfile.open(output, "w:gz") as tar,
    ):

        def add_bytes(name: str, payload: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = int(datetime.now().timestamp())
            tar.addfile(info, io.BytesIO(payload))

        add_bytes(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
        )
        add_bytes(
            "data.json",
            json.dumps(
                dump["tables"], ensure_ascii=False, default=_json_default
            ).encode(),
        )
        if engine.dialect.name == "sqlite":
            # Consistent copy via the SQLite backup API (safe while the
            # app is running).
            src_path = engine.url.database
            snap = Path(tmp) / "database.db"
            with (
                closing(sqlite3.connect(src_path)) as src,
                closing(sqlite3.connect(snap)) as dst,
            ):
                src.backup(dst)
            tar.add(snap, arcname="database.db")
        if settings.attachments_dir.exists():
            tar.add(settings.attachments_dir, arcname="attachments")
    return protect_file(output)


def cmd_backup(args) -> int:
    out = (
        Path(args.output)
        if args.output
        else Path("backups") / f"ketoan-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    )
    path = create_backup(out)
    print(f"Đã sao lưu: {path} ({path.stat().st_size // 1024} KB)")
    return 0


def _coerce(table, row: dict) -> dict:
    out = {}
    for col in table.columns:
        if col.name not in row:
            continue
        val = row[col.name]
        if val is not None and isinstance(val, str):
            if isinstance(col.type, DateTime):
                val = datetime.fromisoformat(val)
            elif isinstance(col.type, Date):
                val = date.fromisoformat(val)
            elif isinstance(col.type, Numeric) and not isinstance(
                col.type, Integer
            ):
                val = Decimal(val)
        out[col.name] = val
    return out


def load_tables(engine, tables: dict) -> None:
    """Recreate an empty schema and load the JSON data.

    Used for PostgreSQL, or when the backup has no .db file.
    """
    import app.models  # noqa: F401
    from app.core.db import Base
    from app.core.migrate import upgrade_db
    from app.core.triggers import drop_statements, install_triggers

    with engine.begin() as conn:
        Base.metadata.drop_all(conn)
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    upgrade_db(str(engine.url.render_as_string(hide_password=False)))
    with engine.begin() as conn:
        # Disable triggers while loading (posted entries are loaded
        # together with their lines), then re-enable them right after.
        for sql in drop_statements(conn.dialect.name):
            conn.execute(text(sql))
        for table in Base.metadata.sorted_tables:
            rows = [_coerce(table, r) for r in tables.get(table.name, [])]
            if rows:
                conn.execute(table.insert(), rows)
        if engine.dialect.name == "postgresql":
            for table in Base.metadata.sorted_tables:
                pk = table.columns.get("id")
                if (
                    pk is not None
                    and pk.primary_key
                    and isinstance(pk.type, Integer)
                ):
                    conn.execute(
                        text(
                            "SELECT setval(pg_get_serial_sequence("
                            f"'{table.name}', 'id'), "
                            "COALESCE((SELECT MAX(id) FROM "
                            f"{table.name}), 0) + 1, false)"
                        )
                    )
        install_triggers(conn)


def restore_backup(archive: Path, *, prefer_json: bool = False) -> str:
    settings = get_settings()
    engine = _engine()
    with (
        tempfile.TemporaryDirectory() as tmp,
        tarfile.open(open_archive(archive, tmp), "r:gz") as tar,
    ):
        tar.extractall(tmp, filter="data")
        root = Path(tmp)
        manifest = json.loads((root / "manifest.json").read_text())
        if manifest.get("format") != BACKUP_FORMAT:
            raise SystemExit("File sao lưu không đúng định dạng")
        engine.dispose()
        if (
            engine.dialect.name == "sqlite"
            and (root / "database.db").exists()
            and not prefer_json
        ):
            target = Path(engine.url.database)
            # Use the SQLite backup API (instead of overwriting the
            # file) to stay safe with WAL and open connections.
            if target.exists():
                bak = target.with_name(
                    f"{target.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
                )
                with (
                    closing(sqlite3.connect(target)) as cur,
                    closing(sqlite3.connect(bak)) as dst,
                ):
                    cur.backup(dst)
            with (
                closing(sqlite3.connect(root / "database.db")) as src,
                closing(sqlite3.connect(target)) as dst,
            ):
                src.backup(dst)
            from app.core.migrate import upgrade_db

            upgrade_db(settings.db_url)
            method = "sqlite-file"
        else:
            tables = json.loads((root / "data.json").read_text())
            load_tables(engine, tables)
            method = "json"
        att = settings.attachments_dir
        if att.exists():
            shutil.rmtree(att)
        if (root / "attachments").exists():
            shutil.copytree(root / "attachments", att)
        else:
            att.mkdir(parents=True, exist_ok=True)
    return method


def cmd_restore(args) -> int:
    if not args.yes:
        print(
            "Khôi phục sẽ GHI ĐÈ toàn bộ dữ liệu hiện tại. "
            "Dừng ứng dụng trước, rồi chạy lại với --yes.",
            file=sys.stderr,
        )
        return 2
    method = restore_backup(Path(args.file), prefer_json=args.json)
    print(
        f"Đã khôi phục từ {args.file} (phương thức: {method}). "
        "Khởi động lại ứng dụng."
    )
    return 0


def export_company(company_id: int, output: Path) -> Path:
    """Export one company's data.

    Includes every table with a company_id plus the member users
    (without passwords).
    """
    import app.models  # noqa: F401
    from app.core.db import Base

    settings = get_settings()
    engine = _engine()
    data: dict = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name == "companies":
                rows = (
                    conn.execute(
                        table.select().where(table.c.id == company_id)
                    )
                    .mappings()
                    .all()
                )
            elif "company_id" in table.c and table.name not in (
                "user_sessions",
            ):
                rows = (
                    conn.execute(
                        table.select()
                        .where(table.c.company_id == company_id)
                        .order_by(*table.primary_key.columns)
                    )
                    .mappings()
                    .all()
                )
            else:
                continue
            data[table.name] = [dict(r) for r in rows]
        if not data.get("companies"):
            raise SystemExit("Không tìm thấy công ty")
        users = Base.metadata.tables["users"]
        members = Base.metadata.tables["memberships"]
        rows = (
            conn.execute(
                select(
                    users.c.id,
                    users.c.email,
                    users.c.full_name,
                    members.c.role,
                )
                .join(members, members.c.user_id == users.c.id)
                .where(members.c.company_id == company_id)
            )
            .mappings()
            .all()
        )
        data["members"] = [dict(r) for r in rows]
        data.pop("api_tokens", None)
    # Do not export secrets: the (encrypted) AI provider API key in
    # the company settings.
    for row in data.get("companies", []):
        cfg = dict(row.get("settings") or {})
        if isinstance(cfg.get("ai"), dict):
            cfg["ai"] = {
                k: v for k, v in cfg["ai"].items() if k != "api_key_enc"
            }
        row["settings"] = cfg
    # ...and the (encrypted) e-invoice provider passwords.
    for row in data.get("einvoice_configs", []):
        row["secrets"] = None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as tar:
        payload = json.dumps(
            {
                "format": BACKUP_FORMAT,
                "company_id": company_id,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "tables": data,
            },
            ensure_ascii=False,
            default=_json_default,
            indent=1,
        ).encode()
        info = tarfile.TarInfo("company.json")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
        att = settings.attachments_dir / str(company_id)
        if att.exists():
            tar.add(att, arcname="attachments")
    return protect_file(output)


def cmd_export_company(args) -> int:
    out = (
        Path(args.output)
        if args.output
        else Path("backups")
        / f"company-{args.company_id}-{datetime.now():%Y%m%d-%H%M%S}.tar.gz"
    )
    path = export_company(int(args.company_id), out)
    print(f"Đã xuất dữ liệu công ty #{args.company_id}: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Quản trị VFAS"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Tạo/cập nhật schema DB").set_defaults(
        func=cmd_migrate
    )

    p = sub.add_parser(
        "create-admin", help="Tạo tài khoản quản trị (superadmin)"
    )
    p.add_argument("--email", required=True)
    p.add_argument("--full-name", default="")
    p.add_argument("--password", help="Bỏ trống để nhập ẩn")
    p.add_argument(
        "--no-superadmin",
        action="store_true",
        help="Tài khoản thường (không quản trị nền tảng)",
    )
    p.add_argument(
        "--company", help="Tạo luôn công ty với tên này (người này là chủ)"
    )
    p.add_argument("--tax-code", default="")
    p.add_argument("--start-date", help="Ngày bắt đầu hạch toán dd/mm/yyyy")
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("create-company", help="Tạo công ty cho một người dùng")
    p.add_argument("--owner-email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--tax-code", default="")
    p.add_argument("--start-date", help="dd/mm/yyyy")
    p.set_defaults(func=cmd_create_company)

    p = sub.add_parser("reset-password", help="Đặt lại mật khẩu")
    p.add_argument("--email", required=True)
    p.add_argument("--password", help="Bỏ trống để nhập ẩn")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser(
        "send-test-email", help="Gửi email thử để kiểm tra cấu hình SMTP"
    )
    p.add_argument("--to", required=True)
    p.set_defaults(func=cmd_send_test_email)

    sub.add_parser(
        "einvoice-poll",
        help="Cập nhật trạng thái hóa đơn điện tử đang chờ (chạy theo cron)",
    ).set_defaults(func=cmd_einvoice_poll)

    p = sub.add_parser("demo", help="Tạo công ty mẫu có dữ liệu ~1 năm")
    p.add_argument("--email", default="demo@example.com")
    p.add_argument("--password", default="Demo@2026")
    p.add_argument("--full-name", default="Người dùng demo")
    p.add_argument(
        "--company", default="CÔNG TY TNHH GIẢI PHÁP CÔNG NGHỆ AN PHÁT (DEMO)"
    )
    p.add_argument("--short-name", default="An Phát (demo)")
    p.add_argument("--tax-code", default="0318888888")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("export-company", help="Xuất dữ liệu một công ty")
    p.add_argument("company_id")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export_company)

    p = sub.add_parser("backup", help="Sao lưu DB + file đính kèm")
    p.add_argument("-o", "--output", help="Đường dẫn file .tar.gz")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="Khôi phục từ file sao lưu")
    p.add_argument("file")
    p.add_argument(
        "--yes", action="store_true", help="Xác nhận ghi đè dữ liệu hiện tại"
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Nạp từ data.json thay vì file SQLite",
    )
    p.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
