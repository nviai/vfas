# Requests from team D (inventory – `inventory`)

## To the core / integration step
1. The **baseline migration** must include the tables: `warehouses`, `stock_docs`, `stock_doc_lines`, `stock_moves`, `costing_runs`.
2. **Company setting** `company.settings["inventory"] = {"allow_negative_stock": bool}` (off by default) – edited at
   `/inventory/warehouses` (permission `inventory.manage`). The company Settings page may show it too if desired.
3. Goods issue notes (XK) and stock transfers (DC) are **not JournalEntry records**: numbers are assigned via `journal.service.next_number`
   (series `XK`, `DC`) inside `company_tx`; duplicate numbers are only checked in `journal_entries` – no impact, because no
   engine voucher uses the XK/DC prefixes. If prefixes become configurable later, the `stock_docs` table should be checked as well.
4. The dashboard could add a "Hàng tồn kho" (Inventory) card = `inventory.service.on_hand(...)` (sum of `amount`), and a to-do item
   "Tháng cần tính giá xuất kho" (Months needing stock costing) = `inventory.costing.months_status(...)` (`needs_run`).
5. TT133 inventory book codes used: **S06-DNN** detailed ledger of materials, tools (products, goods) (source: MISA
   amis.misa.vn/49401), **S07-DNN** detailed summary table…, **S08-DNN** stock card (sources: hoatieu.vn, ketoanleanh.edu.vn);
   notes **01-VT** goods receipt, **02-VT** goods issue. Should be checked against the original TT133 text before release.

## To team C (sales and purchases)
1. Inventory sync runs through `@posting_validator` for entries with `source in ("sales","purchases")`, doc_type BH/TL (sales) and
   MH/TM (purchases), reading `SalesLine`/`PurchaseLine` by `doc_id = entry.source_id` – verified by an integration test against the real
   models (`tests/inventory/test_inventory.py::test_real_sales_purchases_modules`).
2. A purchase line only goes into stock when `debit_account` starts with `15`; lines with `deductible=False` add the VAT amount to the purchase cost.
3. The purchase/sales forms have no **per-line warehouse** field yet: empty `warehouse_id` → default warehouse. A (optional) warehouse column should be added
   when the company has > 1 warehouse (`inventory.service.list_warehouses`).
4. Selling goods with no stock on hand is blocked (negative stock). Items not tracked in stock should use the "Dịch vụ" (Service) type, or enable
   "Cho phép xuất âm kho" (Allow negative stock). The Items ("Hàng hóa") page should mention this.
