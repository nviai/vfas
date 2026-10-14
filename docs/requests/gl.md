# Team A (gl) – reference sources, open questions, requests to the core

## Sources for line codes (Circular 133/2016/TT-BTC)

| Form | Source | Notes |
|---|---|---|
| B01a-DNN | [ketoanthienung.net – cách lập Báo cáo tình hình tài chính TT133](https://ketoanthienung.net/cach-lap-bao-cao-tinh-hinh-tai-chinh-theo-thong-tu-133.htm) (how to prepare the Statement of financial position); [hoatieu.vn](https://hoatieu.vn/bieu-mau/cach-lap-bang-can-doi-ke-toan-theo-thong-tu-133-3225) | Codes 110–182, 200 (total assets), 300–320, 400–417, 500 (total liabilities and equity). **Check 200 = 500** (not 270/440 as in TT200). |
| B02-DNN | [ketoanthienung.net – lập Báo cáo KQHĐKD TT133](https://ketoanthienung.net/cach-lap-bao-cao-ket-qua-hoat-dong-kinh-doanh-theo-thong-tu-133.htm) (preparing the Income statement); [hoatieu.vn](https://hoatieu.vn/bieu-mau/mau-bao-cao-ket-qua-hoat-dong-kinh-doanh-cua-cong-ty-74435) | 01, 02, 10, 11, 20, 21, 22, 23, 24, 30, 31, 32, 40, 50, 51, 60. Formula 30 = 20 + 21 − 22 − 24. |
| B03-DNN | [ketoanthienung.net – lập Báo cáo lưu chuyển tiền tệ TT133](https://ketoanthienung.net/cach-lap-bao-cao-luu-chuyen-tien-te-theo-thong-tu-133.htm) (preparing the Cash flow statement) | Direct method: 01–07, 20; 21–25, 30; 31–35, 40; 50, 60, 61, 70. |
| F01-DNN | [ketoanthienung.net – Bảng cân đối tài khoản TT133](https://ketoanthienung.net/cach-lap-bang-can-doi-tai-khoan-theo-thong-tu-133.htm) (Trial balance); [hoatieu.vn](https://hoatieu.vn/bieu-mau/mau-so-f01-dnn-bang-can-doi-tai-khoan-theo-thong-tu-133-144175) | Columns A, B, 1–6; "Tổng cộng" (Grand total) row. |
| S03a-DNN, S03b-DNN | [thuvienphapluat.vn – mẫu sổ Nhật ký chung TT133](https://thuvienphapluat.vn/hoi-dap-phap-luat/tai-mau-so-nhat-ky-chung-theo-thong-tu-133-138043351.html) (General journal template); column layout per the equivalent TT200/QĐ48 template | Columns A–H, 1–2; "Sổ này có … trang" (This book has … pages), "Ngày mở sổ" (Book opening date). |

## Open questions / team decisions (proposed for DECISIONS)

1. **B01a code 110**: only 111 + 112. Term deposits ≤ 3 months (detail accounts 1281/1288) cannot be distinguished yet, so they go to 122. Splitting them requires detail accounts or a "cash equivalent" flag.
2. **B01a – unclosed balances**: unclosed balances of class 5–9 accounts are added to **417** (so the statement always balances) and a warning is shown.
3. **B01a – dual-nature accounts**: split by partner for accounts tracked by partner (131, 331, 141, 334, 338x, 1388…); accounts not tracked by partner (333x…) are split by detail account. Line mapping rule: longest account prefix + balance side (`statements.B01A_RULES`).
4. **F01 – dual-nature accounts**: dual-nature accounts tracked by partner show debit/credit balances per partner on both sides; other accounts show the net balance. The guidance found does not say clearly.
5. **B02**: figures come from movements **excluding closing (KC) vouchers** (equivalent to "contra 911"). Line 23 (interest expense) comes from 635 detail accounts whose name contains "lãi vay" (interest) – if none exist it is left blank with a warning. Fixed asset disposals are not yet separated from 31/32 (the law only takes the net gain/loss).
6. **B03**: classified by the contra account of the cash line (`B03_IN`, `B03_OUT`), overridable via `journal_lines.cash_flow`. Payments to 331 default to **02**; paying debts for fixed asset purchases through 331 requires overriding code 21 on the line (cannot be inferred from the voucher). 515 → 25. Internal transfers 111 ↔ 112 are ignored.
7. **Closing entries**: close the full balance of class 5–8 accounts up to the period end date (including unclosed balances from earlier periods); re-running a period = cancelling that period's previous closing (KC) vouchers, only while the next period has no closing yet. 611/631 are not closed automatically (warning). Close 4212 → 4211 on 01/01 of the following year, once per year.
8. **B09**: pre-filled template (business characteristics, policies, tables V.01–V.08, VI.01–VI.06); narrative sections are left for the user to edit in the exported file.

## Reuse from other modules

`from app.features.gl.entries import entry_context` → pass `**entry_context(db, ctx, entry)` to the template, then:
`{% include "gl/_entry_lines.html" %}`, `{% include "gl/_attachments.html" %}`, `{% include "gl/_history.html" %}`,
and in the `page_header` caller: `{% with edit_url=…, print_url=…, copy_url=… %}{% include "gl/_entry_actions.html" %}{% endwith %}`
(the post / cancel / adjust / edit description / delete draft buttons use the core endpoints `/entries/{id}/…`).
Common print template for every voucher: `GET /entries/{id}/print.pdf` ("PHIẾU KẾ TOÁN", general journal voucher).

## Requests to the core

1. `ui.line_grid` has only one "Đối tượng" (Partner) column and no bank account column; the gl team wrote its own `gl/_pk_grid.html` (copying
   `_grid_combo` because private macros cannot be imported). If the core wants it shared: add a `columns` parameter to `line_grid`
   (separate debit/credit partner, bank account) or drop the `_` so the `grid_combo` macro can be imported.
2. Inside `.paper` (always light background) the `.doc-no` class uses the dark-mode text color → almost white in dark mode. gl avoids using
   `.doc-no` inside `.paper`; the core CSS should be fixed: `.paper .doc-no { color: inherit }`.
3. Period names in `period_selector` show the month according to the browser locale (`<input type="month">`) – no business impact.
4. Suggestion: `test_i18n` (integration step) should also scan `tr("…")` inside macros (gl uses it in `_pk_grid.html`).
