# PROGRESS (v2)

Progress of v1 (cash book): `docs/v1/PROGRESS.md`.

**Final test results (14/10/2026, after the security review and the VFAS rename):**
- 280 tests (unit, business, HTTP, integration, security) pass on SQLite and on PostgreSQL 16.
- 22 end-to-end tests in real Chrome (Playwright) pass in about 90 seconds (`make e2e`).
- The Docker image runs in hardened mode (read-only, `cap_drop: ALL`, unprivileged user): migrations, `/healthz`, demo
  data, sign-in, PDF printing; about 140 MB RAM with the demo data.
- GitHub Actions CI (`.github/workflows/ci.yml`): ruff, pytest on SQLite + PostgreSQL, E2E, image build and smoke run.
- Release workflow (`.github/workflows/docker-publish.yml`): multi-arch image on GHCR, smoke-tested, SBOM/provenance, cosign signature.

**Security review 14/10/2026:** 4 areas (authentication/sessions, authorization/multi-company, input/files, AI/secrets/deployment);
every High/Medium finding has been fixed with tests (`tests/core/test_security.py`, `tests/security/`). Details:
`docs/SECURITY-GUIDE.md`.

## Test layers

| Layer | Where | What it checks |
|---|---|---|
| Unit / business | `tests/<module>` | Calculations and rules: balances, retroactive negative checks, tax, depreciation, cost of goods sold, payroll, tax return line items, financial statements |
| HTTP (server) | `tests/<module>/test_web*.py`, `tests/core/test_web.py` | Routes, role permissions, submitted forms, page rendering (vi/en), PDF/Excel downloads |
| Integration | `tests/integration` | One year of transactions through the real services → every cross-check; scan of every route with an id for cross-company data leaks (with positive controls); 6 concurrent posting flows never drive cash negative |
| End-to-end | `tests/e2e` (enabled with `E2E=1`) | Real browser: JavaScript (entry grid, invoice grid, combobox, Ctrl+K, shortcuts), XML/CSV upload, PDF/Excel download, AI assistant via a fake LLM (SSE + tools), API token + OpenAI endpoint, inviting members, roles, 390px phone, dark mode, English; FAILS automatically on any JS error or HTTP ≥ 500 |
| Static | `tests/core/test_ui_assets.py`, `test_i18n.py`, `test_migrations.py` | Icons exist, templates parse, no missing translations, migrations match the models |

| Step | Content | Status |
|---|---|---|
| A1 | Plan `docs/PLAN.md`, contracts `docs/CONTRACTS.md`, module specs `docs/MODULES.md`, decisions `docs/DECISIONS.md` | ✅ |
| A2 | Core: multi-company (2 filtering layers), users/sessions/API tokens, per-company roles, module registry, double-entry engine, numbering, period locks, opening balances, immutability triggers, append-only audit log, catalogs, core pages | ✅ |
| A3 | Design system: Tailwind v4 + self-hosted Inter, page shell, ~60 macros, Alpine components (entry grid, invoice grid, combobox, Ctrl+K, AI chat), SVG charts, `/ui-kit`, `docs/UI.md` | ✅ |
| B-A | General ledger, financial statements (S03a, S03b, detailed ledgers, F01, B01a, B02, B03, B09), general journal vouchers, opening balances, closing entries, period locks | ✅ |
| B-B | Cash (01-TT, 02-TT, S04a, S04b, 08a-TT, Excel import), bank (BC/BN/CK, S05, payment orders, statements, reconciliation) | ✅ |
| B-C | Sales and purchases, XML invoices, VAT (purchase/sales listings, 01/GTGT + XML, 8% appendix), CIT, filing calendar, receivables/payables, items | ✅ |
| B-D | Inventory: receipts/issues/transfers, periodic weighted average, negative-stock blocking, stock cards, stock movement summary | ✅ |
| B-E | Fixed assets, tools and supplies/242, payroll (insurance, PIT with effective-dated parameters), salary payment | ✅ |
| B-F | AI assistant (OpenAI Chat Completions + tools, SSE), `/api/v1` API, OpenAI-compatible endpoint, API tokens | ✅ |
| C | Integration: baseline migration + triggers, complete translations, dashboard, demo data, golden tests, PostgreSQL, Docker, README | ✅ |

## Tests per module

| Directory | Main content |
|---|---|
| `tests/core` | Engine (numbering, retroactive negative check, cancellation, adjustment, period locks, triggers), tenancy, permissions/API scopes, web flows (setup, sign-up, invitations, company switching), dashboard, migrations match models, icons, templates, translations |
| `tests/gl` | One-year data set: F01 balances, B01a 200 = 500, B02 60 = closing entries, B03 70 = closing cash, PDF/Excel |
| `tests/cash`, `tests/bank` | v1 section 8 scenarios via cash receipts/payments, cash book, Excel with formulas, cash count, Excel import, BC/BN/CK, statements + matching |
| `tests/sales`, `purchases`, `vat`, `debts`, `items` | Sales/purchase entries, invoice XML (including XXE blocking), 01/GTGT computed by hand for 0/5/8/10/KCT (not subject to VAT), carrying [43] → [22], debt aging |
| `tests/inventory` | Weighted average computed by hand, retroactive negative stock, recalculation on back-dating, purchase → sale → real COGS |
| `tests/assets`, `tests/payroll` | Depreciation, disposals, allocation, payroll 2025 vs 2026, insurance caps, payroll confidentiality |
| `tests/ai`, `tests/api` | Fake LLM: tool loop, permissions, drafts only, SSE; API 401/429, scopes, isolation, OpenAI format |
| `tests/integration` | One year of demo data through the real services: every cross-check + 57 pages rendered; cross-company leak scan; concurrent posting |
| `tests/e2e` | 22 user journeys in real Chrome (see the "Test layers" table) |

## Bugs found during integration (fixed)
- Opening balances: SQLite and PostgreSQL gave different results for periods starting before the book opening date → the opening line carries a "before all periods" date (D-v2-04).
- Chart divided by zero when a data series was flat (new company).
- v1 translations in the core catalog overrode module translations → the module wins on duplicate keys.
- A page variable with the same name as a shell variable (`companies`) broke the company switcher → the page variable wins; the admin page variable was renamed.
- Payroll page returned 500 (non-existent Jinja filter `zip`) once posted and with a bank account present.
- The `demo` command did not run migrations on a fresh DB.
- Swagger `/api/docs` was blocked by the CSP → a dedicated CSP for that page; `'unsafe-inline'` removed from the app-wide `script-src`.
- Excel cash book import changed voucher status by itself → now uses the engine's `post_batch`.

## Bugs that only showed up in a real browser (fixed, with regression tests)
- **Flash messages never appeared**: the toast event fired before the notification area was ready → every success/error message
  after a page navigation was lost (e.g. "phải nhập lý do" (a reason is required), "kỳ đã khóa" (period is locked)). Fix: a toast queue in `core.js`/`shell.js`.
- **Enter in a grid combobox added a spurious line** (entry grid, invoice grid, inventory notes), so the form could not be saved.
  Fix: correctly identify the grid root in `forms.js`.
- **A failed "Lưu và ghi sổ" (Save and post) left only a draft + a fleeting message**: the form is now shown again (422) with a persistent error,
  the entered data kept and the partner field highlighted.
- The `export-company` command failed when the company had an AI configuration (shadowed variable name) – caught by a new CLI test.

## Still open
- The 01/GTGT return XML has not been checked against an official XSD (must be opened in HTKK); XML for financial statements, 03/TNDN, 05/QTT-TNCN not done yet.
- Codes of some book templates (inventory, fixed assets, payroll) follow advisory sources and need checking against the original TT133 text.
- Issuing e-invoices, bank API connections, foreign currency, product costing, TT99/TT58, manual receivable/payable matching.
- The general journal voucher grid uses a partial of its own in the gl module (the shared `line_grid` macro has no debit/credit partner columns yet).
