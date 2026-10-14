# PRODUCT PLAN – VFAS (v2)

> The original working name was "NVIAI Kế toán"; it was renamed to **VFAS – Vietnam Free Accounting Software** when the source code was opened (D-v2-27).

Online accounting software for Vietnamese enterprises under the accounting regime of Circular 133/2016/TT-BTC (SMEs),
Circular 99/2025/TT-BTC (all enterprises from 2026) or Circular 58/2026/TT-BTC (micro enterprises), with many companies
on one system (SaaS) or self-hosted on a VPS.
Continues from v1 "Cash book" ("Sổ quỹ tiền mặt") (v1 docs: `docs/v1/`).

The product name is a working name and can be changed via the `PRODUCT_NAME` variable. The name "eTax" is not used
because it is the name of the tax authority's (Cục Thuế) e-tax portal.

---

## 1. Goals and target users

| User | Main needs |
|---|---|
| Small and micro businesses doing their own bookkeeping | Fast voucher entry, books and financial statements in the official formats, VAT returns, filing deadline reminders |
| Outsourced accountants (one person serving many companies) | Fast company switching, per-client permissions, bulk e-invoice import |
| Business owners | See cash, receivables/payables and profit/loss on a phone; ask about figures in natural language (AI) |
| Auditors, tax authorities (read-only) | Full data export, an audit log that cannot be deleted |

Product principles:
1. **Compliance first, convenience second.** Every book and report prints in the TT133 format; all figures flow through a single ledger.
2. **No silent changes to figures.** Posted vouchers cannot be edited or deleted; they can only be cancelled (open period) or reversed with an adjusting entry (locked period). The audit log is append-only.
   Meets Article 28 of Circular 99/2025/TT-BTC: chronological audit trail, prevention of unauthorized tampering.
3. **AI is an assistant, not a bookkeeper.** AI only reads figures and creates **draft vouchers**; only authorized users post.
4. **The data belongs to the customer.** Full export (Excel, JSON, backup) at any time; self-hostable.

## 2. Scope

### Phase 1 – Double-entry core
- Full TT133 chart of accounts, level 1 and level 2 (level 3 for 3331, 3338, 4111); detail accounts can be added.
- Multi-line double entries (Debit/Credit), vouchers for other transactions (general journal voucher – PK).
- Opening balances by account, partner and bank account; balance check.
- General journal S03a-DNN, general ledger S03b-DNN, account detail ledger, trial balance F01-DNN.
- Period-end closing entries (511, 515, 711, 632, 635, 642, 811, 821 → 911 → 4212), transfer 4212 → 4211 at the start of the year.
- Financial statements: B01a-DNN (Statement of financial position), B02-DNN (Income statement), B03-DNN (Cash flow statement – direct method), B09-DNN (Notes – template with auto-filled figures).
- Sequential monthly/yearly period locks (from v1), data checks, activity log.

### Phase 2 – Cash, receivables and payables
- Cash (from v1): cash receipt 01-TT, cash payment voucher 02-TT, S04a-DNN, S04b-DNN, cash count 08a-TT, retroactive blocking of negative cash.
- Bank deposits: bank account list, bank receipts/payments (BC/BN), internal transfers, bank book S05-DNN, payment orders ("Ủy nhiệm chi").
- Bank statement import (CSV/XLSX, column mapping saved per bank), automatic reconciliation, draft vouchers from unmatched statement lines (suggested by keyword rules + AI).
- Receivables (131) and payables (331): detailed payment ledger per partner, receivables/payables summary, aging (FIFO), balance confirmation letter.

### Phase 3 – Sales, purchases and tax
- Catalog of goods, materials and services; VAT rates 0/5/8/10%, KCT (not subject to VAT), KKKNT (not required to declare and pay VAT).
- Sales vouchers (BH), purchases of goods/services (MH), discounts/returns (decrease adjustments); paid immediately (cash/bank) or on credit.
- Input/output VAT invoice listings; every VAT line goes through the `vat_records` table (including cash payment vouchers with VAT).
- **E-invoice XML import** (Decree 123/2020/ND-CP, Circular 78/2021/TT-BTC standard – `HDon` file), single file or ZIP → draft purchase/sales vouchers, duplicate prevention.
- VAT return 01/GTGT (credit method, monthly/quarterly, Circular 80/2021/TT-BTC), auto-filled, carrying [43] of the previous period to [22]; export to PDF, Excel and XML in the HTKK structure (validated against the HTKK schema; open it in HTKK before filing – see D-v2-12).
- XML for HTKK of the annual financial statements (TT133 set: B01a, B02, B03, F01), the CIT return 03/TNDN (with 03-1A) and the PIT finalization 05/QTT-TNCN (with 05-1 and 05-2).
- VAT reduction to 8% (Decree 174/2025/ND-CP, until 2026-12-31): rate "8" is declared together with the 10% group and has a tax-reduction appendix.
- Corporate income tax (CIT): finalization return 03/TNDN (main line items, taken from B02-DNN + manual adjustments); rates under Law 67/2025 (15% / 17% / 20% depending on the previous year's revenue).
- Filing deadline calendar for tax returns and financial statements on the dashboard.

### Phase 4 – Inventory, assets, payroll
- **Inventory:** warehouses, goods receipts (from purchases or NK receipt notes), goods issues (from sales or XK issue notes), retroactive blocking of negative stock, periodic (monthly) weighted-average issue cost, stock card, detailed materials and goods ledger, receipts–issues–balance summary.
- **Fixed assets:** asset cards, additions (from purchases or opening balances), monthly straight-line depreciation, disposal, fixed asset register, depreciation calculation and allocation schedule.
- **Tools and supplies / prepaid expenses (242):** additions, allocation over a number of months, allocation schedule.
- **Payroll:** employee records (a partner of type employee), monthly payroll, social, health and unemployment insurance and trade union fee (BHXH/BHYT/BHTN/KPCĐ) at effective-dated rates, progressive personal income tax (PIT) (effective-dated tables: 7 brackets through 2025; 5 brackets + VND 15.5M/6.2M deductions from 2026), payslips, payroll posting, PIT summary.

### Cross-cutting
- **SaaS:** sign-up, multiple companies, member invitations, per-company roles, company switching, platform administration pages.
- **OpenAI-style AI agent** (section 7) and a **public API** with tokens.
- **New UI** (section 9): own design system, light/dark, mobile, keyboard shortcut sheet, Ctrl+K.

### Out of scope for this release (extension points exist)
- Issuing e-invoices (requires a provider accepted by the tax authority): an `EInvoiceProvider` interface exists, no provider implemented yet.
- Direct bank API connections: replaced by statement import.
- Foreign currencies, production costing, construction, contracts, budgets, subscription billing.

---

## 3. Architecture

### Stack
Keep the v1 stack (Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, Jinja2 + HTMX 2 + Alpine 3,
standalone Tailwind v4, openpyxl, WeasyPrint, SQLite/PostgreSQL, Docker). Why Jinja is kept: see D-v2-01.
Added: `httpx` (LLM calls), `cryptography` (API key encryption), `defusedxml` (safe parsing of invoice XML).

### Directory layout
```
app/
  core/                 shared infrastructure
    config.py db.py money.py dates.py errors.py i18n.py web.py pdf.py xlsx.py
    audit.py            append-only audit log (with company_id, via)
    tenancy.py          Ctx, per-company data filtering (do_orm_execute + before_flush)
    security.py         users, sessions, per-company roles, permissions, CSRF, API tokens
    registry.py         registers nav, permissions, quick actions, AI tools, data checks, hooks
    triggers.py         DB triggers (SQLite + PostgreSQL) – shared by migrations and tests
    crypto.py           secret encryption keyed by SECRET_KEY
    charts/tt133.py     TT133 chart of accounts + detail-tracking flags
    templates/ static/  design system
  features/<module>/    router.py service.py models.py schemas.py templates/ locales/en.json
                        nav.py perms.py ai_tools.py checks.py (optional, auto-discovered)
```
Every `nav.py`, `perms.py`, `ai_tools.py`, `checks.py`, `models.py`, `router.py` file in a feature
is loaded automatically – adding a module requires no changes to core.

### Modules
| Module | Contents | Owner |
|---|---|---|
| `org` | companies, members, invitations, sign-up, company switching, platform administration | core |
| `auth` | users, login, sessions, my account, API tokens | core |
| `settings` | company information, signatories, number format, tax filing period, AI | core |
| `accounts` `partners` `items` | catalogs (core models; the items UI belongs to the sales team) | core |
| `journal` | posting engine, numbering, posting, cancellation, adjustment, period locks, balance queries | core |
| `gl` | general journal vouchers, opening balances, S03a, S03b, detail ledgers, F01, financial statements, closing entries, period lock UI | team A |
| `cash` `bank` | cash receipts/payments, cash count, S04a/S04b; bank, statements, reconciliation, S05 | team B |
| `sales` `purchases` `vat` `debts` | sales and purchases, XML invoices, VAT, CIT, receivables/payables | team C |
| `inventory` | warehouses, receipts and issues, costing, stock cards, receipts–issues–balance summary | team D |
| `assets` `payroll` | fixed assets, tools and supplies, payroll, insurance, PIT | team E |
| `ai` `api` | assistant, tools, OpenAI-compatible endpoint, REST API | team F |
| `dashboard` `checks` `audit` `about` `imports` | integration | core |

### Multi-company (tenancy)
- Every business table has `company_id` (`TenantMixin` mixin).
- `Ctx(company_id, user, via, ip)` is passed to every service function.
- Second line of defence: `session.info["company_id"]` → the `do_orm_execute` event automatically adds
  `WHERE company_id = …` to every ORM SELECT; `before_flush` refuses to write objects belonging to another company.
- Platform administrators (superadmin) can **not** automatically see customers' books.

### Serialized posting
`UPDATE companies SET lock_version = lock_version + 1 WHERE id = :cid` at the start of each post/cancel transaction:
PostgreSQL locks the row until commit (serialized per company); SQLite takes a database-wide write lock.
The v1 in-process lock is dropped → multiple workers can run on PostgreSQL. Login brute-force throttling is stored in the DB.

---

## 4. Main data model

| Table | Main columns |
|---|---|
| `companies` | name, short_name, tax_code, address, phone, email, legal_rep, chief_accountant, preparer, cashier, storekeeper, regime=`tt133`, start_date, vat_method (`khau_tru`/`truc_tiep`), vat_period (`month`/`quarter`), tax_office, region (minimum-wage region), number_format, settings JSON, plan, is_active, lock_version |
| `users` | email (login), full_name, password_hash, is_active, is_superadmin, language, theme |
| `memberships` | user_id, company_id, role, is_active |
| `invitations` | company_id, email, role, token_hash, expires_at, accepted_at |
| `user_sessions` | token_hash, user_id, selected company_id, csrf_secret, expires_at |
| `api_tokens` | company_id, user_id, name, prefix, token_hash, scopes, expires_at, revoked_at |
| `audit_logs` | company_id, at, user, action, entity, before/after, reason, ip, via (`web`/`api`/`ai`/`cli`) |
| `accounts` | company_id, code, name, parent_code, level, kind, normal (`debit`/`credit`/`both`), is_postable, partner_mode (`none`/`optional`/`required`), needs_bank, needs_item, no_negative, is_system, is_active |
| `partners` | company_id, code, name, is_customer, is_supplier, is_employee, tax_code, address, phone, email, bank_account, bank_name, payment_terms_days |
| `bank_accounts` | company_id, account_code (1121), number, bank_name, branch, is_active |
| `items` | company_id, code, name, unit, kind (`goods`/`material`/`tool`/`product`/`service`), stock_account, revenue_account, cogs_account, expense_account, vat_rate, sale_price, purchase_price |
| `journal_entries` | company_id, doc_type, number, number_seq, date (accounting date), doc_date, description, status (`draft`/`posted`/`cancelled`), partner_id, contact_name, contact_address, ref_no, attachments_note, source, source_id, posting_seq, created/posted/cancelled by/at, cancel_reason, reverses_id |
| `journal_lines` | entry_id, company_id, line_no, account, debit, credit, description, partner_id, bank_account_id, item_id, asset_id, cash_flow |
| `opening_balances` | company_id, account, partner_id, bank_account_id, debit, credit |
| `number_sequences` | company_id, series, year, last_seq |
| `period_locks` | company_id, year, month, locked_at/by, unlock history via the audit log |
| `vat_records` | company_id, entry_id, direction (`in`/`out`), invoice_template, invoice_series, invoice_no, invoice_date, partner_name, partner_tax_code, goods_amount, vat_rate, vat_amount, deductible, item_desc |
| Module | `cash_counts`, `bank_statements`/`bank_statement_lines`, `sales_docs`/`sales_lines`, `purchase_docs`/`purchase_lines`, `einvoice_imports`, `warehouses`, `stock_moves`, `fixed_assets`, `prepaid_tools`, `employees`, `payroll_runs`/`payroll_lines`, `tax_tables`, `ai_settings`, `ai_conversations`/`ai_messages` |

Money: integer dong (BIGINT). Quantity: `Numeric(18,3)`. Unit price: `Numeric(18,2)` (line amounts rounded to whole dong).

### Single ledger source
`journal.queries.ledger_source(company_id)` = lines of **posted** vouchers ∪ opening balances (dated "before all periods" – D-v2-04).
Every book, report, financial statement and tax return reads from here. Order: `(date, posting_seq, line_no)`.

### DB triggers (`core/triggers.py`)
- `audit_logs`: UPDATE/DELETE forbidden.
- `journal_entries`: once the status is not `draft`, financial fields (date, type, number, posting_seq, company) cannot change;
  only `posted → cancelled` is allowed; deletion forbidden. On `draft → posted`: total debit = total credit > 0.
- `journal_lines`: INSERT/UPDATE/DELETE forbidden if the parent entry is no longer `draft`.

---

## 5. Business rules and default postings

### Posting engine (`app/features/journal/service.py`)
```
create_entry(db, ctx, EntryIn, post=False) / update_draft / delete_draft
post_entry(db, ctx, entry)   assign number, validate, post
cancel_entry(db, ctx, entry, reason)   period still open
reverse_entry(db, ctx, entry, date, reason)   create a draft adjusting entry (Debit/Credit swapped)
balance / turnover / trial_balance / account_ledger / partner_balances
```
Checks on posting: debits = credits; accounts are detail (postable) accounts and active; partner required (131, 331, 141, 334…);
bank account required for 112x; period not locked; date ≥ start date; **no retroactive negative balance** on `no_negative` accounts
(1111, 1112, each 112x bank account) plus the negative-stock hook; hooks registered by modules (`registry.posting_validators`).

Voucher number: `{prefix}{year}-{seq:04d}`, assigned on posting (v1 D10), continuous per type + year. Types: PT (cash receipt),
PC (cash payment), BC (bank receipt), BN (bank payment), PK (general journal voucher), BH (sales), MH (purchase),
TL (returns/discounts), NK (goods receipt), XK (goods issue), KH (depreciation), PB (allocation), LG (payroll),
KC (closing), CK (internal transfer).

### Default postings
| Transaction | Debit | Credit |
|---|---|---|
| Cash / bank receipt | 1111 / 1121 | 131, 511x, 3331, 711, 1388, 3388, 341, 411… |
| Cash / bank payment | 331, 642x, 1331, 334, 3335, 338x, 141, 211… | 1111 / 1121 |
| Bank withdrawal to cash / cash deposit to bank | 1111 / 1121 | 1121 / 1111 |
| Sale | 131 (or 1111/1121) | 511x, 33311 |
| Cost of goods sold (on issue costing) | 632 | 156/155 |
| Purchase of stocked goods / services | 156/152/153, 642x, 211, 242 + 1331 | 331 (or 1111/1121) |
| Sales discounts, sales returns | 511x, 33311 | 131 |
| Depreciation | 6421/6422/154 | 2141/2143 |
| Tools and supplies allocation | 6421/6422 | 242 |
| Payroll | 6421/6422 (salary + employer insurance) ; 334 (employee insurance, PIT) | 334 ; 3382, 3383, 3384, 3385, 3335 |
| Cash count surplus / shortage | 1111 / 1381 | 3381 / 1111 |
| Closing | 511, 515, 711 → 911 ; 911 → 632, 635, 642x, 811, 821 ; profit 911 → 4212 (loss the other way round) | |

### Closing entries and financial statements
- Closing runs per month or per year and creates a KC voucher (can be cancelled in an open period and run again).
- B01a-DNN, B02-DNN: line item ↔ account mappings (debit/credit balances, turnover) declared as data (`gl/statements.py`), with a check
  that total assets = total liabilities and equity.
- B03-DNN direct method: line items by the contra account of cash lines (111x, 112x), overridable via `journal_lines.cash_flow`.

### VAT (01/GTGT – Circular 80/2021/TT-BTC)
[22] = [43] of the previous period; [23]/[24] = value/VAT of deductible purchased goods and services; [25] = deductible [24];
[26] not subject to VAT; [29] 0%; [30]/[31] 5%; [32]/[33] 10% (including 8% under Decree 174/2025/ND-CP, with the reduction appendix);
[27] = [29]+[30]+[32]+[32a]; [28] = [31]+[33]; [34] = [26]+[27]; [35] = [28]; [36] = [35]−[25];
[40a] = [36]−[22]+[37]−[38]−[39a] if ≥ 0; [40] = [40a]−[40b]; [41] if < 0; [43] = [41]−[42].

### Inventory
Periodic weighted average per month, per item + warehouse. Issue notes record the quantity at posting; the month's "Issue costing"
("Tính giá xuất kho") computes the unit cost and creates the cost-of-goods-sold voucher (XK) – it can be cancelled and run again
while the period is open. Negative stock is blocked by quantity, retroactively.

### Fixed assets, tools and supplies
Straight-line depreciation: monthly amount = remaining depreciable cost / remaining months, rounded to the dong, with the
difference absorbed in the final month. Starts in the month after the addition (default; optionally from the addition date – D-v2-15).

### Payroll
Insurance rates (from 2026): employee BHXH 8%, BHYT 1.5%, BHTN 1%; employer BHXH 17.5%, BHYT 3%, BHTN 1%, KPCĐ 2%.
Contribution caps: BHXH/BHYT 20 × base salary ("lương cơ sở"); BHTN 20 × regional minimum wage (Decree 293/2025/ND-CP from 2026-01-01:
I 5,310,000; II 4,730,000; III 4,140,000; IV 3,700,000).
PIT: deductions of VND 15.5M for the taxpayer and 6.2M per dependant (Resolution 110/2025, from 2026-01-01); 5 brackets of
5/10/20/30/35% with thresholds at 10/30/60/100M (Law 109/2025).
All parameters are effective-dated data tables (`tax_tables`), editable, not hard-coded.

---

## 6. Books, reports, print forms

| Form | Name | Module |
|---|---|---|
| 01-TT, 02-TT | Cash receipt ("Phiếu thu"), cash payment voucher ("Phiếu chi") | cash |
| 08a-TT | Cash count report (VND) ("Bảng kiểm kê quỹ") | cash |
| S03a-DNN | General journal ("Sổ Nhật ký chung") | gl |
| S03b-DNN | General ledger ("Sổ Cái") | gl |
| S04a-DNN, S04b-DNN | Cash book ("Sổ quỹ tiền mặt"), detailed cash ledger ("Sổ kế toán chi tiết quỹ tiền mặt") | cash |
| S05-DNN | Bank book ("Sổ tiền gửi ngân hàng") | bank |
| – | Account detail ledger, detailed payment ledgers for customers/suppliers, receivables/payables summary, aging, balance confirmation letter | gl, debts |
| – | Stock card, detailed materials and goods ledger, receipts–issues–balance summary | inventory |
| – | Fixed asset card, fixed asset register, depreciation calculation and allocation schedule, tools and supplies allocation schedule | assets |
| – | Payroll sheet, payslip, insurance summary, PIT summary | payroll |
| F01-DNN | Trial balance ("Bảng cân đối tài khoản") | gl |
| B01a-DNN, B02-DNN, B03-DNN, B09-DNN | Financial statements ("Báo cáo tài chính") | gl |
| 01/GTGT | VAT return ("Tờ khai thuế GTGT") (+ listings, tax-reduction appendix) | vat |
| 03/TNDN | CIT finalization return ("Tờ khai quyết toán thuế TNDN") (main line items) | vat |

Print forms are always in Vietnamese, in the Liberation Serif font, with the header "Đơn vị / Địa chỉ / Mẫu số … (Ban hành theo
Thông tư số 133/2016/TT-BTC ngày 26/8/2016 của Bộ Tài chính)" (Entity / Address / Form no. … (Issued under Circular
133/2016/TT-BTC dated 26/08/2016 of the Ministry of Finance)). Every book and report exports to PDF and Excel (Excel with
formulas where sensible). Form codes for books not in the table above are only printed once verified against the original legal text.

---

## 7. OpenAI-style AI agent and API

### 7.1 The app calls an LLM (any OpenAI-compatible provider)
- **Chat Completions** protocol (`POST {base_url}/chat/completions`, `tools`, `tool_calls`, `stream=true`, SSE) – the common standard
  of OpenAI, Anthropic (OpenAI compatibility layer), OpenRouter, Ollama, vLLM, LM Studio.
- Per-company configuration: preset (OpenAI, Anthropic Claude, Ollama, custom), `base_url`, `api_key` (encrypted with
  `core/crypto.py`), `model`, temperature, daily call limit. Disabled by default until configured.
- Agent loop: system prompt (role of a TT133 accountant, company, today's date, the user's permissions) → LLM → execute
  tools → loop up to 8 rounds → answer. Streamed over SSE to the chat panel.
- **Tools** are registered with `@registry.ai_tool(...)` in each module and described with OpenAI-function-style JSON Schema.
  Each tool declares the permission it needs and `write=True/False`. Write tools **only create drafts** and return a link so the user posts them.
- Planned tools: company information, account balance, trial balance, voucher search, voucher view, cash on hand, receivables/payables
  by partner, aging, revenue/expenses by period, financial statement summary, VAT summary, data checks, account suggestions, draft
  cash receipts/payments, draft general journal vouchers, posting suggestions for statement lines / imported invoices.
- Safety: every tool runs with the asking user's permissions in the right company; data read from the books (partner names,
  descriptions) is treated as data, not instructions; the audit log records `via=ai`; conversations are stored per user; API keys are hidden from the UI.

### 7.2 The app as an OpenAI-compatible "model"
- `GET /api/v1/models`, `POST /api/v1/chat/completions` (streaming and non-streaming): any OpenAI client (SDK, Open WebUI,
  LibreChat, n8n…) that points `base_url` at the system and uses an API token as `api_key` can talk to the company's accounting assistant.
- `GET /api/v1/tools` returns the tool list in exactly OpenAI's `tools` format; `POST /api/v1/tools/{name}` executes a tool
  → external agents (OpenAI Agents SDK, LangChain…) can use the books via function calling.

### 7.3 REST API
`/api/v1/…`: accounts, partners, items, entries (read, create drafts), reports (balances, trial balance), vat. Authentication
`Authorization: Bearer <token>`; per-company tokens, scopes `read` / `write_draft`; no CSRF needed; OpenAPI docs at `/api/docs`.

---

## 8. Security and permissions

Roles are **per company**: `owner` (Business owner/Director), `chief_accountant` (Chief accountant), `accountant` (Accountant),
`cashier` (Cashier), `viewer` (Read-only). Each module declares its permissions in `perms.py` (permission → default roles).

| Permission group | owner | chief accountant | accountant | cashier | viewer |
|---|---|---|---|---|---|
| View books, reports | ✓ | ✓ | ✓ | cash | ✓ |
| Create draft vouchers | ✓ | ✓ | ✓ | receipts/payments | |
| Post, cancel, adjust | ✓ | ✓ | ✓ | receipts/payments (post) | |
| Lock/unlock periods, closing entries, opening balances | ✓ | ✓ | | | |
| Payroll (view/edit) | ✓ | ✓ | ✓ | | |
| Members, company settings, AI, API tokens | ✓ | ✓ (except ownership transfer) | | | |

Kept from v1: argon2, DB-backed sessions (HttpOnly cookie, SameSite=Lax), per-session CSRF, CSP, upload size limits,
login brute-force protection (moved to the DB). Added: API tokens hashed with sha256 and shown once; XML parsed with `defusedxml`;
ZIP limits on file count and uncompressed size.

---

## 9. User interface

- Tailwind v4 design system: color tokens (background, surface, border, text, teal primary, success, warning, danger, info), border radius,
  shadows, spacing; **Inter** font (self-hosted, with a Vietnamese subset); numbers use `tabular-nums`.
- Shell: sidebar grouped by business area (collapsible); top bar with company switcher, search/Ctrl+K, "Tạo mới" (New) button, AI assistant
  button (right-hand drawer, streaming chat) and user menu; on mobile: bottom bar + slide-out menu.
- Components: buttons, status badges, stat cards, tables (sticky header, right-aligned numeric columns, total rows), filters, money inputs
  (shorthand 2tr/500k), account/partner/item comboboxes (search by code or name), voucher line grid (add/remove lines from the keyboard,
  debit/credit totals, imbalance warning), tabs, modal, drawer, toast, empty states, skeleton, print-form preview.
- Sample page `/ui-kit` (only with `DEBUG_UI=1` or for a superadmin) shows every component and 6 page templates: list, voucher form with
  line grid, voucher detail, report, dashboard, login.
- Template contract: see `docs/UI.md` (written by the UI team).

---

## 10. Testing

- Unit: money, amount in words, dates, PIT, insurance, depreciation, average cost, 01/GTGT line items.
- Business rules: keep every scenario from section 8 of v1 (ported to the new engine); add scenarios for each module (post → books → reports).
- **Golden dataset**: one year of sample transactions for a service company (cash, bank, sales and purchases, depreciation, payroll,
  closing); checks that F01 balances, B01a assets = liabilities and equity, B02 profit = 911, B03 closing cash = 111 + 112, 01/GTGT matches the listings.
- Integrity: triggers block edits/deletes; tenancy (a user of company A cannot read/write company B via the web, the API or AI tools).
- AI: fake LLM server (httpx MockTransport) returning `tool_calls` → checks the loop, permissions, drafts only.
- Web smoke: every page renders (vi/en) without errors; run on both SQLite and PostgreSQL (`TEST_DATABASE_URL`).

## 11. Deployment
A single Docker image on a single port; SQLite (default) or PostgreSQL (recommended for SaaS, allows multiple workers: `WEB_CONCURRENCY`).
Full-system backup/restore and **single-company data export** (JSON + attachments). New environment variables: `PRODUCT_NAME`,
`ALLOW_SIGNUP`, `PUBLIC_URL`, `WEB_CONCURRENCY`, `AI_DEFAULT_*`.

## 12. Implementation roadmap

| Step | Content | Who |
|---|---|---|
| A1 | Plan, decisions, module contracts | core |
| A2 | Core: tenancy, auth, org, catalogs, posting engine, registry, triggers, tests | core (in parallel with A3) |
| A3 | Design system, page shell, components, `/ui-kit`, `docs/UI.md` | UI team |
| B | Modules in parallel: A (gl), B (cash/bank), C (sales, purchases, tax, receivables/payables), D (inventory), E (assets/payroll), F (AI/API) | 6 teams |
| C | Integration: base migration, dashboard, navigation, golden data, PostgreSQL, Docker, README, PROGRESS | core |

Each team only edits its own module directory + `tests/<module>/`; core changes it needs are written to `docs/requests/<module>.md`
for the integration step to handle.

## 13. Risks
| Risk | Mitigation |
|---|---|
| Wrong financial statement / tax return line items | Mappings declared as data, golden-data tests, recommend review by a chief accountant before real use |
| Malformed HTKK XML | State clearly "must be checked in HTKK"; separate XML generator for quick fixes |
| Law changes (tax rates, deductions, minimum wage) | Effective-dated parameter tables, editable in the UI |
| Data leaking between companies | Two layers of tenancy filtering + isolation tests for the web, API and AI |
| AI records wrong data | Drafts only, the person who posts is accountable, audit log `via=ai` |
