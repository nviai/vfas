# Installing and operating VFAS

This document is for people who install and run the server. Before exposing it to the Internet, also read the
[Security guide](SECURITY-GUIDE.md): it has separate checklists for cloud (SaaS) and self-hosted installs.

- [1. Choosing a deployment type](#1-choosing-a-deployment-type)
- [2. Installing with Docker Compose (behind Traefik)](#2-installing-with-docker-compose-behind-traefik)
- [3. Environment variables](#3-environment-variables)
- [4. PostgreSQL](#4-postgresql)
- [5. Upgrading](#5-upgrading)
- [6. Backup, restore, data export](#6-backup-restore-data-export)
- [7. Admin commands](#7-admin-commands)

## 1. Choosing a deployment type

| | Self-host (one company) | Cloud / SaaS (many companies) |
|---|---|---|
| Server | 1 GB VPS or a machine on the LAN | 2–4 GB VPS or larger |
| Database | SQLite (default, 1 process) | PostgreSQL, 2–4 web processes |
| Sign-up | `ALLOW_SIGNUP=false`, the admin invites members | `ALLOW_SIGNUP=true` (needs SMTP – not available yet, see roadmap) |
| Access | HTTPS via a reverse proxy, or a VPN (WireGuard/Tailscale) | HTTPS via Traefik, rate limiting at the proxy |
| AI assistant | Prefer self-hosted AI (Ollama) so data never leaves the server | Each company enters its own API key; a platform-wide key is optional |
| Backup | Daily, encrypted, copied to another machine | Daily, encrypted, pushed to separate storage, restore drills |

## 2. Installing with Docker Compose (behind Traefik)

Requirements: Linux, Docker + Docker Compose, Traefik (handles HTTPS) with a `proxy` network.

```bash
git clone https://github.com/nviai/vfas.git && cd vfas
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # paste into SECRET_KEY in .env
# edit APP_DOMAIN, PUBLIC_URL, ALLOW_SIGNUP…
docker network create proxy 2>/dev/null || true
docker compose up -d --build
```

Create the admin account and the first company using **one** of two options:

```bash
# Option 1: CLI command
docker compose exec vfas python -m app.cli create-admin --email admin@congty.vn --full-name "Nguyễn Văn A" \
  --company "CÔNG TY TNHH ABC" --tax-code 0123456789 --start-date 01/01/2026
```

Option 2: open the `/setup?token=…` link printed in the startup log (`docker compose logs vfas`). The setup page requires this
**setup code** so that strangers cannot take over a fresh server; it only works while no user exists yet.

After signing in:
1. **Company information** ("Thông tin công ty"): signers, VAT filing period, minimum-wage region, voucher number format.
2. **Opening balances** ("Số dư đầu kỳ") as of the accounting start date (entered by hand or from Excel), bank accounts, opening inventory, opening fixed assets.
3. **Members** ("Thành viên"): invite accountants, cashiers… (send the invitation link).
4. **AI assistant** (optional): "Hệ thống → Trợ lý AI" (System → AI assistant).

To try it out with one year of sample data: `docker compose exec vfas python -m app.cli demo`.

> Not using Traefik? Remove the `labels`/`networks: proxy` blocks in `docker-compose.yml`, point another reverse proxy (Caddy,
> Nginx) at port 8000 and set `FORWARDED_ALLOW_IPS` to that proxy's IP.
> If you access it over plain HTTP on the LAN, set `COOKIE_SECURE=false`, otherwise the browser will not store the sign-in cookie.

The container runs as an unprivileged user with a read-only file system, `cap_drop: ALL`, `no-new-privileges`; data lives in the
`/data` volume (permissions 600/700).

## 3. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | auto-generated in `DATA_DIR/.secret_key` | Key for signing cookies and encrypting AI API keys, ≥ 32 characters (required for SaaS); the app refuses to start with the sample value |
| `SECRET_KEY_OLD` | empty | Previous key when rotating `SECRET_KEY` (stored AI API keys can still be decrypted) |
| `SETUP_TOKEN` | auto-generated, printed to the log | Code required by the first-run `/setup` page |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` (compose: `172.16.0.0/12`) | Proxies whose `X-Forwarded-*` headers are trusted (real client IP for brute-force protection and the audit log) |
| `SESSION_HOURS` | `12` | Sign-in session lifetime |
| `SESSION_IDLE_MINUTES` | `120` | Automatic sign-out after inactivity |
| `ALLOW_SIGNUP` | `false` | `true`: anyone can sign up for an account + company (SaaS) |
| `SIGNUP_PER_IP_HOUR`, `MAX_COMPANIES_PER_USER` | `5`, `0` | SaaS: sign-ups per IP per hour, number of companies each user may create (0 = unlimited) |
| `BACKUP_PASSPHRASE` | empty | Encrypts backup/export files (scrypt + Fernet) |
| `PRODUCT_NAME` | `VFAS` | Name shown in the UI |
| `DATA_DIR` | `/data` (Docker) | SQLite DB, attachments, temporary import files |
| `DATABASE_URL` | empty = SQLite | PostgreSQL: `postgresql+psycopg://user:pass@host:5432/db` |
| `WEB_CONCURRENCY` | `1` | Number of web processes when using PostgreSQL (always 1 for SQLite) |
| `PUBLIC_URL` | empty | Public address, used in invitation links |
| `COOKIE_SECURE` | `false` | `true` when running behind HTTPS (cookies also switch to Secure automatically when the request arrives over https) |
| `MAX_UPLOAD_MB` | `10` | Maximum upload size |
| `TIMEZONE` | `Asia/Ho_Chi_Minh` | Time zone |
| `AI_DEFAULT_BASE_URL`, `AI_DEFAULT_API_KEY`, `AI_DEFAULT_MODEL` | empty | Default AI configuration for companies that have not configured their own |
| `AI_DEFAULT_DAILY_LIMIT` | `100` | Requests per user per day when using the platform's default key (the key is only sent to `AI_DEFAULT_BASE_URL`) |
| `AI_ALLOW_PRIVATE_URLS` | `0` | `1`: lets the assistant call AI servers on the internal network when `ALLOW_SIGNUP=true` |
| `DEBUG_UI` | `false` | Opens the `/ui-kit` UI sample page to everyone (admins only by default) |

## 4. PostgreSQL

Recommended for SaaS. Uncomment `DATABASE_URL` in `docker-compose.yml`, set `POSTGRES_PASSWORD` (required) and
`WEB_CONCURRENCY=2` in `.env`:

```bash
docker compose --profile postgres up -d --build
```

The schema (including the triggers that prevent editing/deleting posted vouchers) is created/updated automatically when the container starts (Alembic).
Posting is serialized per company with a row lock, so running several processes is safe.

## 5. Upgrading

```bash
docker compose exec vfas python -m app.cli backup -o /backups/truoc-cap-nhat.tar.gz
git pull && docker compose up -d --build      # migrations run automatically on startup
```

Read the [CHANGELOG](../CHANGELOG.md) before upgrading for changes that need attention.

## 6. Backup, restore, data export

```bash
docker compose exec vfas python -m app.cli backup -o /backups/vfas-$(date +%F).tar.gz
docker compose exec vfas python -m app.cli restore /backups/vfas-2026-10-14.tar.gz --yes   # OVERWRITES data, stop the app first
docker compose exec vfas python -m app.cli export-company 3 -o /backups/cong-ty-3.tar.gz    # one company (JSON + attachments)
```

- A backup contains the whole DB (SQLite: a consistent copy via the backup API; any DB: `data.json`) and the attachments, written with
  permissions 600.
- Set `BACKUP_PASSPHRASE` to encrypt it (`.tar.gz.enc` file; restoring needs the same passphrase – lose the passphrase and you lose the backup).
- A single-company export contains no passwords, API tokens or AI API keys.
- Also back up `SECRET_KEY` (or the `/data/.secret_key` file): without the key, stored AI API keys cannot be decrypted.

Daily backup with cron on the server:

```cron
0 2 * * * cd /opt/vfas && docker compose exec -T vfas python -m app.cli backup -o /backups/vfas-$(date +\%F).tar.gz
```

## 7. Admin commands

Run inside the container (`docker compose exec vfas …`) or in the venv when running from source.

| Command | Purpose |
|---|---|
| `python -m app.cli migrate` | Create/update the schema |
| `python -m app.cli create-admin --email … [--company … --tax-code … --start-date dd/mm/yyyy]` | Create an admin (+ company) |
| `python -m app.cli create-company --owner-email … --name …` | Create a company for a user |
| `python -m app.cli reset-password --email …` | Reset a password |
| `python -m app.cli backup [-o FILE]` / `restore FILE --yes [--json]` | Back up / restore |
| `python -m app.cli export-company ID [-o FILE]` | Export one company's data |
| `python -m app.cli demo` | Create a sample company with one year of transactions (sign in with `demo@example.com` / `Demo@2026`) |
