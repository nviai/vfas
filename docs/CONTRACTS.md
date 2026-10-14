# Contracts between core, UI and modules (v2)

This document fixes the interfaces so the teams can work in parallel. Changes must be recorded in `docs/requests/<team>.md`.

## 1. Template variables provided by core (`app/core/web.py::base_context`)

| Variable | Type | Notes |
|---|---|---|
| `user` | obj \| None | `.id .full_name .email .initials .role .role_label .is_superadmin`, `.can(perm)` |
| `company` | obj \| None | currently selected company: `.id .name .short_name .tax_code .address` |
| `companies` | list[dict] | `{id, name, role_label, current: bool}` – for the company switcher |
| `nav` | list[dict] | `{key, label, icon, items: [{label, href, icon, active: bool, badge: str\|None}]}` filtered by permission; empty groups are dropped |
| `quick_actions` | list[dict] | `{label, href, icon, shortcut: str\|None, group}` – the "Tạo mới" (New) menu and Ctrl+K |
| `product_name` | str | |
| `ai_enabled` | bool | shows the AI assistant button |
| `csrf_token` | str | send via the `csrf_token` input or the `X-CSRF-Token` header |
| `lang` | `vi`/`en` | |
| `theme` | `light`/`dark`/`system` | `theme` cookie |
| `_` | callable | translation: `_("Chuỗi tiếng Việt")`, accepts `**kwargs` for formatting |
| `get_flashes()` | list[dict] | `{kind: success\|error\|warning\|info, message}` |
| `can(perm)` | callable | |
| `is_htmx`, `path`, `today` | | |
| `breadcrumbs` | list[(label, href\|None)] | per page |

Jinja filters: `money` (1.234.567), `money0` (blank when 0), `qty` (1.234,5), `pct`, `date` (dd/mm/yyyy),
`datetime`, `words` (amount in words).

## 2. Shared endpoints

| Endpoint | Description |
|---|---|
| `POST /switch-company` (form `company_id`) | switch company, redirect to `/` |
| `GET /lang/{vi\|en}` | switch language |
| `POST /logout` | |
| `GET /search?q=` | JSON `{"results": [{label, hint, href, icon, group}]}` for Ctrl+K |
| `GET /lookup/accounts?q=&postable=1&prefix=111&limit=20` | JSON `[{value, label, hint}]` (`value` = account code) |
| `GET /lookup/partners?q=&role=customer\|supplier\|employee` | `value` = id, `label` = "MÃ – Tên" (CODE – Name), `hint` = tax code/address, plus `extra: {address, tax_code}` |
| `GET /lookup/items?q=` | `extra: {unit, vat_rate, sale_price, purchase_price}` |
| `GET /lookup/bank-accounts?q=` | `extra: {account_code}` |

## 3. AI assistant (chat panel in the drawer)

- `GET /ai/panel` → HTML partial with the drawer content (conversation list + chat panel). Only called when `ai_enabled`.
- `POST /ai/chat` (form: `content`, optional `conversation_id`, `csrf_token`) → `text/event-stream`:
  - `event: meta` `{"conversation_id": 12}`
  - `event: token` `{"text": "…"}` (appended incrementally)
  - `event: tool` `{"id": "call_1", "name": "get_balance", "label": "Xem số dư", "status": "running|done|error", "summary": "…", "link": "/entries/5"|null}`
  - `event: done` `{"message_id": 99, "html": "<p>…</p>"}` (HTML sanitized server-side; replaces the streamed text)
  - `event: error` `{"message": "…"}`
- `GET /ai/conversations/{id}` → HTML partial with the messages.

## 4. Login/sign-up (templates owned by the UI team, router owned by core)

| Template | Form fields | Context |
|---|---|---|
| `auth/login.html` | `email`, `password`, `next` | `error`, `form`, `allow_signup` |
| `auth/signup.html` | `full_name`, `email`, `password`, `company_name`, `tax_code` | `errors` (dict), `form` |
| `auth/setup.html` (first run, self-hosted) | same as signup + `address` | `errors`, `form` |
| `auth/invite.html` | `full_name`, `password` (if the user has no account yet) | `invitation` (`.email .company_name .role_label`), `has_account`, `errors` |
| `auth/select_company.html` | company selection buttons | `companies` |

## 5. Module registry (`app/core/registry.py`)

```python
from app.core.registry import NavItem, QuickAction, nav, quick_action, permission, ai_tool, data_check, posting_validator, hook

# nav.py
NAV = [NavItem(section="money", label="Phiếu thu, chi", href="/cash", icon="wallet", perm="cash.view", order=10)]
ACTIONS = [QuickAction(label="Phiếu thu", href="/cash/new?kind=PT", icon="arrow-down-circle", perm="cash.create", shortcut="Alt+T")]

# perms.py  – permission → default roles
PERMISSIONS = {"cash.view": ROLES_ALL, "cash.post": {"owner", "chief_accountant", "accountant", "cashier"}}

# ai_tools.py
@ai_tool(name="get_cash_balance", description="…", parameters={...JSON Schema...}, perm="cash.view", write=False)
def get_cash_balance(db, ctx, *, date: str | None = None) -> dict: ...

# checks.py
@data_check(code="cash_negative", title="Tồn quỹ âm", perm="cash.view")
def check(db, ctx) -> list[CheckIssue]: ...
```
Nav sections: `overview, money, sales, purchases, inventory, assets, payroll, tax, books, catalog, system`.
