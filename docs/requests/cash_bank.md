# Requests to the core – team B (cash, bank)

1. **Delete the old print templates** `app/core/templates/print/tt133/` (v1). The `cash` module has its own set in
   `app/features/cash/templates/print/` (`_layout`, `_parts`, `voucher` 01-TT/02-TT, `book` S04a/S04b/S05, `cashcount` 08a-TT);
   `bank/templates/print/unc.html` (payment order) reuses `cash/print/_layout.html`.
2. **Bulk posting when importing an Excel cash book**: `cash/importer.py` creates drafts via `journal.service.create_entry` and then switches them
   to `posted` itself (setting `posting_seq`, `posted_by/at`) inside `company_tx`, running the retroactive negative check only for account 1111.
   Reason: historical data has 1121 contra lines (cash withdrawals/deposits) while the bank balance is not in the software yet → `post_entry` would block
   because account 1121 goes negative. Suggest the core adds a parameter, e.g. `post_entry(..., negative_accounts={"1111"}, number_preset=True)`, or a
   `post_imported(entries)` function so the module does not have to change the status itself; once available, switch the importer to it.
3. **Breadcrumbs not translated**: `ui.breadcrumbs_nav` prints labels verbatim – routers pass Vietnamese strings. Suggest the macro applies `tr(label)`
   (translations for team B's labels are already in `locales/en.json`).
4. **`.paper` in dark mode**: table row classes (`tr.row-group`…) change background with the theme, so they become dark on white "paper".
   Team B removed `row-group` from the ledger preview; suggest the UI forces light colors for every element inside `.paper`.
5. **Generic page `/entries/{id}`** (team A): for PT/PC/BC/BN/CK it should redirect to `entry.url` (`/cash/{id}`, `/bank/{id}`).
   Team B's S04a/S04b/S05 books already link straight to the module URL.
6. **Hooks**: `bank/hooks.py` uses `@on("entry.posted")` (auto-matches the statement line when a voucher created from a statement is posted) and
   `@on("entry.cancelled")` (releases the matched statement line). Vouchers created from a statement store `meta["statement_line_id"]`.
