# PROGRESS

Testing: `make test` (SQLite) and `TEST_DATABASE_URL=postgresql+psycopg://… pytest` (PostgreSQL).
Final result: **146 tests pass on SQLite and 146 tests pass on PostgreSQL 16**.

## M1 – Foundation ✅

- Features-first scaffold, configuration via env (`app/core/config.py`), SQLAlchemy 2 + Alembic (`0001` schema, `0002` append-only / anti-tampering triggers for vouchers).
- Seeded NVIAI company data, fund 1111 with book opening date 12/01/2023, 32 TT133 contra accounts (`app/core/seed.py`).
- argon2 sign-in, DB sessions + HttpOnly cookie, CSRF, brute-force protection; first-run setup screen + `create-admin` command.
- Responsive layout (sidebar / phone menu / fixed Receipt–Payment button bar), light/dark, Vietnamese/English.
- Settings screen (company, signers, voucher number format, lockable opening balance), Users, My account.
- Tests: `test_web_smoke.py`, `test_integrity.py`.

## M2 – Vouchers & cash book ✅

- Cash receipts/payments: draft → posted → cancelled (reason required) / adjustments; editing the description of posted vouchers (accountants); image/PDF attachments.
- Fast entry form: Enter saves a draft, Ctrl+Enter saves and posts, Alt+T/Alt+C, payer/payee suggestions + address autofill, shorthand `500k`/`2tr`, live amount-in-words.
- List filtered by type, status, date, contra account, search by voucher number/description (HTMX, with a card view for phones).
- Automatic continuous numbering; retroactive negative-cash check when posting payments and cancelling receipts; append-only audit log.
- S04a, S04b books on the web: month / year / all / custom range; correct opening balance at the start date.
- Contra account and payer/payee catalogs; audit log page with filters.
- Section 8 tests: `test_business.py` (12 tests) + `test_web_vouchers.py`.

## M3 – Printing & export ✅

- PDF (WeasyPrint): S04a-DNN, S04b-DNN (A4 landscape, repeated header, page numbering, exact "Sổ này có N trang" (This book has N pages)), cash receipt 01-TT / cash payment voucher 02-TT (A5 landscape, amount in words, 5 signature boxes), cash count report 08a-TT.
- Excel (openpyxl): the Balance column is a formula, total rows use SUM, layout matches the import.
- Amounts in words: `app/core/money.py`, `test_money.py` (0, 1.005, 15.000.000, 1.000.000.000, 21, 105, lăm/năm, mốt/một, linh/lẻ…).
- PDFs rendered in Docker checked visually (Liberation Serif font, all diacritics present).
- Tests: `test_exports.py` (Excel reopened, recalculated formulas match the DB; PDF read back with pypdf, a 120-line multi-page book).

## M4 – Summaries, period locks, cash count ✅

- Dashboard: current balance, monthly receipts/payments, month-end balance chart (SVG, with a data table), warnings for drafts / unlocked periods / data checks, recent vouchers.
- Summaries: by year from 2023 to now, by month for the selected year, receipts/payments by contra account.
- Sequential monthly/yearly period locks, unlocking requires a reason; "Khóa sổ năm" (Year-end lock) screen + printing the whole year's book.
- Cash count by denomination from 500,000 down to 1,000, compared with the book balance, suggested vouchers to settle differences (never created automatically), 08a-TT printout.
- Data checks: negative balances, missing contra accounts, gaps/duplicates in voucher numbers, voucher date after posting date, periods unlocked > 30 days, cash count differences.
- Tests: `test_reports.py`, `test_locks.py`, `test_cashcount.py`, `test_checks.py`.

## M5 – Import & packaging ✅

- Excel import with preview, errors/warnings classified, a single transaction, original voucher numbers kept (`test_import.py`, fixture `tests/fixtures/so_quy_mau.xlsx`).
- Multi-stage Dockerfile (Tailwind standalone CLI → Python 3.12 runtime + Pango + fonts), `docker-compose.yml` (Traefik labels, PostgreSQL profile). Built and smoke-tested: healthz, setup, admin creation, PDF printing in the container; ~70 MB RAM at runtime.
- CLI: `migrate`, `create-admin`, `reset-password`, `backup`, `restore` (`test_cli.py`: back up → corrupt the data → restore, via both the SQLite and the JSON path).
- Vietnamese README: installation, backup, restore, operations.

## Additional checks

- The whole test suite run on PostgreSQL 16 (Docker) – found and fixed a sequence reset bug on restore.
- Demo data with ~170 vouchers: found and fixed a Vietnamese case-matching bug on SQLite (`ulower`).
- Headless Chrome screenshots: desktop, 390px phone, light/dark – fixed horizontal overflow on phones (grid `minmax(0,1fr)`) and cramped ledger tables.
- `test_i18n.py`: every UI string has an English translation; every page renders in English mode.

## Still open / out of scope for the first release

- TT99/2025, TT58/2026, foreign currency 1112, account 112 / S05-DNN book, multiple funds/companies: extension points exist (`regimes.py`, `funds` table) but are not implemented.
- Running multiple workers: the posting lock and the failed-sign-in counter need to move to the DB/Redis.
