<div align="center">

<img src="app/static/favicon.svg" width="72" height="72" alt="VFAS logo">

# VFAS

**Vietnam Free Accounting Software**<br>
Free, open-source accounting for Vietnamese small and medium businesses – Circular 133/2016/TT-BTC (TT133) regime

[![CI](https://github.com/nviai/vfas/actions/workflows/ci.yml/badge.svg)](https://github.com/nviai/vfas/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Code style: PEP 8](https://img.shields.io/badge/code%20style-PEP%208%20(ruff)-261230)](CONTRIBUTING.md#code-style)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLite | PostgreSQL](https://img.shields.io/badge/DB-SQLite%20%7C%20PostgreSQL-4169E1?logo=postgresql&logoColor=white)](docs/DEPLOY.md#4-postgresql)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](CONTRIBUTING.md)

[Features](#features) · [Try it](#try-it-in-2-minutes) · [Deploy](#deploying-for-a-business) ·
[Docs](#documentation) · [Contributing](#contributing) · [Tiếng Việt](README.vi.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/dashboard-dark.png">
  <img src="docs/images/dashboard-light.png" alt="VFAS dashboard with demo data" width="900">
</picture>

</div>

## Why VFAS

- **Free and open** – no license fees, no limits on users or vouchers. The source is public under the MIT license, so
  anyone can check how taxes and reports are calculated – and reuse the code.
- **Your data** – self-host on your own server or run it as SaaS; export all data of a company at any time.
- **Clean books** – posted vouchers cannot be edited or deleted (enforced in the database); mistakes are fixed by a
  reasoned cancellation or an adjusting entry; the audit log is append-only.
- **Statutory coverage** – TT133 ledgers and financial statements, VAT return 01/GTGT, personal income tax and social
  insurance with effective-dated parameters.
- **OpenAI-style AI agent** – ask about your figures in Vietnamese and let the agent draft vouchers; works with Claude,
  OpenAI, OpenRouter or self-hosted Ollama. The agent only drafts – a person with the right role posts.
- **Lightweight** – runs on a 1 GB VPS with SQLite, or on PostgreSQL with several workers for SaaS; one Docker command.

The user interface defaults to Vietnamese and is fully translated into English.

## Screenshots

| Sales voucher | Statement of financial position (B01a-DNN) |
|---|---|
| <img src="docs/images/sales-doc.png" alt="Sales voucher with its journal entries" width="440"> | <img src="docs/images/report-b01a.png" alt="Statement of financial position B01a-DNN" width="440"> |

| Cash receipt on a phone | Dark mode |
|---|---|
| <img src="docs/images/cash-mobile.png" alt="Quick cash receipt form on a phone" width="220"> | <img src="docs/images/dashboard-dark.png" alt="Dashboard in dark mode" width="440"> |

## Features

| Area | What you get |
|---|---|
| **Ledger & reports** | Double-entry journal, opening balances (Excel import), general journal S03a-DNN, general ledger S03b-DNN, account detail ledgers, trial balance F01-DNN, financial statements B01a/B02/B03/B09-DNN, period-end closing entries, monthly/yearly period locks |
| **Cash** | Cash receipts 01-TT and payments 02-TT (quick phone form for cashiers), cash books S04a/S04b-DNN, cash counts 08a-TT, import of legacy cash books from Excel |
| **Bank** | Deposits/withdrawals, internal transfers, bank book S05-DNN, payment orders, CSV/XLSX statement import with auto-matching |
| **Sales & purchases** | Goods and services, sales and purchase vouchers, returns and allowances, **e-invoice XML import** (single files or ZIP) |
| **Tax** | Purchase/sales listings, **01/GTGT** VAT return (PDF, Excel, XML in the HTKK layout), 8% VAT reduction appendix, corporate income tax worksheet, filing calendar |
| **Receivables & payables** | Balances per customer/supplier, payment ledgers, aging, balance confirmation letters |
| **Inventory** | Multiple warehouses, receipts/issues/transfers, periodic weighted-average costing, stock cards, stock summary |
| **Fixed assets** | Asset cards and register, monthly straight-line depreciation, disposals; tools and prepaid expenses with allocation |
| **Payroll** | Employee records, monthly payroll, social/health/unemployment insurance and union fees, personal income tax, payslips, salary payments |
| **AI assistant** | Questions in Vietnamese, **draft** vouchers, tools filtered by the asking user's permissions |
| **API** | REST `/api/v1`, per-company tokens, OpenAI-compatible endpoints (`/api/v1/chat/completions`, `/api/v1/tools`) |
| **Platform** | Multiple companies, 5 roles, member invitations, audit log, data checks, backup/restore (optional encryption) |
| **UI** | Light/dark, mobile, Vietnamese/English, Ctrl+K search everywhere, keyboard shortcuts for data entry |

**Data principles**
- **Never negative:** cash, each bank account and stock never go below zero at any point in time, including back-dated
  postings.
- **Single source of truth:** every ledger, report and tax return reads from the general ledger, with cross-checks
  (balanced trial balance, assets = liabilities + equity, inventory = TK 15x…).
- **Strict access control:** payroll data is visible only to authorized roles; companies on the same server are fully
  isolated.

## Try it in 2 minutes

With Docker:

```bash
git clone https://github.com/nviai/vfas.git && cd vfas
docker build -t vfas .
docker run -d --name vfas -p 127.0.0.1:8000:8000 -v vfas-data:/data vfas
docker exec vfas python -m app.cli demo
```

Open <http://127.0.0.1:8000> and sign in with `demo@example.com` / `Demo@2026`. The demo company contains about one
year of transactions. Clean up with `docker rm -f vfas && docker volume rm vfas-data`.

## Deploying for a business

| | Self-host (one company) | Cloud / SaaS (many companies) |
|---|---|---|
| Server | 1 GB VPS or a LAN machine | 2–4 GB VPS or larger |
| Database | SQLite | PostgreSQL |
| Access | HTTPS via a reverse proxy, or a VPN | HTTPS via Traefik, rate limiting at the proxy |
| Sign-up | Admin invites members | Self sign-up (needs SMTP – on the roadmap) |

Full guide: [docs/DEPLOY.md](docs/DEPLOY.md). Before exposing it to the Internet, follow the checklist in
[docs/SECURITY-GUIDE.md](docs/SECURITY-GUIDE.md).

## Running from source

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). Tailwind CSS uses the standalone CLI – no Node needed.

```bash
make setup && make tailwind && make css     # venv + dependencies, Tailwind CLI, build CSS
make migrate && .venv/bin/python -m app.cli demo
make dev                                    # http://127.0.0.1:8000
make test                                   # pytest: unit, business rules, web, integration
make e2e                                    # end-to-end tests in a real browser (Playwright)
make lint                                   # PEP 8 (ruff) + English-only comments
```

**Architecture:** `app/core` is the infrastructure (multi-tenancy, permissions, registry, posting engine, database
triggers, shared UI). Each business module in `app/features/<module>` declares its own routes, models, permissions,
menu entries, AI tools and data checks, and is loaded automatically.

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 + Alembic · Jinja2 + HTMX + Alpine.js · Tailwind CSS v4 · WeasyPrint ·
openpyxl · SQLite/PostgreSQL.

## Documentation

| Document | Contents |
|---|---|
| [docs/DEPLOY.md](docs/DEPLOY.md) | Installation, environment variables, PostgreSQL, upgrades, backups, admin commands |
| [docs/USAGE.md](docs/USAGE.md) | Roles and permissions, AI assistant, OpenAI-style API, keyboard shortcuts |
| [docs/SECURITY-GUIDE.md](docs/SECURITY-GUIDE.md) | Security model, latest review, cloud and self-host checklists |
| [docs/EINVOICE.md](docs/EINVOICE.md) | Research and design for issuing e-invoices through Vietnamese providers |
| [docs/PLAN.md](docs/PLAN.md) | Product plan and architecture |
| [docs/MODULES.md](docs/MODULES.md) · [docs/CONTRACTS.md](docs/CONTRACTS.md) · [docs/UI.md](docs/UI.md) | Module specs, core–module contracts, design system |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Design decisions and the legal sources they were checked against |
| [docs/PROGRESS.md](docs/PROGRESS.md) | Progress and test layers |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Roadmap

- [ ] Email (SMTP): address verification, password reset, invitation emails – required before opening public sign-up
- [ ] Issue e-invoices through providers (Viettel S-Invoice, MISA meInvoice, VNPT, BKAV) – design in [docs/EINVOICE.md](docs/EINVOICE.md)
- [ ] XML export of financial statements, 03/TNDN and 05/QTT-TNCN for HTKK
- [ ] Accounting regimes under Circular 99/2025 and Circular 58/2026 (extension point `app/core/regimes.py`)
- [ ] Prebuilt Docker image
- [ ] Foreign currencies, bank API connections, production costing

Want to take one? Open an issue to discuss it first.

## Contributing

All contributions are welcome: bug reports, code, translations, documentation – and especially **accountants checking
ledger layouts and posting rules against the legal texts**. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) before opening a pull request. Issues and discussions may be in Vietnamese or
English; code, comments and documentation are in English and Python follows PEP 8.

## Security

Found a vulnerability? **Do not open a public issue** – report it privately as described in [SECURITY.md](SECURITY.md).

## Legal notes

- The software is provided "as is", without warranty (see [LICENSE](LICENSE)). Managers and chief accountants remain
  responsible for the accuracy of the figures (Article 28, Circular 99/2025/TT-BTC); have the chief accountant review
  reports before filing.
- The **01/GTGT XML** follows the common HTKK layout but has not been validated against the official XSD: **open it in
  HTKK before filing**.
- Some ledger form codes (S06/S07/S08/S09/S11-DNN, 02-LĐTL, 06-TSCĐ…) come from advisory sources and still need to be
  checked against the original TT133 text.
- Payroll and tax parameters were checked against public sources in October 2026 and **can be edited** under
  "Lương → Tham số" (Payroll → Parameters) when the law changes.

## License

VFAS is released under the [MIT License](LICENSE). Copyright © 2026 NVIAI CO., LTD.

In short – this does not replace the license text: you may use, copy, modify, merge, publish, distribute, sublicense
and sell VFAS, including in commercial and closed-source products, as long as the copyright notice and the license
text are kept. The software comes without any warranty.

## Acknowledgements

VFAS is built on [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy](https://www.sqlalchemy.org/),
[Alembic](https://alembic.sqlalchemy.org/), [Jinja2](https://jinja.palletsprojects.com/), [HTMX](https://htmx.org/),
[Alpine.js](https://alpinejs.dev/), [Tailwind CSS](https://tailwindcss.com/), [WeasyPrint](https://weasyprint.org/),
[openpyxl](https://openpyxl.readthedocs.io/), [Lucide](https://lucide.dev/) icons (ISC) and the
[Inter](https://rsms.me/inter/) font (SIL OFL).
