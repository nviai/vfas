# User interface – design system v2

Live sample page: **`/ui-kit`** (enabled with `DEBUG_UI=1` or for a superadmin account). Every component and page template
below is there with fake data. To view it outside the app (no DB needed):

```bash
python tools/render_kit.py /tmp/kit && python -m http.server 8765 --directory /tmp/kit
# http://127.0.0.1:8765/ui-kit/  ·  /dark/ui-kit/<page>/  ·  /m/<page>.html (390px phone frame)
```

Stack: Jinja2 + HTMX 2.0 + Alpine 3.17 (+ the Focus plugin for `x-trap`) + Tailwind v4 (standalone CLI).
No JS build step. CSS: `make css` (`tools/tailwindcss -i app/static/src/app.css -o app/static/css/app.css --minify`).

## 1. Principles

1. **Numbers are the main characters.** Money is always right-aligned, `tabular-nums`, formatted `1.234.567`; negative numbers are red (`amount-neg`).
   Never show money in chart data colors.
2. **Keyboard first.** Every voucher form: `Ctrl+S` saves a draft, `Ctrl+Enter` posts, `Enter` moves through the line grid.
   Global shortcuts: `Ctrl+K` search/jump, `Ctrl+J` AI assistant, `?` shortcut sheet, `Alt+<letter>` per `quick_actions`.
3. **Moderate density.** 13.5px text, 36px inputs (32px inside grids), thin 1px borders, very subtle shadows (light) / no shadows (dark).
4. **Status is never shown by color alone.** Badges have a dot + text; toasts/alerts have an icon; charts have a legend + a hidden table for screen readers.
5. **Wrap every string in `_()`**, with Vietnamese source strings; translations live in `app/features/<module>/locales/en.json`.
   Official print forms (PDF/"paper" previews) are always in Vietnamese and never translated.

## 2. Tokens

Semantic colors switch between light and dark (CSS variables `--ui-*`, declared on `:root` and `.dark`):

| Tailwind class | Used for |
|---|---|
| `bg-bg` | page background |
| `bg-sidebar` | sidebar |
| `bg-surface` · `bg-surface-2` · `bg-surface-3` | cards · secondary background (table header, footer) · hover/chips |
| `border-line` · `border-line-strong` | regular borders · input borders |
| `text-fg` · `text-fg-2` · `text-fg-3` | primary · secondary · muted text |
| `bg-primary` `text-primary-fg` `hover:bg-primary-hover` | primary button (teal-700 light / teal-500 dark) |
| `bg-primary-soft` `text-primary-soft-fg` | soft accent background |
| `brand-50 … brand-950` | full teal scale |
| `shadow-card` · `shadow-pop` | cards · menus/dialogs |

Statuses use Tailwind's built-in colors: emerald (success), amber (warning/draft), rose (error/cancelled), sky (info), violet (locked).
Font: self-hosted Inter Variable (`app/static/fonts`, vietnamese + latin + latin-ext subsets).
Dark mode: `dark` class on `<html>`; cookie `theme` = `light | dark | system`; `js/theme-init.js` applies it before the first paint.
Collapsed sidebar: `<html data-sidebar="collapsed">` (variant `sb-collapsed:`), remembered in localStorage.

## 3. Page shells

- `base.html` – the logged-in app: sidebar from `nav`, topbar (company switcher, Ctrl+K search box, **Tạo mới** (New) button from
  `quick_actions`, **Trợ lý AI** (AI assistant) button when `ai_enabled`, user menu: account, appearance, language, shortcuts, log out),
  bottom bar on mobile, toasts, confirmation dialog, AI drawer (lazy-loaded from `GET /ai/panel`).
  Variables: see `docs/CONTRACTS.md` section 1. Copes with `user=None`, `company=None`, `nav=[]`.
  Blocks: `title`, `head`, `body_class`, `content`, `scripts`.
- `auth_base.html` – login/sign-up/setup/invitation/company selection/errors when not logged in: introduction on the left half,
  form on the right half. Blocks: `title`, `width` (default `max-w-[400px]`), `content`, `scripts`.
- `errors/error.html` – variables `status`, `title`, `message`.
- Server flashes appear as **toasts** (base) or **alerts** (auth_base). HTMX: return the header
  `HX-Trigger: {"toast": {"message": "Đã lưu", "kind": "success"}}` to show a toast.

Every page:

```jinja
{% extends "base.html" %}
{% import "macros.html" as ui with context %}
{% import "charts.html" as charts %}
{% block title %}{{ _("Phiếu thu, chi") }}{% endblock %}
{% block content %}
{% call ui.page_header(_("Phiếu thu, chi"), _("Mô tả ngắn"), breadcrumbs=[(_("Tiền"), "/cash"), (_("Phiếu thu, chi"), None)]) %}
  {{ ui.link_button(_("Phiếu thu"), "/cash/new?kind=PT", "primary", icon_name="plus") }}
{% endcall %}
…
{% endblock %}
```

> `with context` is required when importing `macros.html` (the macros use `_` and `csrf_token`). v1 templates that use
> `{% from "macros.html" import page_header %}` still work (the macro skips translation when `_` is missing).
> The icon parameter is named **`icon_name`** (not `icon`, because `icon` is the name of a macro).

## 4. Page templates (copy from `app/core/templates/ui_kit/`)

| Template | File | Notes |
|---|---|---|
| (a) List | `ui_kit/list.html` | status tabs + `filter_bar` + table (desktop) / cards (mobile, `md:hidden`) + `pagination`. If the table has per-row menus, add `md:overflow-visible` to `.table-wrap` |
| (b) Voucher form | `ui_kit/form_entry.html` | `<form x-data="docForm()" @keydown="keys($event)">`, general information card, postings card `ui.line_grid`, `ui.form_actions` sticky at the bottom. The grid needs ≥ 900px: do not put a side column next to the grid below `2xl` |
| (b) Invoice | `ui_kit/form_invoice.html` | customer combobox + tax code/address auto-filled via the `combobox-select` event, `ui.invoice_grid` |
| (c) Detail | `ui_kit/detail.html` | page_header + status badge + actions dropdown; "đã ghi sổ" (posted) ribbon; `desc_list`, postings table, right column: amount + amount in words, attachments, `timeline`; cancel modal with a reason |
| (d) Report | `ui_kit/report.html` | `period_selector` + `report_toolbar`; preview in `.paper` with `official_header`, `report_title`, `.table-report`, `signature_block` |
| (e) Dashboard | `ui_kit/dashboard.html` | 4 `stat_card`s, receipts/payments `bar_chart` + 12-month totals, to-dos, filing deadlines, `line_chart`, `hbar_list`, `donut`, recent vouchers |
| (f) Auth | `features/auth/templates/*.html`, `errors/error.html` | see `docs/CONTRACTS.md` section 4 |
| (g) Settings | `ui_kit/settings.html` | tabs + `form_section` (title on the left, controls on the right) + `form_actions`. Real pages should use `ui.tabs` (one URL per tab) |
| (h) AI assistant | `partials/ai_chat.html`, `partials/ai_message.html` | see section 9 |

Checklist before a page is done:
- [ ] Every string goes through `_()` and has a translation in the module's `locales/en.json`; check it on `/lang/en`.
- [ ] 390px: no horizontal page scroll (wide tables sit inside `.table-wrap`), the primary button stays visible, `form_actions` is not hidden by the bottom bar.
- [ ] Dark mode: no hard-coded colors (`bg-white`, `text-slate-…`) – use tokens such as `bg-surface`, `text-fg-2`…
- [ ] Amounts: `|money` + `.num`; blank cells instead of `0` in Debit/Credit columns (`|money0`).
- [ ] Destructive actions have `confirm=` (button) or `data-confirm` (form/link) and `danger=True`.
- [ ] Forms include `{{ ui.csrf_field() }}`; HTMX sends the `X-CSRF-Token` header automatically (set on `<body>`).

## 5. Macros (`macros.html`)

The full signatures are in the file; the main parameters are listed below.

### Layout
| Macro | Usage |
|---|---|
| `page_header(title, subtitle=None, breadcrumbs=None, icon_name=None, badge=None, back=None)` + caller = buttons | page title; `breadcrumbs=[(label, href \| None)]`; `badge=ui.status_badge("draft")`; `back="/cash"` shows a ← button |
| `section_title(title, subtitle=None)` + caller | group heading within a page |
| `card(title=None, subtitle=None, actions=None, flush=False, cls="", body_cls="", footer=None, id=None, icon_name=None)` + caller | `actions`/`footer` are HTML (`{% set acts %}…{% endset %}`); `flush=True` when it contains a table |
| `stat_card(label, value, unit="đ", delta=None, delta_label=None, good="up", icon_name=None, hint=None, href=None, spark=None, money_value=True)` | `delta` = % (float); `good="down"` when smaller is better (receivables/payables, expenses); `spark=[…]` replaces the icon with a sparkline |
| `desc_list(items, cols=…)` | `items=[(label, value)]`; empty values show "–" |
| `empty_state(title, icon_name="inbox", message=None, compact=False)` + caller = buttons | |
| `alert(message, kind="info", title=None, dismissible=False)` + caller = buttons | kind: success, error, warning, info |
| `skeleton(lines=3)`, `progress(value, max=100, tone="brand")`, `kbd("Ctrl+K")`, `avatar(name)`, `money(value, signed=False)` | |
| `timeline(items)` | `items=[{at, actor, action, detail, icon, tone}]` |
| `attachment_list(files, can_delete=False)` | `files=[{name, size, href, kind: pdf\|image\|xml\|xlsx, delete_action}]` |

### Buttons, badges, menus
| Macro | Usage |
|---|---|
| `button(label=None, variant="primary", size=None, icon_name=None, icon_right=None, type="submit", name=None, value=None, attrs="", cls="", kbd_hint=None, disabled=False, hotkey=None, confirm=None, danger=False, form=None)` | variant: `primary secondary ghost soft danger danger-ghost success`; size: `xs sm lg`; `hotkey="save"\|"post"` for `docForm`; `confirm="Hỏi?"` (the confirmation question) |
| `link_button(label, href, variant="secondary", size=None, icon_name=None, icon_right=None, attrs="", cls="", kbd_hint=None, target=None)` | |
| `icon_button(icon_name, label, href=None, variant="ghost", size="sm", attrs="")` | `label` = aria-label + tooltip |
| `badge(label, color="gray", icon_name=None, dot=False)` | color: gray brand green amber red blue violet |
| `status_badge(status)` | draft posted cancelled locked open active inactive paid partial unpaid overdue matched unmatched pending error submitted invited disposed |
| `kind_badge(kind)` | PT PC BC BN PK BH MH TL NK XK KH PB LG KC CK (+ v1 receipt/payment) |
| `dropdown(label=None, icon_name=None, variant="secondary", size="sm", align="right", width="w-56", aria_label=None)` + caller | the caller contains `menu_item(label, href=None, icon_name=None, danger=False, attrs="", kbd_hint=None)`, `menu_post(label, action, icon_name=None, danger=False, confirm=None, fields={})`, `menu_sep()`, `menu_label(text)` |
| `tabs(items, active)` | `items=[{key, label, href, count, icon}]` |
| `pagination(page, pages, url_for_page, total=None)` | `url_for_page` is a macro: `{% macro page_url(p) %}?page={{ p }}{% endmacro %}` |
| `modal(id, title=None, size="md", description=None)` + caller | open: `@click="$dispatch('modal-open', 'id')"`; close: `$dispatch('modal-close')`. Body: `<div class="px-5 pb-5">`, footer: `<div class="flex justify-end gap-2 border-t border-line bg-surface-2 px-5 py-3">` |
| `drawer(id, title=None, width="sm:max-w-lg", description=None)` + caller | open: `$dispatch('drawer-open', 'id')` |

Confirmation outside macros: `data-confirm="…"` (+ `data-danger`) on a `<form>`, a submit button or an `<a>`; `hx-confirm` also uses the app's dialog.
JS: `toast(message, kind)`, `await uiConfirm(message, {title, confirmLabel, danger})`.

### Forms
All of them accept `errors` (dict of name → message, translated automatically) and show the error below the field; the default id is `f-<name>`.

| Macro | Sent to the server |
|---|---|
| `input(name, label=None, value="", type="text", errors=None, required=False, hint=None, attrs="", cls="", placeholder=None, prefix=None, suffix=None, autofocus=False, input_cls="", id=None, help=None, readonly=False)` | `name` |
| `textarea(name, label=None, value="", rows=3, …)` · `select(name, label=None, options=(), value=None, placeholder=None, …)` | `options`: `[(v, label)]`, `[{value, label}]` or `["a"]` |
| `password_input(name, label=None, errors=None, autocomplete="current-password", hint=None, required=True, minlength=None)` | has a show/hide button |
| `checkbox(name, label, checked=False, value="1", hint=None)` | `name=1` when checked |
| `switch(name, label, checked=False, hint=None)` | always sends `name=1` or `0` |
| `radio_group(name, label=None, options=(), value=None, style="segmented" \| "cards" \| "list")` | `cards`: options `[{value, label, hint}]`. When using `attrs='x-model="kind"'`, the Alpine variable must be initialised with exactly `value` (e.g. `x-data="{kind: 'PT'}"`), otherwise Alpine unchecks every option on startup; if you only need to react to changes, use `@change`. |
| `date_input(name, label=None, value=None, min=None, max=None, …)` | `yyyy-mm-dd` |
| `money_input(name, label=None, value=None, …, suffix="đ")` | hidden `name` = **integer dong**; the visible input accepts `2tr`, `500k`, `1,5tr`, `10.000.000` |
| `combobox(name, label=None, url="/lookup/accounts", value=None, value_label=None, params=None, placeholder=None, errors=None, required=False, hint=None, allow_free=False, local_filter=False, items=None, min_chars=0)` | hidden `name` = `value`; emits the `combobox-select` event (detail `{value, label, hint, extra}`) |
| `field(name, label=None, required=False, hint=None, errors=None, help=None)` + caller | label/error wrapper around a hand-written control |
| `field_error(errors, name)` · `csrf_field()` | |
| `form_section(title, description=None)` + caller | settings layout |
| `form_actions(sticky=True)` + caller | button bar sticky at the bottom (above the bottom bar on mobile) |

Example – auto-fill the address when a partner is selected:
```jinja
<div x-data="{ address: '' }" @combobox-select="address = ($event.detail.extra || {}).address || address">
  {{ ui.combobox("partner_id", _("Đối tượng"), url="/lookup/partners", params={"role": "supplier"}) }}
  <input name="contact_address" class="input" x-model="address">
</div>
```

### Voucher line grid – `line_grid`
```jinja
{{ ui.line_grid("split", lines) }}                                    {# Account | Debit | Credit | Partner | Description #}
{{ ui.line_grid("pair", lines, fixed={"debit_account": "1111"}) }}  {# cash receipt: Credit account | Amount | … (Debit 1111 fixed) #}
```
Parameters: `mode`, `lines=[{account, account_label, debit, credit, debit_account, debit_account_label, credit_account,
credit_account_label, amount, partner_id, partner_label, description}]`, `prefix="lines"`, `accounts_url`, `accounts_params`
(default `{"postable": 1}`), `partners_url`, `show_partner=True`, `fixed={}`, `description_from="#f-description"`, `min_lines`.

Form submission: `lines-count`, then for i = 0…count-1: `lines-i-account`, `lines-i-debit`, `lines-i-credit` (split) or
`lines-i-debit_account`, `lines-i-credit_account`, `lines-i-amount` (pair), `lines-i-partner_id`, `lines-i-description`.
Money is an integer; an empty cell is an empty string. Keys: `Enter` moves to the next cell/adds a line (a new line is pre-filled
with the difference needed to balance Debit/Credit), `Ctrl+D` duplicates, `Ctrl+Delete` deletes the line. The grid footer shows
"Cân Nợ – Có" (balanced) or "Lệch …" (out of balance by …).

### Invoice grid – `invoice_grid`
```jinja
{{ ui.invoice_grid(lines, items_url="/lookup/items", price_field="sale_price", default_vat="10") }}
```
`lines=[{item_id, item_label, description, unit, quantity, unit_price, amount, discount, vat_rate, vat_amount}]`;
`vat_rates` defaults to 10, 8, 5, 0, KCT, KKKNT; `show_discount=False`. Choosing an item auto-fills the description, unit, unit price
(`extra[price_field]`) and VAT rate (`extra.vat_rate`). Amount = round(qty × unit price); VAT = round((amount − discount) × VAT rate).
The "Thành tiền" (Amount) / "Tiền thuế" (VAT amount) cells can be overridden by hand (yellow border). Submits: `lines-count`,
`lines-i-item_id`, `-description`, `-unit`, `-quantity` (e.g. `1234.5`), `-unit_price` (e.g. `15000.5`), `-amount`, `-discount`,
`-vat_rate`, `-vat_amount`.

### Reports
| Macro | Usage |
|---|---|
| `period_selector(period, years=None, action="", extra_hidden=None, modes=("month","quarter","year","custom"))` | `period`: `.mode .start .end` (+ `.quarter .year`). Sends `mode`, `month=YYYY-MM`, `quarter`, `year`, `start`, `end`. Add `"all"` to `modes` if needed |
| `report_toolbar(pdf_href=None, xlsx_href=None, print=False)` + caller | |
| `official_header(company, form_code=None)` · `report_title(title, period_label=None, unit_label="Đơn vị tính: Đồng")` · `signature_block([(title, note, name)], date_label)` | inside `<div class="paper">`; tables use `.table .table-report` |

## 6. Tables (CSS classes)

`.table-wrap` (horizontal scroll) › `table.table` (+ `table-hover`, `table-compact`, `table-sticky`, `table-report`, `table-fixed`).
Numeric cells: `td.num`/`th.num`. Rows: `tr.is-link`, `tr.is-selected`, `tr.is-muted`, `tr.row-total`, `tr.row-group`.
Voucher numbers: `.doc-no`; codes: `.mono`; receipt/payment amounts: `.amount-in` / `.amount-out`; negative: `.amount-neg`.
A table that is the last child of `card(flush=True)` gets rounded bottom corners automatically.

## 7. Charts (`charts.html`)

SVG rendered on the server, x in %, y in px → scales with the container while text keeps its real size; x-axis labels thin out
automatically in narrow containers (container query).
Colors follow the entity (colorblind-checked palette, variables `--series-1…8`, with separate steps for dark backgrounds):
**receipts = slot 1, payments = slot 2, balance = slot 1 when shown on its own** – kept the same across pages. Every chart has
tooltips (hover/focus) and a hidden table for screen readers.

| Macro | Usage |
|---|---|
| `bar_chart(labels, series, height=240, value_format="money", caption=None, legend=True)` | grouped bars; `series=[{"name", "values", "slot"}]`; negative values are drawn below the zero line |
| `line_chart(labels, series, height=240, area=True, value_format="money", caption=None, zero_based=True)` | a single series gets a soft fill; multiple series get a legend |
| `donut(items, center_label=None, center_value=None, size=148, value_format="money")` | shares of ≤ 6 parts, `items=[{label, value, slot}]` |
| `hbar_list(items, value_format="money", slot=1)` | ranking, `items=[{label, value, href}]` |
| `sparkline(values, width="w-24", height=28)` | inside `stat_card(spark=…)` |
| `compact(v)` | 1,2 tỷ · 350 tr · 12 k (Vietnamese short units: tỷ = billion, tr = million) |

`value_format`: `money` (1.234.567), `compact`, or raw. No dual Y axes; two quantities in different units → two charts.
Data that is all zeros, constant, empty or `None` still renders (the scale widens automatically).

## 8. Alpine components (`app/static/js`)

| File | Component / function |
|---|---|
| `theme-init.js` | (synchronous, in `<head>`) applies the theme + collapsed sidebar; `window.__theme.get()/set()` |
| `core.js` | `formatVnd`, `parseVnd`, `parseQty`, `formatQty`, `foldVi` (strips diacritics), `setCookie`, `uiStore` (safe localStorage), `csrfToken()`, `readJSON(id)`, `debounce`, `toast()`, `uiConfirm()`, `data-confirm` handling, htmx configuration (422/403 still swap, `HX-Trigger: toast`, network error reporting), chart tooltips |
| `shell.js` | `appShell()` (navOpen, collapsed, theme, paletteOpen, aiOpen, helpOpen, createOpen; `openAi()`, `setTheme()`, shortcuts), `commandPalette()`, `toaster()`, `confirmDialog()` |
| `forms.js` | `moneyInput(initial)`, `combobox(opts)`, `lineGrid(opts)`, `invoiceGrid(opts)`, `docForm()`, `cashCount(denoms, book, initial)`, `voucherForm()` (v1, to be removed) |
| `ai.js` | `aiChat({postUrl, conversationId, historyCount})` – reads the SSE events `meta/token/tool/done/error` |

`combobox(opts)`: `{url, params, value, label, minChars, allowFree, localFilter, items, onSelect, limit}`; the server returns
`[{value, label, hint, extra}]` or `{results: [...]}`. Inside grids, the dropdown list uses `position: fixed` (`data-fixed`)
so that the table's scroll container does not clip it.

`docForm()`: put it on the `<form>` together with `@keydown="keys($event)"`; `Ctrl+Enter` clicks the `data-hotkey="post"` button, `Ctrl+S` clicks `data-hotkey="save"`.

## 9. AI assistant (drawer)

`base.html` has an `#ai-panel` drawer (`data-url` = `ai_panel_url`, default `/ai/panel`) that htmx lazy-loads on first open.
The AI router returns `{% include "partials/ai_chat.html" %}` with the variable:

```python
chat = {
  "conversation_id": 12 | None,
  "messages": [{"role": "user" | "assistant", "text": "...", "html": "<p>…</p>",   # html already sanitized on the server
                "tools": [{"name", "label", "status": "done|error", "summary", "link"}]}],
  "conversations": [{"id", "title", "updated_at", "active"}],
  "suggestions": ["…"], "post_url": "/ai/chat", "model_label": "…",
  "disabled_reason": None | "Chưa cấu hình…", "settings_url": "/settings/ai",
}
```
`GET /ai/conversations/{id}` returns the saved messages, each rendered with `{% include "partials/ai_message.html" %}` and the variable `m`.
Streaming protocol: `docs/CONTRACTS.md` section 3.

## 10. Icons

`ui.icon(name, cls="size-4", stroke=1.75, label=None)` – the Lucide set (lucide-static 0.460.0, ISC) in `icons.html`.
Both the original Lucide names (`wallet`, `landmark`, `receipt-text`, `file-spreadsheet`, `git-compare`, `hand-coins`, `scale`, `sparkles`…)
and familiar aliases are available: `edit trash alert-triangle more-horizontal unlock chart-bar refresh key home dashboard bank cash help loader
attachment transfer reconcile ai`. To add an icon: download the SVG from `cdn.jsdelivr.net/npm/lucide-static@0.460.0/icons/<name>.svg`
and copy what is inside `<svg>` into `ICONS`.
