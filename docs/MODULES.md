# MODULE SPECIFICATIONS (step B) – for teams working in parallel

Read first: `docs/PLAN.md`, `docs/CONTRACTS.md`, `docs/UI.md`, `docs/DECISIONS.md` and the core code:
`app/core/{registry,tenancy,security,web,xlsx,pdf,money,dates,audit}.py`,
`app/features/journal/{models,service,queries,locks,opening,router,checks}.py`,
`app/features/{accounts,partners,items,bank,org}/service.py`, `tests/conftest.py`, `tests/core/*.py`.

## General rules

1. **Only edit your own team's directories** (the "Owns" line) + `tests/<module>/`. Do not edit `app/core/**`,
   `app/features/journal/**`, other modules or `tests/conftest.py`. If core needs to change → write to `docs/requests/<team>.md`
   (description + rationale), and implement a temporary version inside your own module if possible.
2. **Every journal entry goes through the engine** `app.features.journal.service` (`EntryIn`, `LineIn`, `VatIn`, `create_entry`,
   `update_draft`, `post_entry`, `cancel_entry`, `reverse_entry`). Module documents (sales invoices, payrolls…) have an
   `entry_id` pointing to a `JournalEntry`; **the status comes from the entry** (no parallel status is stored).
   Set `EntryIn.source = "<module>"`, `source_id = <module document id>`.
3. **A module's voucher detail page** uses a route by **entry id** (`/cash/{entry_id}`…) and declares
   `DocType(code, label, url="/cash/{id}", perm="cash")` in `nav.py` (`DOC_TYPES`). The Post / Cancel / Adjust /
   Delete draft / Attach buttons use the shared core endpoints: `POST /entries/{id}/post|cancel|reverse|delete|info|attachments`,
   `GET /attachments/{id}`, `POST /attachments/{id}/delete` (permissions follow the DocType's `perm`: `<perm>.post` …).
   To handle things yourself (e.g. update your own table on posting), use the `@on("entry.posted")` / `@on("entry.cancelled")` hooks
   in `hooks.py`.
4. **Permissions** are declared in `perms.py` following the convention `<module>.view|export|create|post|cancel|manage`
   (use the constants `ALL_ROLES, MANAGERS, ACCOUNTANTS, BOOKKEEPERS, READERS` in `app.core.registry`).
   Routers use `require("<perm>")`, `get_tenant_db`, `get_ctx(request)`.
5. **UI**: extend `base.html`, use the macros/components in `docs/UI.md`, copy the closest page template in `/ui-kit`.
   Wrap every UI string in `_()`; add translations to `app/features/<module>/locales/en.json` (including flash messages and errors).
   **Print forms** (PDF) are always in Vietnamese, live in `app/features/<module>/templates/print/`, use the "Liberation Serif" font
   and the header Đơn vị/Địa chỉ/Mẫu số/"(Ban hành theo …)" (Entity/Address/Form no./"(Issued under …)")
   (`app.core.charts.tt133.ISSUED`). PDF: `app.core.pdf.render_pdf`; Excel: `app.core.xlsx.ReportBook` (+ `xlsx_response`, `pdf_response`).
6. **Money** is integer dong (`parse_vnd`, `format_vnd`); quantity/unit price are `Decimal`, line amounts are rounded to the dong
   (ROUND_HALF_UP). Dates via `parse_date`. No floats.
7. **AI tools** (`ai_tools.py`, `@ai_tool`): names are unique system-wide – use exactly the names in your team's section; a short
   English description + JSON Schema; return a JSON-serializable dict (amounts as int, ISO dates); write tools (`write=True`)
   **only create drafts** and return `{"draft_id", "url", "summary"}`.
8. **Data checks** (`checks.py`, `@data_check`), **menus** (`nav.py`: `NAV`, `ACTIONS`, `DOC_TYPES`).
9. **Tests** go in `tests/<module>/` (with `__init__.py`) and use the fixtures `db, company, users, ctxs, ctx, web, post`.
   Required: the main business scenario (post → books/reports with the right figures), permissions (disallowed role → 403),
   web smoke (every page GETs 200 as chief accountant, English `/lang/en` without errors), the PDF opens (pypdf), the Excel file re-opens.
   Run `pytest tests/core tests/<module> -q` and `ruff check app/features/<module> --select F,E9` before reporting done.
10. New models: a `models.py` (or `*_models.py`) file in the module, inheriting `TenantMixin, Base`; loaded automatically.
    No migration needed (the integration step creates the base migration).
11. Do not commit to git. Final report: files created, routes, permissions, voucher types, AI tools, checks, test results,
    requests to core.

---

## Team A – General ledger, financial statements, closing (`gl`)

**Owns**: `app/features/gl/**`.

- **Generic vouchers** `/entries`: list of every voucher type (filter by type, status, period, account, partner; search by
  number/description; HTMX; cards on mobile), linking to `entry.url`. `/entries/{id}`: if the type's `doc_url` is not `/entries/{id}`,
  redirect; otherwise show the generic detail (voucher header, Debit/Credit/partner postings table, VAT invoices, attachments,
  audit history, action buttons). Reusable partials: `gl/_entry_lines.html`, `gl/_attachments.html`,
  `gl/_entry_actions.html`, `gl/_history.html` (other modules may `{% include %}` them).
- **General journal voucher (PK)**: create/edit form with `lineGrid` (Description, Debit account, Credit account, Amount, Debit partner,
  Credit partner, bank account when needed), optional VAT invoice tab (VatIn), save draft / save and post; prints "PHIẾU KẾ TOÁN".
- **Opening balances** `/opening` (permission `opening.manage`, view `journal.view`): grid by account; detail by partner
  (partner-tracked accounts) and by bank account; Debit/Credit totals + imbalance warning; import from an Excel template (download
  template, preview, single transaction) using `journal.opening.replace_opening`.
- **Period locks** `/locks`: 12-month grid per year (`journal.locks.year_grid`), lock/unlock (with a reason), year lock.
- **Closing entries** `/closing` (permission `closing.run` = ACCOUNTANTS): choose the period (month/quarter/year), preview the closing
  entries 511x/515/711 → 911; 911 → 632/635/6421/6422/811/821; profit/loss 911 ↔ 4212; create KC vouchers (posted); running again =
  cancel the period's existing KC, then create new ones; "Kết chuyển lợi nhuận năm trước" (transfer prior-year profit) 4212 → 4211 at
  the start of the year. Revenue/expenses are computed from turnover **excluding KC vouchers**.
- **Books and reports** (web with `official_header` + PDF + Excel): S03a-DNN general journal; S03b-DNN general ledger (one account);
  account detail ledger (account + optional partner/bank account); F01-DNN trial balance (level 1/detail level; dual-nature
  accounts show balances broken down by partner on both sides); B01a-DNN, B02-DNN, B03-DNN (direct method), B09-DNN (notes template
  auto-filling the main figures). B01a/B02 have current-year/prior-year columns. Line item ↔ account mappings are declared as data in
  `gl/statements.py`; **verify the line item codes against the TT133 text** (WebSearch/WebFetch thuvienphapluat) and cite the source.
  B01a: check Total assets (270) = Total liabilities and equity (440) – codes as per the legal text. B03: skip internal transfer lines
  (111↔112); line items by contra account, overridable via `journal_lines.cash_flow`.
- **Doc types**: PK (`/entries/{id}`, perm journal), KC (`/closing/{id}` or `/entries/{id}`).
- **Nav** (section `books`): "Chứng từ" (Vouchers; `/entries`, order 5), "Sổ Nhật ký chung" (General journal), "Sổ Cái"
  (General ledger), "Sổ chi tiết tài khoản" (Account detail ledger), "Bảng cân đối tài khoản" (Trial balance), "Báo cáo tài chính"
  (Financial statements), "Kết chuyển cuối kỳ" (Period-end closing), "Khóa sổ" (Period locks); (section `catalog`) "Số dư đầu kỳ"
  (Opening balances). Action: "Phiếu kế toán" (General journal voucher).
- **AI tools**: `get_trial_balance(start, end)`, `get_account_ledger(account, start, end, partner_code?)`,
  `get_financial_statements(year)` (B01a+B02 summary), `get_profit_and_loss(start, end)`,
  `create_draft_journal_entry(date, description, lines:[{debit, credit, amount, description?, debit_partner_code?,
  credit_partner_code?}])` (write, perm `journal.create`).
- **Checks**: trial balance does not balance; B01a assets ≠ liabilities and equity; year locked but accounts of classes 5–9 still
  have balances (not closed).
- **Accounting regimes** (`app/core/regimes.py`): `statements.statement_set(company)` returns the statements of the
  company's regime – TT133 (`statements.py`: B01a/B02/B03-DNN), TT99 (`statements_tt99.py`: B01-DN with short/long-term
  rules, B02-DN, B03-DN) or TT58 (`statements_tt58.py`: B01-DNSN, B02-DNSN). URLs stay `/reports/b01a|b02|b03`; printed
  codes come from `regime_form(company, code)`.

## Team B – Cash, bank deposits (`cash`, `bank`)

**Owns**: `app/features/cash/**`, `app/features/bank/**` (keep `BankAccount` and the function signatures in
`bank/service.py` unchanged; adding functions/models/files is allowed). Reference: the v1 code (cash book) in the first git commit,
`app/features/` (vouchers, ledger, cashcount, imports) and the print templates `app/core/templates/print/tt133/*` (move/rewrite into
`app/features/cash/templates/print/`; the integration step will delete the old directory).

- **Cash receipts/payments (PT/PC)**: list (filters, HTMX, mobile cards), quick form: date, payer/payee (partner combobox
  + free text + auto-filled address), reason, "kèm theo … chứng từ gốc" (attached: … original documents), lines (PT: credit account +
  amount + partner + description, debit fixed to 1111; PC the other way round); `2tr` shorthand, live amount in words, shortcuts
  (Enter saves a draft, Ctrl+Enter saves and posts, Alt+T/Alt+C); **receipt/payment purposes** (a list with default accounts, stored in
  `company.settings["cash_purposes"]`, with defaults: "Thu tiền khách hàng" (customer collection) → 131, "Rút tiền gửi" (bank
  withdrawal) → 1121, "Góp vốn" (capital contribution) → 4111, "Thu khác" (other receipts) → 711, "Chi tạm ứng" (advance) → 141,
  "Trả NCC" (supplier payment) → 331, "Chi lương" (salary payment) → 334, "Nộp thuế" (tax payment) → 3331/3334/3335,
  "Chi phí quản lý" (administrative expense) → 6422, "Nộp tiền vào NH" (cash deposit to bank) → 1121…) so the cashier does not
  need to know account codes. Cash payment vouchers have a **VAT invoice** block (value, VAT rate, VAT amount → adds a Debit 1331
  line and `VatIn(direction="in")`). Voucher dates cannot be in the future. The detail page shows **every** PT/PC (including those
  created by other modules, e.g. salary payments). Prints 01-TT / 02-TT (A5, amount in words, 5 signatures) – drafts can be printed
  with a "NHÁP" (DRAFT) mark.
- **Cash book** S04a-DNN (one line per voucher, receipt/payment/balance columns) and S04b-DNN (by contra account), period
  month/year/custom, PDF (repeated header, "Sổ này có … trang" (This book has … pages)) + Excel (the balance column is a formula).
- **Cash count** 08a-TT: count by denomination 500,000 → 1,000, compare with the book balance, **suggest** an adjustment voucher
  (surplus: Debit 1111/Credit 3381; shortage: Debit 1381/Credit 1111) by opening a pre-filled draft form, print the count report.
  Models `CashCount`, `CashCountLine`.
- **Cash book import from Excel** (port of the v1 import: preview, error/warning classification, single transaction, original
  numbers kept) → posted PT/PC, permission `import.manage`; fixture `tests/fixtures/so_quy_mau.xlsx`.
- **Bank**: bank account list `/bank-accounts`; bank receipts/payments BC/BN (like cash receipts/payments, with a bank account
  selected); internal transfers CK (bank withdrawal to cash, cash deposit to bank, transfer between 2 banks); S05-DNN bank book per
  account (web/PDF/Excel); print the payment order ("Ủy nhiệm chi", generic template).
- **Statements & reconciliation** `/bank/statements`: CSV/XLSX import, column mapping (date, description, debit, credit, balance,
  reference no.) saved per bank account; statement lines (models `BankStatement`, `BankStatementLine`); auto-matching against the bank
  side of posted BC/BN/CK (same amount, ±3 days, preferring a matching reference number); manual match/unmatch; create draft BC/BN
  from unmatched lines with the contra account suggested by **keyword rules** (`BankRule`: keyword → account, partner); table
  reconciling the statement balance with the book balance.
- **Permissions**: `cash.view` ALL_ROLES, `cash.create|post` BOOKKEEPERS, `cash.cancel` ACCOUNTANTS, `cash.export` READERS∪{cashier},
  `cashcount.manage` BOOKKEEPERS; `bank.view|export` READERS, `bank.create|post|cancel|reconcile|manage` ACCOUNTANTS.
- **Doc types**: PT/PC `/cash/{id}` perm cash; BC/BN/CK `/bank/{id}` perm bank.
- **Nav** (section `money`): "Phiếu thu, chi" (Cash receipts/payments); "Sổ quỹ tiền mặt" (Cash book); "Kiểm kê quỹ" (Cash count);
  "Tiền gửi ngân hàng" (Bank deposits); "Sổ tiền gửi" (Bank book); "Đối chiếu sao kê" (Statement reconciliation). (section `catalog`):
  "Tài khoản ngân hàng" (Bank accounts). Actions: "Phiếu thu" (Cash receipt, Alt+T), "Phiếu chi" (Cash payment, Alt+C),
  "Thu tiền gửi" (Bank receipt), "Chi tiền gửi" (Bank payment), "Chuyển tiền nội bộ" (Internal transfer).
- **AI tools**: `get_cash_balance(date?)`, `get_bank_balances(date?)`, `create_draft_cash_voucher(kind: receipt|payment,
  date, amount, contra_account, contact_name, description, partner_code?)` (write, perm `cash.create`),
  `list_unmatched_bank_lines(bank_account?, limit?)`.
- **Checks**: unresolved cash count differences; statement lines unmatched for more than 30 days; book balance ≠ closing statement balance.
- **Tests**: the v1 section 8 scenario via cash receipts/payments (including rejecting a 15M PC dated 2026-02-10 and an 8M PC dated
  2023-04-01 with the report that row 3 would be −1,000,000; accepting a 2M PC), S04a for March 2023, Excel recalculated formulas
  match the DB, PDF printing, cash count, fixture import, BC/BN/CK, statement import + auto-matching.

## Team C – Sales, purchases, tax, receivables/payables (`sales`, `purchases`, `vat`, `debts`, `items` UI)

**Owns**: `app/features/{sales,purchases,vat,debts}/**`; `app/features/items/` (router, templates, nav, perms, locales,
new functions in service – keep the `Item` model and the existing function signatures).

- **Goods and services** `/items`: list, add/edit, Excel import.
- **Sales (BH)**: model `SalesDoc` (`entry_id`, `partner_id`, buyer_name/address/tax_code, `invoice_template`,
  `invoice_series`, `invoice_no`, `invoice_date`, `payment_method` credit|cash|bank, `bank_account_id`, `due_date`, totals for
  goods/discount/VAT/payment) and **`SalesLine`** with exactly these columns: `doc_id, line_no, item_id, description,
  unit, quantity (Qty), unit_price (Price), amount (Money, after discount), discount_amount, vat_rate, vat_amount,
  revenue_account, warehouse_id (Integer, nullable – used by team D)`. Postings: Debit 131 (partner) or 1111/1121 /
  Credit 511x per line; Debit 131 / Credit 33311 for VAT; `VatIn(direction="out")` per VAT rate. `EntryIn.source="sales"`,
  `source_id=doc.id`, `meta={"doc": "sales"}`. Cost of goods sold is NOT posted here (team D generates it during issue costing).
- **Sales discounts / returns (TL)**: Debit 511x, Debit 33311 / Credit 131; lines carry item + quantity (team D takes them back into stock).
- **Purchases (MH)**: `PurchaseDoc` + **`PurchaseLine`** with columns: `doc_id, line_no, item_id, description, unit, quantity,
  unit_price, amount, vat_rate, vat_amount, deductible (bool), debit_account (156/152/153/642x/211x/242…),
  warehouse_id (nullable)`. Postings: Debit debit_account / Credit 331 (or 1111/1121); Debit 1331 (1332 when debit_account is
  211x) / Credit 331 for the deductible VAT; non-deductible VAT is added to debit_account. `VatIn(direction="in")`.
  `source="purchases"`. **Purchase returns (TM)**: the other way round.
- **E-invoice XML import** (`defusedxml`): a single file or a ZIP (limit 200 files, 20MB uncompressed); read
  `HDon/DLHDon/TTChung` (KHMSHDon, KHHDon, SHDon, NLap), `NDHDon/NBan` + `NMua` (Ten, MST, DChi),
  `DSHHDVu/HHDVu` (THHDVu, DVTinh, SLuong, DGia, ThTien, TSuat), `TToan` (TgTCThue, TgTThue, TgTTTBSo); duplicate prevention
  (seller tax code + series + number); input invoices → draft MH (partner found/created by tax code, lines matched to items by name
  or to the default expense account), output invoices → draft BH; the XML file is stored as an attachment. Preview screen before creation.
- **VAT** (`vat`): purchase/sales invoice listings per period (from the `VatRecord`s of posted vouchers); **01/GTGT** return
  (line item formulas in PLAN section 5), monthly/quarterly per `company.vat_period`; saved returns (`TaxDeclaration`:
  period, JSON values, status draft/final) so that [43] of the previous period is carried to [22]; [37][38] entered manually;
  PDF + Excel + **HTKK XML** (form 842 v2.8.3, validated against the HTKK schema; still to be checked in HTKK); Decree 174/2025/ND-CP tax-reduction
  appendix (8% lines); period-end VAT offset entry (Debit 33311 / Credit 1331 for the smaller amount) created as a draft PK voucher.
  **03/TNDN** (main line items from profit/loss + manual adjustments; CIT rate 15/17/20% depending on the previous year's revenue –
  Law 67/2025). **Tax calendar** `vat/calendar.py`: `deadlines(company, today) -> list[{title, due, period, url}]` (monthly VAT:
  the 20th of the following month; quarterly: the last day of the first month of the next quarter; CIT finalization and financial
  statements: the last day of the 3rd month after year-end) – the dashboard will call this function.
- **XML returns for HTKK**: shared envelope `app/core/htkk.py`; `gl/htkk.py` (financial statements, form 683, TT133 only),
  `vat/htkk_cit.py` (03/TNDN, form 892, with 03-1A), `payroll/htkk.py` (05/QTT-TNCN, form 953, with 05-1/05-2).
- **Receivables/payables** (`debts`): receivables (131) / payables (331): summary by partner (opening, turnover, closing), detailed
  payment ledger per partner, FIFO aging (due date = voucher date + `payment_terms_days`, or `due_date`), balance confirmation
  letter (PDF).
- **Permissions**: `sales.view|export` READERS, `sales.create|post|cancel` ACCOUNTANTS; likewise `purchases.*`; `vat.view|export`
  READERS, `vat.manage` ACCOUNTANTS; `debts.view|export` READERS. Goods and services use `catalog.*`.
- **Doc types**: BH, TL `/sales/{id}` perm sales; MH, TM `/purchases/{id}` perm purchases.
- **Nav**: `sales`: "Bán hàng" (Sales), "Trả lại/giảm giá" (Returns/discounts), "Công nợ phải thu" (Receivables); `purchases`:
  "Mua hàng" (Purchases), "Nhập hóa đơn điện tử" (E-invoice import), "Công nợ phải trả" (Payables); `tax`: "Bảng kê thuế GTGT"
  (VAT listings), "Tờ khai 01/GTGT" (01/GTGT return), "Quyết toán TNDN" (CIT finalization), "Lịch nộp thuế" (Tax calendar);
  `catalog`: "Hàng hóa, dịch vụ" (Goods and services). Actions: "Bán hàng" (Sale), "Mua hàng" (Purchase), "Nhập hóa đơn XML"
  (Import XML invoice).
- **AI tools**: `get_receivables(partner_code?, date?)`, `get_payables(partner_code?, date?)`, `get_debt_aging(kind,
  date?)`, `get_vat_summary(period_start, period_end)`, `get_revenue_by_month(year)`,
  `create_draft_sales_invoice(...)` (write, `sales.create`), `create_draft_purchase(...)` (write, `purchases.create`).
- **Checks**: duplicate invoice numbers; vouchers with 1331/33311 but no VAT invoice; receivables/payables overdue > 90 days;
  malformed partner tax codes.

## Team D – Inventory (`inventory`)

**Owns**: `app/features/inventory/**`.

- Warehouse list `Warehouse`; `StockMove` (company, `entry_id` nullable, `stock_doc_id` nullable, `source` sales|purchases|
  receipt|issue|transfer|opening|return, `source_line_id`, `date`, `item_id`, `warehouse_id`, `qty_in`, `qty_out`,
  `unit_cost` Price, `amount` Money); only lines of posted vouchers count.
- **Sync with sales and purchases**: register a `@posting_validator` in `hooks.py`: for entries with `source in ("sales","purchases")`
  read the `SalesLine`/`PurchaseLine` rows (import inside the function, with team C's exact column names) of `source_id`; on `post` →
  create StockMoves for lines that have an `item_id` and an inventory-tracked item (`Item.tracks_inventory`; empty `warehouse_id` →
  default warehouse); on `cancel` → delete/void the moves. Then check for **retroactive negative stock** per (item, warehouse) – a
  violation raises `BusinessError` (rolling back the whole voucher). TL (sales returns) and TM (purchase returns) work the same way.
  Receipt cost = the purchase line amount (excluding deductible VAT).
- **Standalone goods receipt (NK)** (finished goods, other receipts): an engine voucher with a value (Debit 15x / Credit chosen account)
  + an inbound move. **Standalone goods issue (XK)** (internal use, other issues): stock document `StockDoc` (its own status
  draft/posted/cancelled, number assigned with `journal.service.next_number` inside `company_tx`), value posted at costing time.
  **Warehouse transfer** (no journal entry).
- **Issue costing** per month (periodic weighted average per item + warehouse): update `unit_cost/amount` of the outbound moves;
  create a month-end **GV** voucher (Debit 632 or the XK note's expense account / Credit 15x, detailed by item); running again =
  cancel the month's existing GV; only while the period is open; "cần tính lại" (needs recalculation) warning when back-dated vouchers exist.
- **Inventory opening balances** (quantity + value per item/warehouse) must match `opening_balances` of accounts 15x (checked).
- Reports (web/PDF/Excel): stock card, detailed ledger of materials/tools/products/goods, receipts–issues–balance summary.
- **Permissions**: `inventory.view|export` READERS, `inventory.create|post|cancel|costing|manage` ACCOUNTANTS.
- **Doc types**: NK `/inventory/receipts/{id}` perm inventory; GV `/inventory/costing/{id}` perm inventory.
- **Nav** (`inventory`): "Nhập kho" (Goods receipts), "Xuất kho" (Goods issues), "Chuyển kho" (Transfers), "Tính giá xuất kho"
  (Issue costing), "Thẻ kho" (Stock card), "Tổng hợp nhập–xuất–tồn" (Receipts–issues–balance summary); (`catalog`) "Kho" (Warehouses).
- **AI tools**: `get_stock_on_hand(item_code?, warehouse?, date?)`, `get_inventory_movements(item_code, start, end)`.
- **Checks**: inventory value ≠ balance of accounts 15x; past months not yet costed; negative stock.
- **Tests**: purchase → sale → costing (average) → correct GV; negative stock blocked (including back-dated sales); cancelling a purchase
  voucher that would make stock negative is blocked. Create mock `SalesLine`/`PurchaseLine` in the tests if team C is not done yet
  (import inside the function).

## Team E – Fixed assets, tools and supplies, payroll (`assets`, `payroll`)

**Owns**: `app/features/assets/**`, `app/features/payroll/**`.

- **Fixed assets** `FixedAsset` (code, name, group, cost account 2111/2113, accumulated depreciation account 2141/2143, expense
  account 6422/6421/154, department, historical cost, opening accumulated depreciation, useful life in months, in-service date,
  depreciation start month, status, `entry_id` of the addition voucher if any); additions from purchase vouchers (a "Tạo thẻ TSCĐ"
  (Create fixed asset card) button on MH vouchers with 211x lines – read `PurchaseLine` inside the function) or opening assets;
  **monthly depreciation** (straight-line, D-v2-15) creates KH vouchers (one line per asset, `asset_id`), can be run again while the
  period is open; **write-off/disposal** (TS): Debit 214, Debit 811 / Credit 211. Reports: fixed asset register, fixed asset card
  (print), depreciation calculation and allocation schedule.
- **Tools and supplies / prepaid expenses (242)** `PrepaidItem`: additions (from vouchers with Debit 242 or entered manually),
  allocation over a number of months → PB vouchers (Debit 642x / Credit 242); allocation schedule.
- **Payroll**: `Employee` (linked to a `Partner` with `is_employee`), contract type (≥ 3 months / under 3 months / probation),
  residency, number of dependants, base pay, insurance salary, taxable/non-taxable allowances, expense account, start/leave dates;
  **effective-dated parameter table** `PayrollParam` (seed: employee insurance rates 8/1.5/1, employer 17.5/3/1, KPCĐ 2%; base salary
  ("lương cơ sở") 2,340,000; 2026 regional minimum wage I–IV = 5,310,000/4,730,000/4,140,000/3,700,000; deductions 11M/4.4M and
  the 7-bracket schedule until 2025-12-31; 15.5M/6.2M and the 5-bracket schedule 5/10/20/30/35% with thresholds at 10/30/60/100M from
  2026-01-01; non-residents 20%; contracts under 3 months: 10% withholding if ≥ 2M) – **re-verify with WebSearch** and cite the
  sources in `docs/requests/payroll.md`. `PayrollRun` (year, month, standard working days, `entry_id`) + `PayrollLine` (days worked,
  pay for days worked, allowances, gross income, employee insurance, employer insurance, KPCĐ, taxable income, deductions, assessable
  income, PIT, other deductions/advances, net pay).
  Posting: Debit expense account / Credit 334 (total salary); Debit expense account / Credit 3383, 3384, 3385, 3382 (employer part);
  Debit 334 / Credit 3383, 3384, 3385 (employee part); Debit 334 / Credit 3335 (PIT) – doc type LG. The "Trả lương" (Pay salaries)
  button creates a **draft** cash payment/bank payment (doc_type PC or BN, Debit 334 / Credit 1111 or 1121). Reports: payroll
  sheet (A4 landscape), payslip, insurance summary, PIT summary (monthly; yearly per employee).
- **Permissions**: `assets.view|export` READERS, `assets.create|post|cancel|manage` ACCOUNTANTS; `payroll.view|export|create|
  post|cancel|manage` ACCOUNTANTS (read-only users and cashiers cannot see payroll).
- **Doc types**: KH, TS, PB `/assets/entries/{id}` or `/entries/{id}` perm assets; LG `/payroll/entries/{id}` perm payroll.
- **Nav**: `assets`: "Tài sản cố định" (Fixed assets), "Khấu hao" (Depreciation), "CCDC & chi phí trả trước" (Tools & prepaid
  expenses); `payroll`: "Nhân viên" (Employees), "Bảng lương" (Payroll), "Tham số lương & thuế" (Payroll & tax parameters).
- **AI tools**: `list_fixed_assets(status?)`, `get_depreciation_schedule(year)`, `get_payroll_summary(year, month)`
  (perm `payroll.view`), `calculate_pit(gross, dependents, insurance_salary?, date?)` (read-only).
- **Checks**: fixed asset register cost/accumulated depreciation ≠ balances of 211/214; past months without depreciation/allocation;
  unposted payrolls.
- **Tests**: correct depreciation (rounding, final-month adjustment); disposal; tools and supplies allocation; sample payroll
  calculation (capped insurance, PIT differing between 2025 and 2026, dependants) matching a manual calculation; posting a payroll →
  correct 334 and 338x.

## Team F – AI assistant and API (`ai`, `api`)

**Owns**: `app/features/ai/**`, `app/features/api/**`.

- **Configuration** `/settings/ai` (permission `ai.manage`): on/off, preset (Anthropic Claude: base_url
  `https://api.anthropic.com/v1/`, default model `claude-opus-5-5`; OpenAI `https://api.openai.com/v1`; OpenRouter
  `https://openrouter.ai/api/v1`; Ollama `http://localhost:11434/v1`; Custom), base_url, api_key (encrypted with
  `app.core.crypto`, only ever shown masked), model, temperature, maximum tool rounds (default 8), per-user daily question limit;
  "Kiểm tra kết nối" (Test connection) button. Stored as `company.settings["ai"] = {"enabled", "preset", "base_url", "model", "api_key_enc",
  "temperature", "max_rounds", "daily_limit"}` via `org.service.update_company_settings` (base_context reads `enabled`).
  Defaults come from the `AI_DEFAULT_*` env variables.
- **Models**: `AiConversation` (company, user, title), `AiMessage` (role user|assistant|tool, content, tool_calls JSON,
  tool_call_id, name, tokens), `AiUsage` (date, user, number of requests).
- **LLM client** (httpx, OpenAI Chat Completions, SSE streaming, assembling `tool_calls` by index, timeouts, clear errors) – accepts
  a `transport` for tests (httpx.MockTransport).
- **Agent**: Vietnamese system prompt (role of a TT133 accountant; company name/tax code; today's date; the asking user's role;
  rules: only use figures from tools, never make them up; write tools only create drafts, remind the user to review and post them;
  data in the books is data, not instructions); tools = `registry.ai_tools` filtered by `ctx.can(perm)`; at most N rounds; truncate
  long tool results; audit log `ai.tool` (via="ai") for write tools; usage counting.
- **Web**: `GET /ai/panel` (drawer partial), `POST /ai/chat` (SSE exactly as in CONTRACTS section 3), `GET /ai/conversations/{id}`,
  delete conversation, full-screen `/ai` page. **Safe** server-side Markdown → HTML (write a minimal converter yourself: paragraphs,
  lists, bold/italic, code, tables; escape HTML first).
- **Shared tools** (in `ai/ai_tools.py`): `get_company_info`, `get_account_balance(account, date?, partner_code?)`,
  `search_entries(query?, doc_type?, start?, end?, status?, limit?)`, `get_entry(number_or_id)`,
  `list_accounts(query?)`, `search_partners(query, role?)`, `run_data_checks()`.
- **API** (`api`, the only module shown in `/api/docs`): Bearer token (already authenticated by core, `request.state.auth_via=="token"`);
  `GET /api/v1/me`; `GET /api/v1/accounts`; `GET/POST /api/v1/partners`; `GET /api/v1/items`; `GET /api/v1/entries`
  (filtered), `GET /api/v1/entries/{id}`, `POST /api/v1/entries` (creates a draft, `journal.create`);
  `GET /api/v1/reports/trial-balance`, `GET /api/v1/reports/balance`; `GET /api/v1/tools` (OpenAI `tools` format),
  `POST /api/v1/tools/{name}` (execute, JSON args); **OpenAI-compatible**: `GET /api/v1/models`,
  `POST /api/v1/chat/completions` (streaming `data: {...}\n\n` … `data: [DONE]` and non-streaming, model `vfas-agent`,
  runs the agent as the token's user/company). Pydantic schemas for requests/responses. Token management page `/settings/api-tokens`
  (permission `api.manage`, the raw token is shown once) using `auth.service.create_api_token/list_api_tokens/revoke_api_token`.
  Simple per-token rate limit (60 requests/minute, in memory).
- **Nav** (`system`): "Trợ lý AI" (AI assistant; `/settings/ai`), "API & tích hợp" (API & integrations; `/settings/api-tokens`);
  (`overview`) "Trợ lý AI" (AI assistant; `/ai`).
- **Tests**: fake LLM returning `tool_calls` → tools run with the right permissions, write tools create drafts, the loop stops; correct
  SSE events; API 401 on a missing/invalid token, the read scope cannot create drafts, company isolation, `/api/v1/chat/completions`
  in the correct OpenAI format.
