# Cash book (TK 111) – Circular 133/2016/TT-BTC

A self-hosted web app for managing the **cash fund** of small and medium businesses and printing books in the Ministry of Finance templates:

- Cash receipt **01-TT**, cash payment voucher **02-TT** (A5, with the amount in words)
- Cash book **S04a-DNN**, detailed cash ledger **S04b-DNN** (A4 landscape PDF, Excel with formulas)
- Cash count report **08a-TT**
- Monthly/yearly period locks, retroactive negative-cash checks, an audit log that cannot be deleted, import from old Excel cash book files

Runs fully offline: no external services are called, and accounting data never leaves the server.

---

## 1. Installing on a VPS with Docker (recommended)

Requirements: Linux VPS with 1 GB RAM, Docker + Docker Compose, Traefik running (handles HTTPS) with a network named `proxy`.

```bash
git clone <repo> soquy && cd soquy
cp .env.example .env
# Edit .env: SECRET_KEY (required), APP_DOMAIN, TRAEFIK_CERTRESOLVER
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # use as SECRET_KEY

docker network create proxy 2>/dev/null || true   # if Traefik has not created it yet
docker compose up -d --build
```

Create the first admin account — choose **one** of two options:

```bash
# Option 1: CLI command
docker compose exec soquy python -m app.cli create-admin --username admin --full-name "Nguyễn Văn A" --role giam_doc
```

Option 2: open `https://<APP_DOMAIN>/setup` in a browser (only works while no user exists yet).

After signing in, go to **Settings** ("Cài đặt") to check:
1. Company information (pre-filled with NVIAI), signer names (director, chief accountant, cashier, bookkeeper).
2. **Opening cash balance** at the book opening date 12/01/2023 (default 0). This field is locked as soon as the first voucher is posted.
3. Voucher number format (default `PT2026-0001`, `PC2026-0001`, restarting every year).

Then create accounts for the cashier and accountants under **Users** ("Người dùng").

> Not using Traefik? Remove the `labels`/`networks: proxy` blocks in `docker-compose.yml` and point another reverse proxy at port 8000. If you access it over plain HTTP (no HTTPS), set `COOKIE_SECURE=false` in `.env`, otherwise the browser will not store the sign-in cookie (except on `http://localhost`).

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | auto-generated in `DATA_DIR/.secret_key` | Key for signing flash-message cookies. Should be set to a fixed value. |
| `DATA_DIR` | `/data` (Docker), `./data` (dev) | Holds `soquy.db`, attachments, temporary import files |
| `DATABASE_URL` | empty = SQLite | PostgreSQL: `postgresql+psycopg://user:pass@host:5432/db` |
| `COOKIE_SECURE` | `false` | `true` when running behind HTTPS |
| `SESSION_HOURS` | `12` | Sign-in session lifetime |
| `MAX_UPLOAD_MB` | `10` | Maximum size of attachments / import files |
| `TIMEZONE` | `Asia/Ho_Chi_Minh` | Display time zone |

### Using PostgreSQL instead of SQLite

In `docker-compose.yml` uncomment the `DATABASE_URL` line, set `POSTGRES_PASSWORD` in `.env`, then:

```bash
docker compose --profile postgres up -d --build
```

The schema (including the anti-edit/delete triggers) is created automatically when the container starts (`python -m app.cli migrate`).

### Upgrading

```bash
docker compose exec soquy python -m app.cli backup -o /backups/truoc-cap-nhat.tar.gz
git pull
docker compose up -d --build      # migrations run automatically on startup
```

---

## 2. Backup

One command exports **the whole DB + attachments** to a single `.tar.gz` file:

```bash
# Docker (the file ends up in ./backups on the server)
docker compose exec soquy python -m app.cli backup -o /backups/soquy-$(date +%Y%m%d).tar.gz

# Without Docker
.venv/bin/python -m app.cli backup                 # -> backups/soquy-YYYYMMDD-HHMMSS.tar.gz
```

The backup file contains:

| Component | Content |
|---|---|
| `manifest.json` | timestamp, DB type, schema version, row count per table |
| `data.json` | all tables as JSON (usable for both SQLite and PostgreSQL) |
| `soquy.db` | a consistent SQLite snapshot (SQLite backup API, works while the app is running) |
| `attachments/` | attached invoice images/PDFs |

Automatic daily backup at 23:30 (the server's crontab):

```cron
30 23 * * * cd /opt/soquy && docker compose exec -T soquy python -m app.cli backup -o /backups/soquy-$(date +\%Y\%m\%d).tar.gz && find /opt/soquy/backups -name 'soquy-*.tar.gz' -mtime +60 -delete
```

Copy the `backups/` folder somewhere else (external drive, another machine) regularly.

## 3. Restore

A restore **overwrites all current data** (the previous SQLite file is kept as `soquy.db.bak-<timestamp>`).

```bash
# 1. Stop the web app so nobody writes data during the restore
docker compose stop soquy

# 2. Restore (runs a temporary container sharing the data volume)
docker compose run --rm soquy python -m app.cli restore /backups/soquy-20261013.tar.gz --yes

# 3. Start again
docker compose start soquy
```

Without Docker: stop the app, then run `.venv/bin/python -m app.cli restore <file> --yes`.

- SQLite: loads from `soquy.db` in the backup file. Add `--json` to load from `data.json`.
- PostgreSQL: always loads from `data.json` (drops the old schema, recreates it via migrations, loads the data, resets the sequences).
- A SQLite backup can be restored into PostgreSQL (change `DATABASE_URL`, then run `restore --json`).

Forgotten admin password:

```bash
docker compose exec soquy python -m app.cli reset-password --username admin
```

---

## 4. Running for development (without Docker)

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Pango (for WeasyPrint: `brew install pango` on macOS, `apt install libpango-1.0-0 libpangoft2-1.0-0` on Debian/Ubuntu).

```bash
make setup        # create .venv, install libraries
make tailwind     # download the Tailwind standalone CLI into tools/ (no Node needed)
make css          # build app/static/css/app.css
make dev          # migrate + run http://127.0.0.1:8000 (go to /setup to create the admin)
make test         # pytest (SQLite)
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/soquy_test .venv/bin/pytest   # run the tests on PostgreSQL
```

---

## 5. Usage

### Roles

| Role | Permissions |
|---|---|
| Cashier ("Thủ quỹ") | Create, edit, delete drafts; post; view the S04a cash book; cash counts |
| Accountant ("Kế toán") | + enter/edit contra accounts; view S04b; cancel vouchers; record adjustments; export PDF/Excel; catalogs; audit log |
| Chief accountant / Director ("Kế toán trưởng / Giám đốc") | + lock/unlock periods; manage users; settings; Excel import |

### Main business rules

- Amounts are stored as whole dong (integers); displayed as `10.000.000`.
- Vouchers: **draft → posted → (cancelled)**. Only posted vouchers enter the cash book. Voucher numbers are assigned at posting, continuous and unique.
- Posted vouchers are **never deleted, and their amount/date/type are never edited** (blocked at the DB level too). Mistakes are corrected by **cancelling** (open period, reason required) or by **recording an adjustment** (a new voucher referencing the original).
- **No negative cash**, even for back-dated payments: the software checks the balance at every point from that date onward and reports exactly which date and line would go negative.
- Monthly/yearly period locks, **sequential in time**. Unlocking requires a reason. Every write action is recorded in the audit log (append-only).

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Alt+T` / `Alt+C` | New cash receipt / cash payment voucher |
| `Alt+S` | Open the cash book |
| `/` | Search |
| `Enter` | Save voucher (draft) |
| `Ctrl+Enter` | Save and post |
| `?` | Shortcut list |

Fast amount entry: `500k` = 500.000; `2tr` = 2.000.000; `1,5tr` = 1.500.000 (Vietnamese number format; "tr" = triệu, million).

### Importing data from an old Excel cash book

Menu **"Nhập từ Excel"** (Import from Excel) (Chief accountant/Director). The `.xlsx` file needs:

- Sheet `S04a-DNN`: opening balance in cell **H12**; data from **row 13**, columns **A–I**: posting date, voucher date, receipt number, payment number, description, receipts, payments, balance, notes.
- (Optional) sheet `S04b-DNN`: column **F** is the contra account, matched by voucher number.
- Rows with the note "Dòng ví dụ" (example row) are skipped.

The software shows a preview (rows to be imported, error rows: missing date, missing voucher number, both receipt and payment, causing negative cash, …). Nothing is written to the DB until you confirm; the whole import runs in **one transaction**. Imported vouchers keep their original numbers and are in posted status. Excel files exported from the software can be imported again.

---

## 6. Technical

- Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Pydantic v2; SQLite (WAL) or PostgreSQL.
- UI: Jinja2 + HTMX + Alpine.js, Tailwind CSS (standalone CLI, JS vendored, no Node needed at runtime). Responsive, light/dark, Vietnamese/English (print templates are always in Vietnamese).
- Printing: WeasyPrint (HTML → PDF), Excel: openpyxl (the Balance column is a formula).
- Security: argon2 passwords, DB-stored sessions + HttpOnly cookie, CSRF, brute-force protection, security headers.
- Book templates configured per accounting regime in `app/core/regimes.py` (this release: TT133; prepared for TT99/2025, TT58/2026).

```
app/
  core/        config, db, security, i18n, money (amounts in words), audit, pdf, regimes, shared templates + print templates
  features/    <feature>/{router.py, service.py, models.py, templates/, locales/}
               auth, settings, vouchers, ledger, accounts, partners, locks, cashcount, checks,
               reports, dashboard, imports, audit, about
migrations/    Alembic (including the append-only triggers)
tests/         pytest (section 8 business tests in tests/test_business.py)
docs/          PLAN.md, PROGRESS.md, DECISIONS.md
```

## 7. Legal notice

The software supports cash fund record-keeping using the Circular 133/2016/TT-BTC templates. The business is solely responsible for the accuracy of its figures and for compliance with current accounting and tax regulations. If you use accounting software to keep your books, check the rules on accounting software and the applicable regime (TT133, TT99/2025, TT58/2026) with your accountant or the tax authority.
