# PLAN – Cash book (TK 111) under TT133

## 1. Architecture

```
Browser (HTMX + Alpine.js, prebuilt Tailwind)
        │  HTML (Jinja2) / HTMX fragments
        ▼
FastAPI (1 uvicorn process)  ── app/features/<feature>/{router,service,models,schemas,templates}
        │                      app/core/  (config, db, security, i18n, money, audit, pdf, regimes)
        ▼
SQLAlchemy 2.x ──► SQLite (default, WAL) | PostgreSQL (DATABASE_URL)
Alembic manages the schema + append-only triggers
Attachments: DATA_DIR/attachments/
```

- Fully server-side rendered; HTMX for list filtering, payer/payee suggestions, live amount-in-words.
- No external services: htmx/alpine/CSS are vendored in `app/static`.
- Printing: dedicated HTML templates (always in Vietnamese) → WeasyPrint → PDF. Excel: openpyxl, the Balance column is a formula.
- Book templates are tied to the **accounting regime** via the `app/core/regimes.py` registry (TT133 is the only regime in the first release; TT99/TT58 are added with a new entry + templates).
- Designed for extension: a `funds` table – the first release only has fund `1111 Tiền Việt Nam` (Vietnamese dong cash), and every balance query goes by `fund_id`.

## 2. DB schema

| Table | Main columns | Notes |
|---|---|---|
| `company_settings` | id=1, name, name_en, short_name, tax_code, address, regime, director_name, chief_accountant_name, cashier_name, bookkeeper_name, receipt_prefix, payment_prefix, number_format, number_reset, book_no, default_language, default_theme | 1 row |
| `funds` | id, code (1111), name, currency, open_date, opening_balance BIGINT, lock_version | Opening balance locked once a voucher has been posted |
| `users` | id, username, full_name, password_hash (argon2), role, is_active, language, theme | role: thu_quy, ke_toan, ke_toan_truong, giam_doc (cashier, accountant, chief accountant, director) |
| `user_sessions` | id (token hash), user_id, csrf_token, created_at, expires_at | HttpOnly cookie |
| `accounts` | id, code, name, is_active, is_system, sort_order | contra account catalog |
| `partners` | id, name, address, is_active, last_used_at | payers/payees |
| `vouchers` | id, fund_id, kind (receipt/payment), status (draft/posted/cancelled), number, number_series, number_seq, doc_date, post_date, posting_seq, partner_name, partner_address, reason, amount BIGINT, contra_account, attached_docs, original_doc_ref, note, adjusts_voucher_id, cash_count_id, source (manual/import), created_by, posted_by, posted_at, cancelled_by, cancelled_at, cancel_reason | `UNIQUE(fund_id, number)`, `UNIQUE(fund_id, posting_seq)`; CHECK amount>0 |
| `number_sequences` | fund_id, kind, period_key, last_value | continuous numbering per year |
| `attachments` | id, voucher_id, filename, stored_name, content_type, size | |
| `period_locks` | id, fund_id, year, month (NULL = year lock), is_locked, locked_by/at, unlocked_by/at, unlock_reason | |
| `cash_counts` + `cash_count_lines` | count_at, book_balance, actual_total, members, reason/conclusion; lines: denomination, quantity | 08a-TT cash count report |
| `audit_logs` | id, at, user_id, username, action, entity_type, entity_id, before, after, reason, ip | **append-only**: triggers block UPDATE/DELETE |

DB triggers (SQLite & PostgreSQL):
- `audit_logs`: forbid UPDATE, DELETE.
- `vouchers`: forbid DELETE when status ≠ draft; forbid changing amount/kind/post_date/doc_date/fund_id when the old status ≠ draft.

## 3. Balance rules

- `signed = amount` for a receipt, `-amount` for a payment; only `status = posted` counts.
- Book order: `post_date ASC, posting_seq ASC`. `posting_seq` is assigned at posting (increasing, never reused).
- Balance per line: `opening + SUM(signed) OVER (ORDER BY post_date, posting_seq)`.
- Posting a payment dated D for amount A: the new line comes after every line with `post_date ≤ D`. Check `balance(D) − A ≥ 0` and that every line with `post_date > D` has `balance − A ≥ 0`. Report the date + number of the first line that would go negative.
- Cancelling a receipt: every line after it must still have `balance − A ≥ 0`.
- Sequential period locks: a period can only be locked when all earlier periods are locked; a period can only be unlocked when no later period is locked. Back-dated postings can therefore only affect open periods.
- Posting/cancellation is serialized with a row lock on `funds` (UPDATE lock_version) + a process lock.

## 4. Roles

| Permission | Cashier | Accountant | Chief accountant / Director |
|---|---|---|---|
| Create/edit/delete drafts, post, view S04a, cash count, summaries | ✓ | ✓ | ✓ |
| Enter/edit contra accounts, edit the description of posted vouchers, S04b, cancel, record adjustments, export files, catalogs, audit log | | ✓ | ✓ |
| Lock/unlock periods, users, settings, import | | | ✓ |

## 5. Screens (routes)

| Screen | Route |
|---|---|
| First-run setup / sign-in | `/setup`, `/login`, `/logout` |
| Dashboard | `/` |
| Cash receipts/payments | `/vouchers`, `/vouchers/new?kind=`, `/vouchers/{id}`, `/vouchers/{id}/edit`, `/vouchers/{id}/post`, `/cancel`, `/delete`, `/print.pdf`, `/attachments` |
| S04a / S04b | `/ledger/s04a`, `/ledger/s04b` (+ `.xlsx`, `.pdf`) |
| Summaries | `/reports` |
| Cash count | `/cashcounts`, `/cashcounts/new`, `/cashcounts/{id}`, `/cashcounts/{id}/print.pdf` |
| Period locks | `/locks`, `/locks/year/{year}` |
| Data checks | `/checks` |
| Catalogs | `/accounts`, `/partners` |
| Settings, users, profile | `/settings`, `/users`, `/me` |
| Import | `/import`, `/import/{token}` |
| Audit log | `/audit` |
| About | `/about` |

## 6. Milestones

1. **M1 – Foundation:** scaffold, config, DB + Alembic (triggers), seed TT133 accounts, auth (argon2, sessions, CSRF), roles, layout (theme, vi/en i18n), Settings.
2. **M2 – Vouchers & cash book:** cash receipts/payments (draft → posted → cancelled, adjustments), numbering, retroactive negative-cash check, audit log, S04a/S04b on the web, section 8 tests.
3. **M3 – Printing & export:** PDF S04a, S04b, 01-TT, 02-TT; Excel with formulas; amounts in words.
4. **M4 – Summaries, period locks, cash count:** dashboard, summaries by year/month/account, sequential period locks, cash count + 08a-TT, data checks.
5. **M5 – Import & packaging:** Excel import with preview (1 transaction), Docker/compose, backup/restore CLI, README.
