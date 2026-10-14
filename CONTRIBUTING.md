# Contributing to VFAS

Thank you for your interest in VFAS! Issues and discussions may be written in Vietnamese or English; everything that
goes into the repository follows the [language rules](#language-rules) below.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- **Bug reports** – use the "Bug report" issue form. **Remove real data** (customer names, tax codes, amounts) from
  descriptions, screenshots and attachments.
- **Accounting rules** – check ledger layouts, posting rules and tax parameters against the legal texts. This is the
  most valuable contribution. Always cite the **document number and article** you rely on.
- **Code** – fix bugs or pick an item from the [roadmap](README.md#roadmap). Open an issue to discuss larger changes
  first.
- **Translations** – English UI strings live in `app/core/locales/en.json` and `app/features/<module>/locales/en.json`.
- **Documentation** – guides, tutorials, videos, articles.

Security vulnerabilities: **do not open a public issue** – follow [SECURITY.md](SECURITY.md).

## Language rules

| What | Language |
|---|---|
| `README.md`, everything in `docs/`, `CONTRIBUTING.md`, `CHANGELOG.md`, issue/PR templates | **English** |
| `README.vi.md` (the only Vietnamese document; keep it in sync with `README.md`) | Vietnamese |
| Code comments, docstrings, logging messages, developer-facing exception messages | **English** |
| Identifiers (variables, functions, classes, files), commit messages, PR titles | **English** |
| User-facing text: templates, `_()` strings, errors shown to users, reports, CLI output | Vietnamese (source language of the UI), with English translations in `locales/en.json` |

Notes:
- Use English accounting terms (cash receipt, posting, general ledger, opening balance, VAT return…) and keep official
  codes as they are (TK 111, S03a-DNN, B01a-DNN, 01/GTGT). Cite laws as "Circular 133/2016/TT-BTC",
  "Decree 254/2026/ND-CP".
- A comment may quote a Vietnamese UI string or legal title inside quotes or backticks, e.g.
  `# shown as "Phiếu thu"`.
- `make lint` runs `tools/check_english.py`, which fails on Vietnamese text in comments, docstrings and log messages.

## Code style

Python code follows **[PEP 8](https://peps.python.org/pep-0008/)**, enforced by [ruff](https://docs.astral.sh/ruff/)
with the configuration in `pyproject.toml`:

- maximum line length **79** characters; comments and docstrings wrapped at **72** (W505);
- formatting by `ruff format` (4-space indentation, double quotes, consistent wrapping);
- rule sets `E`/`W` (pycodestyle), `F` (pyflakes), `I` (import order: standard library, third party, first party)
  and `N` (pep8-naming: `snake_case` functions and variables, `CapWords` classes, `UPPER_CASE` constants, exception
  names ending in `Error`);
- docstrings follow [PEP 257](https://peps.python.org/pep-0257/): a one-line summary, a blank line, then details;
- long user-facing strings are split with implicit concatenation inside parentheses – the resulting text must not
  change;
- `# noqa` only when there is no reasonable alternative, always with a reason, e.g. `# noqa: E501 - URL`.

```bash
make format    # ruff format + ruff check --fix
make lint      # what CI runs: format check, ruff check, English-only check
```

Templates, JavaScript and CSS follow the style of the surrounding code (2-space indentation, Tailwind utility classes,
no inline scripts).

## Development setup

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and make. Tailwind CSS uses the standalone CLI – no Node.

```bash
make setup && make tailwind && make css            # venv + dependencies, Tailwind CLI, build CSS
make migrate && .venv/bin/python -m app.cli demo   # demo data: demo@example.com / Demo@2026
make dev                                           # http://127.0.0.1:8000 (auto-reload)
```

After changing templates or CSS classes run `make css` again (or
`tools/tailwindcss -i app/static/src/app.css -o app/static/css/app.css --watch`). Shared UI components are shown at
`/ui-kit` (admin login) and documented in [docs/UI.md](docs/UI.md).

## Tests

```bash
make test                                   # full suite on SQLite
make e2e                                    # end-to-end in a real browser (Playwright; uses Chrome if chromium is missing)
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/test .venv/bin/pytest   # on PostgreSQL
python tools/i18n_scan.py                   # UI strings missing an English translation
```

Pull requests must pass the whole suite on SQLite and PostgreSQL plus `make lint` (CI runs all of them). Every bug fix
comes with a test that reproduces the bug.

## Architecture and conventions

- `app/core` is the infrastructure: multi-tenancy, permissions, registry, posting engine, database triggers, shared UI.
- Each business area is a module `app/features/<module>/` with `models.py`, `router.py`, `perms.py`, `nav.py`,
  `ai_tools.py`, `checks.py`, `templates/` and `locales/`. Modules are loaded automatically through the registry; the
  core–module contract is in [docs/CONTRACTS.md](docs/CONTRACTS.md) and module specs in [docs/MODULES.md](docs/MODULES.md).

Mandatory rules:

1. **Multi-tenancy** – business models use `TenantMixin`; every query filters on `company_id`; IDs received from forms
   or the API must be checked to belong to the current company. An integration test scans for cross-company leaks
   (`tests/integration`).
2. **Post through the engine** – every journal entry goes through `app/features/journal` (debit/credit account pairs).
   Posted vouchers are never edited or deleted; corrections are reasoned cancellations or adjusting entries.
3. **Money is `Decimal`**, never `float`.
4. **Permissions** – routes check permissions with the core dependencies; AI tools declare a `perm`; write tools only
   create drafts.
5. **Schema changes** come with an Alembic migration in `migrations/versions/`.
6. **UI** – user-facing strings are wrapped in `_()` and translated in `locales/en.json`; no inline scripts (CSP); never
   interpolate user data into Alpine expressions (`x-data`, `@click`, `x-text`) – pass it through `data-*` attributes
   or `|tojson`.
7. **Cite your sources** – accounting treatments, report layouts and tax parameters are recorded in
   [docs/DECISIONS.md](docs/DECISIONS.md) with the source they were checked against.

## Pull request process

1. Fork [nviai/vfas](https://github.com/nviai/vfas) and branch from `main`: `fix/negative-cash-balance`, `feat/viettel-einvoice`…
2. Use [Conventional Commits](https://www.conventionalcommits.org/) in English: `feat(cash): …`, `fix(vat): …`,
   `docs: …`.
3. Update the documentation, and `CHANGELOG.md` ("Unreleased") when users are affected. If you change `README.md`,
   update `README.vi.md` too.
4. Open the pull request with the template and explain **why** the change is needed and how you tested it.

## License of contributions

VFAS is licensed under the [MIT License](LICENSE) (copyright NVIAI CO., LTD.). By submitting a contribution you
confirm that you have the right to submit it and agree to release it under the same license. Signing off commits with `git commit -s`
([Developer Certificate of Origin](https://developercertificate.org/)) is encouraged.
