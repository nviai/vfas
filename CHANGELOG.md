# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Email over SMTP (`SMTP_*`, `MAIL_FROM`): invitation emails with a "Resend" action, password reset ("Quên mật khẩu?")
  and email verification for self sign-up (`EMAIL_VERIFICATION=auto`); `python -m app.cli send-test-email`.
- Prebuilt Docker image `ghcr.io/nviai/vfas` (linux/amd64 + linux/arm64) published by the "Docker image" workflow on
  release tags (`X.Y.Z`, `X.Y`, `X`, `latest`) and on `main` (`edge`), with SBOM/provenance and a cosign signature;
  `docker-compose.yml` pulls it by default (`VFAS_VERSION`).
- E-invoice issuing (module `einvoice`): Viettel S-Invoice, MISA meInvoice, VNPT Invoice and BKAV eHoadon adapters
  (HSM signing), decrease adjustment invoices from sales returns (Viettel, BKAV), idempotent outbox with lookup after
  timeouts, polling of the tax authority code (`python -m app.cli einvoice-poll`), signed XML kept with its SHA-256,
  issued vouchers locked against edit/delete/cancel; a simulator for demos (`EINVOICE_SIMULATOR=true`).
- Accounting regimes per company (`app/core/regimes.py`): **Circular 99/2025** (full chart, B01-DN with
  short/long-term presentation settings, B02-DN, B03-DN, B09-DN, trial balance S06-DN, ledgers coded -DN) and
  **Circular 58/2026** for micro enterprises (B01-DNSN, B02-DNSN); chosen when creating a company and changeable in
  Settings while the books are empty. Default accounts of payroll, fixed assets, purchases, cash purposes and CIT
  follow the regime; printed and Excel forms carry the regime's form codes and "Kèm theo Thông tư…" line.
- XML for HTKK / the e-tax portal: annual financial statements under TT133 (form 683: B01a, B02, B03, F01), CIT
  finalization 03/TNDN (form 892 v2.9.4 with appendix 03-1A) and PIT finalization 05/QTT-TNCN (form 953 v2.9.3 with
  appendices 05-1 and 05-2). All XML returns validate against the schemas shipped with HTKK 5.6.4.
- PIT withholding return **05/KK-TNCN** (Thuế → Tờ khai 05/KK-TNCN): built from posted payroll runs, quarterly from
  period 04/2026 (Resolution 66.16/2026/NQ-CP, Circular 89/2026/TT-BTC Article 22) and following the VAT period
  before; per-employee details, finalize/reopen, PDF and HTKK XML (form 864 v2.9.3, the layout HTKK 5.8.0 still
  files). The filing calendar reminds it quarterly and links to the return.
- Supplementary returns (**01/KHBS**, **01-1/KHBS**) for 01/GTGT and 05/KK-TNCN: once a return is finalized, the
  return page lists the differences between the books and the latest filed version and creates "Bổ sung lần thứ n"
  with a reason; late-payment interest at 0.03%/day on an increase (Law 108/2025/QH15 Art. 16); PDF with the
  explanation page and HTKK XML (`loaiTKhai` B, appendices `PL01_KHBS` + `PL01_1_KHBS`, validated against the KHBS
  schemas). The latest supplement can be deleted; an original with supplements cannot be reopened.
- CIT loss carry-forward (**03-2/TNDN**): the worksheet allocates losses of the previous 5 years oldest first (Law
  67/2025/QH15 Art. 16, Decree 320/2025/ND-CP Art. 7), taking them from earlier finalized worksheets or from a table
  entered by hand; [C3] is computed and the 03/TNDN XML carries appendix 03-2/TNDN.
- Dependants of employees (name, birth date, tax code or ID document, relationship, deduction months) on the employee
  page; the 05/QTT-TNCN XML now includes appendix **05-3/BK-QTT-TNCN**.

### Fixed
- The 01/GTGT XML now validates against the HTKK schema: version 2.8.3, the required business-line/sub-item head
  elements, and no empty signature element.

### Upgrade notes
- Migration `0004` adds `users.email_verified_at`; existing accounts are marked as verified. Migration `0005` adds the
  e-invoice tables. Migration `0006` adds `employee_dependents`.
- `ALLOW_SIGNUP=true` without SMTP now logs a warning at startup: configure SMTP before opening public sign-up.
- `docker-compose.yml` now uses the image `ghcr.io/nviai/vfas:${VFAS_VERSION:-latest}` instead of a local `vfas:latest`
  build; add `--build` to keep building from the source.

## [2.0.0] – Unreleased

First full accounting release, published as open source under the name **VFAS – VietNam Free Accounting Software**.

### Added
- Multi-company accounting under TT133: general ledger, financial statements B01a/B02/B03/B09-DNN, cash, bank, sales,
  purchases, e-invoice XML import, VAT (01/GTGT with XML in the HTKK layout), receivables/payables, inventory, fixed
  assets and prepaid expenses, payroll.
- AI assistant on the OpenAI Chat Completions protocol (tools, SSE), REST API `/api/v1` and OpenAI-compatible
  endpoints.
- New UI (Jinja2 + HTMX + Alpine.js + Tailwind CSS v4): light/dark, mobile, Vietnamese/English, Ctrl+K.
- Backup/restore, single-company export, backup encryption with `BACKUP_PASSPHRASE`.
- Test suite: unit, business rules, web, integration (cross-company isolation, concurrent posting) on SQLite and
  PostgreSQL, Playwright end-to-end tests.
- Open-source project files: MIT license (copyright NVIAI CO., LTD.), contributing guide, code of conduct,
  security policy, issue/PR templates, CI.
- Code style: PEP 8 enforced by ruff (79 columns), English-only comments and docs checked by
  `tools/check_english.py`; Vietnamese README in `README.vi.md`.

### Security
Review of 2026-10-14, details in [docs/SECURITY-GUIDE.md](docs/SECURITY-GUIDE.md):
- Brute-force throttling stored in the database; `X-Forwarded-For` trusted only from proxies in
  `FORWARDED_ALLOW_IPS`.
- The `/setup` page requires a setup code; sign-up and company creation are rate limited; idle sessions expire.
- Fixed stored XSS in the warehouse list, formula injection in exported Excel files, DoS through voucher number
  formats.
- Payroll data hidden from users without payroll permission (ledgers, audit log, Ctrl+K, AI assistant).
- The platform's AI API key is only sent to `AI_DEFAULT_BASE_URL`; SSRF to non-public addresses is blocked.
- Container: read-only filesystem, all capabilities dropped, base images pinned by digest.

### Upgrade notes (from the "NVIAI Kế toán" preview)
- Cookie and API token prefixes changed from `nvk_` to `vfas_`: everyone has to sign in again and **existing API
  tokens must be recreated**.
- The OpenAI-style endpoint model is now `vfas-agent` (was `ketoan-agent`).
- Exceptions were renamed to PEP 8 names (`NotFound` → `NotFoundError`, `PermissionDenied` → `PermissionDeniedError`,
  `LoginRequired` → `LoginRequiredError`, `CompanyRequired` → `CompanyRequiredError`,
  `AiUnavailable` → `AiUnavailableError`) – relevant only for code built on top of VFAS.
- Docker Compose renamed the service `ketoan` → `vfas`, the volumes `ketoan-data`/`ketoan-pg` → `vfas-data`/`vfas-pg`,
  and the PostgreSQL database and user `ketoan` → `vfas`. If you already deployed the old version, **keep the old
  volume** so no data is lost, for example:
  ```yaml
  volumes:
    vfas-data:
      name: <project-dir>_ketoan-data   # check with: docker volume ls
  ```
  and keep `POSTGRES_DB`/`POSTGRES_USER`/`DATABASE_URL` as before.
- Encrypted backups and stored AI API keys remain readable (formats unchanged).

## [1.0] – 2026-10-13

Cash book release (documentation in [docs/v1/](docs/v1/)).
