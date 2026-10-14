# Security guide – model, review results, secure deployment

> Reporting vulnerabilities: see [SECURITY.md](../SECURITY.md) (do not open a public issue).

Security review of 14/10/2026 covering 4 areas (authentication/sessions; authorization/multi-company; input data/files; AI, secrets,
deployment). Each finding was reproduced with TestClient/Chrome where possible and fixed with a regression test
(`tests/core/test_security.py`, `tests/security/`, `tests/integration/test_isolation_concurrency.py`).

## 1. Layers of protection

| Layer | How |
|---|---|
| Sign-in | argon2id (19 MiB, t=2, p=1 – old hashes upgraded automatically), at most 4 concurrent hash operations; a dummy password comparison when the email does not exist (no email enumeration via timing); common passwords and passwords resembling the email are rejected, 8–128 characters |
| Brute-force protection | Counted in the DB (correct with multiple workers): 8 failures/email+IP/15 minutes, 30/IP/15 minutes, 50/email/hour; also applied to the "current password" field; the IP is taken from the actual connection – `X-Forwarded-For` is trusted only when coming from a proxy in `FORWARDED_ALLOW_IPS` |
| Sessions | 256-bit tokens, the DB stores only the sha256; HttpOnly + SameSite=Lax cookies, Secure switched on automatically under HTTPS; absolute expiry after 12 hours + idle expiry after 120 minutes; changing the password/locking the user revokes sessions; other sessions can be viewed and signed out under "Tài khoản của tôi" (My account) |
| CSRF | Per-session token for every POST (forms, HTMX, multipart); the token changes when switching companies (a form opened under the old company cannot write into the new one) |
| First-run setup | `/setup` requires a setup code (auto-generated, printed to the log, stored in `DATA_DIR/.setup_token`) – strangers cannot take over a fresh server |
| Sign-up (SaaS) | Limits on sign-ups per IP per hour, maximum companies per user, company creations per hour; with SMTP configured, new accounts must verify their email (signed link, valid 3 days) before they can open a company |
| E-invoice providers | Account passwords/tokens encrypted with `SECRET_KEY` (Fernet), never shown again or exported; a custom API address must be https and, on SaaS installs, resolve to public Internet addresses (checked when saved and before each call – SSRF); provider answers parsed with `defusedxml`; ZIP downloads limited in depth and size; issuing needs the `einvoice.issue` permission (API tokens cannot issue); every attempt is in the audit log |
| Password reset | Only with SMTP; signed link valid 60 minutes that embeds a fingerprint of the current password hash (single use, invalidated by any password change); the same answer whether or not the email has an account; 3 requests/email/hour and 10/IP/hour; every session is signed out after the reset |
| Multi-company | Every query filters on `company_id` + the session's automatic filtering layer; a test scans every route with an id for leaks of other companies' data (with positive controls); IDs referenced in forms are checked to belong to the company |
| Authorization | 5 per-company roles, per-module permissions; payroll data (per-employee detailed ledgers, salary change log, employees' personal data, Ctrl+K) is shown only to users with payroll permission |
| Data immutability | DB triggers block editing/deleting posted vouchers and the audit log (append-only) |
| API | Per-company Bearer tokens (only for `/api/`), scopes read / write_draft – no posting, no administration; expire after 90 days by default; 60 requests/minute/token counted in the DB; validation errors do not echo the submitted data |
| AI assistant | Tools filtered by the asking user's permissions, write tools only create drafts, audit log entries `via=ai`; API keys encrypted (Fernet, key rotation via `SECRET_KEY_OLD`); the platform's default key is only sent to the exact `AI_DEFAULT_BASE_URL` within the platform's quota; SaaS blocks non-public addresses (including CGNAT, metadata) and re-checks before every call; limits of 180 seconds/2 MB per request, 3 concurrent requests per company, quota reserved before the call; no DB connection held while waiting for the AI; payroll data not sent to the AI by default |
| Files, XML | defusedxml (blocks XXE, billion laughs); ZIP limits on file count/size, never extracted to disk; attachments identified by magic bytes; random per-company storage names; exported Excel files contain no formulas from user data; limits on import row counts; PDFs only load internal resources |
| Browser | CSP `script-src 'self' 'unsafe-eval'` (Alpine), no inline scripts; `frame-ancestors 'self'`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, COOP; HSTS under HTTPS |
| Secrets | Refuses to start with the sample `SECRET_KEY`; SaaS requires ≥ 32 characters; the auto-generated key file is created atomically with permissions 600; invitation, password-reset and verification tokens and setup codes are masked in the access log |
| Backup | Files with permissions 600; scrypt + Fernet encryption when `BACKUP_PASSPHRASE` is set; single-company exports contain no passwords, API tokens, AI API keys or e-invoice provider passwords |
| Container | Non-root user, read-only file system, `cap_drop: ALL`, `no-new-privileges`, `umask 077` (DB, attachments and keys readable only by the app); base image pinned by digest; Tailwind CLI verified by sha256 |

## 2. Review results

Severity: H = High, M = Medium, L = Low, I = Info. Status: ✅ fixed (with test), 📘 handled by configuration/documentation, ⏳ outstanding.

| ID | Severity | Issue | Status |
|---|---|---|---|
| A1/D-4 | H | Spoofed `X-Forwarded-For` bypasses brute-force protection, can lock other people's accounts, wrong IP in the audit log | ✅ IP from trusted proxies + limits per IP and per email |
| A2 | H | Strangers can take over `/setup` on a fresh server | ✅ mandatory setup code, lock against concurrent runs |
| C-1 | H | Stored XSS bypassing the CSP on the inventory catalog page | ✅ |
| D-1 | H | The platform's AI API key sent to a URL set by a tenant | ✅ |
| A3 | M | Read-only API tokens can write via web routes | ✅ Bearer only for `/api/` |
| A4/D-6 | M | Mass sign-up, no email verification | ✅ rate limits + email verification and password reset over SMTP |
| A5 | M | argon2 at 64 MiB per hash can exhaust RAM | ✅ |
| A6/D-14 | M | Invitation link in the URL/log | ✅ signed cookie + log filtering; invitations sent by email when SMTP is configured |
| A7 | M | Email enumeration via response timing | ✅ |
| A8 | M | Cookies not Secure by default | ✅ switched on automatically under HTTPS |
| B-01/D-5 | M | Payroll leaked via the 334 detailed ledger, receivables/payables, AI, audit log | ✅ |
| B-02 | M | Ctrl+K reveals vouchers outside the user's permissions | ✅ |
| B-03 | M | A form from company A writes into company B after switching | ✅ CSRF token changes on switch |
| C-2 | M | Malicious Excel formulas in exported files | ✅ |
| C-3 | M | Voucher number format string can crash the server | ✅ |
| C-4 | M | Item Excel import O(n²), no row limit | ✅ |
| D-2 | M | SSRF via CGNAT, DNS rebinding, echoing error content | ✅ block every non-public address, re-check before the call, no echo; 📘 SaaS should go through an egress proxy |
| D-3 | M | AI holds the DB connection, no time/size limits | ✅ |
| D-7 | M | Sample/short SECRET_KEY, key file not created atomically | ✅ |
| D-8 | M | Backups not encrypted | ✅ permissions 600 + optional encryption |
| A9 | L | Missing HSTS, Permissions-Policy | ✅ |
| A10 | L | Unlimited guessing of the current password | ✅ |
| A11 | L | Weak password policy | ✅ |
| A12 | L | No idle timeout, sessions not viewable | ✅ |
| A13 | I | 422 errors echo the submitted data | ✅ |
| A14 | I | Superadmin sees company metadata | 📘 privacy policy |
| B-04 | L | Other companies' IDs slip into data (inventory, source documents…) | ✅ |
| B-05 | L | Cashier can edit the description of a posted voucher | ✅ |
| B-06 | L | Audit log shows self-chosen names | ✅ email stored alongside |
| B-07 | L | Employees' personal data exposed to every role | ✅ |
| C-5 | L | 500 errors on malformed XML/numbers | ✅ |
| C-6 | L | Content-Disposition built by string concatenation | ✅ |
| C-7/D-9 | L | Disguised link `/\evil.com`, crash on `\x00` in AI Markdown | ✅ |
| C-8 | L | Temporary import files not cleaned up | ✅ |
| C-9 | I | WeasyPrint loads arbitrary URLs | ✅ internal resources only |
| C-10, C-11 | I | Cancelling an import session does not check the company; LIKE not escaped | ✅ |
| D-10 | L | AI quota bypass with parallel requests | ✅ |
| D-11 | I | API client injects system messages | 📘 no privilege escalation (tools check permissions server-side) |
| D-12 | L | In-memory API rate limit; public `/api/docs` | ✅ counted in the DB; 📘 public API documentation is intentional |
| D-13 | L | Container not hardened, image not pinned | ✅ |
| D-15 | I | Personal data sent to foreign AI providers | ✅ payroll not sent by default; 📘 needs assessment under the Personal Data Protection Law |
| B-08 | I | Module tables outside the ledger protected only by code; SaaS on SQLite | 📘 SaaS uses PostgreSQL |

**Payroll in the cash book:** cash payment vouchers paying individual salaries still appear in the cash book/bank book for users allowed to view
cash (the cashier needs them to pay out). If the "Chỉ xem" (Viewer) role should not see them, pay salaries with a single combined voucher or do not
give this role to outsiders.

**Email (SMTP):** email verification, password reset and invitation emails work once `SMTP_HOST` is set – mandatory before
opening public sign-up (cloud); the app logs a warning at startup when `ALLOW_SIGNUP=true` runs without SMTP. A self-hosted
single-company install does not need it (the admin resets passwords via the CLI and sends invitation links directly).

## 3. Cloud / SaaS deployment (many companies, exposed to the Internet)

Recommended architecture: Traefik (HTTPS, rate limiting) → app (2–4 workers) → dedicated PostgreSQL (no exposed port);
encrypted backups pushed to separate storage; secrets from a secret manager; centralized logging.

**Mandatory before opening sign-up**
1. PostgreSQL `DATABASE_URL`, strong password, SSL if the DB is on another machine; `WEB_CONCURRENCY=2..4`.
2. `SECRET_KEY` ≥ 32 characters, fixed, stored separately (lose the key = lose the stored AI API keys and e-invoice provider passwords); rotate with `SECRET_KEY_OLD`.
3. HTTPS at Traefik; `COOKIE_SECURE=true`; `FORWARDED_ALLOW_IPS` = Traefik's Docker network range (default 172.16.0.0/12).
4. Configure SMTP (`SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `MAIL_FROM`; check with `python -m app.cli send-test-email --to …`)
   **before** `ALLOW_SIGNUP=true` – email verification then turns on automatically; set `SIGNUP_PER_IP_HOUR`, `MAX_COMPANIES_PER_USER`.
   Publish SPF/DKIM/DMARC for the sender domain so verification and reset emails are not marked as spam.
5. Traefik rate-limit + in-flight middleware (labels already in `docker-compose.yml`), body size limit ≥ `MAX_UPLOAD_MB`.
6. AI: do not set `AI_DEFAULT_API_KEY` unless you accept the cost; set `AI_DEFAULT_DAILY_LIMIT`; keep `AI_ALLOW_PRIVATE_URLS=0`;
   preferably add an egress proxy that only allows the AI providers' domains (fully prevents DNS rebinding).
7. Daily backups with `BACKUP_PASSPHRASE`, pushed off the server, quarterly restore drills.
8. Keep the container hardening (`read_only`, `cap_drop`, `no-new-privileges`); scan the image (trivy) at build time;
   update the base image regularly (deliberate digest changes).
9. Privacy policy: platform admins see the list of companies/users but not the books; data processing agreement
   with the AI provider; assessment of cross-border transfer of personal data (Personal Data Protection Law 2025).
10. Monitoring: alerts on spikes in failed sign-ins, 5xx errors, AI costs, disk usage.

## 4. Self-host (one company, 1 GB VPS or a machine on the LAN)

**Mandatory**
1. Do not expose port 8000 to the Internet; access via Traefik/Caddy with HTTPS. If it must be exposed directly: `FORWARDED_ALLOW_IPS=127.0.0.1`.
2. LAN-only use without TLS: set `COOKIE_SECURE=false` and allow access only from machines on a trusted network; prefer a VPN
   (WireGuard/Tailscale) over exposing it.
3. `ALLOW_SIGNUP=false` (default); create the admin with `python -m app.cli create-admin` or via `/setup` with the code from the log.
4. Leave `SECRET_KEY` empty (auto-generated in the volume) or set your own ≥ 32 characters; back up the `/data/.secret_key` file (or the key
   value) together with the backups.
5. Daily backups with `BACKUP_PASSPHRASE`, copied to another machine/external drive.
6. Firewall opens only 80/443 (and key-based SSH); update the image regularly.

**Recommended**
- Self-hosted AI (Ollama) so data never leaves the server; when using an external provider, keep "Cho trợ lý đọc dữ liệu lương" (Let the assistant read payroll data) off.
- Do not hand out the "Chỉ xem" (Viewer) role freely; one account per person (the audit log records who did what).

## 5. Operations and incident response

| Situation | What to do |
|---|---|
| Suspected password leak | Admin locks the account (`/admin` or lock the member) → sessions and API tokens are revoked immediately; `python -m app.cli reset-password` |
| Leaked API token | Revoke it under "Hệ thống → API & tích hợp" (System → API & integrations) |
| Leaked `SECRET_KEY` | Set a new key, put the old one in `SECRET_KEY_OLD`, restart, re-enter AI API keys and e-invoice provider passwords, remove `SECRET_KEY_OLD` |
| Suspected tampering with figures | Check the audit log ("Nhật ký thao tác", cannot be deleted); DB triggers block editing/deleting posted vouchers |
| Data corruption | `python -m app.cli restore <file> --yes` (requires `BACKUP_PASSPHRASE` if the backup is encrypted) |
