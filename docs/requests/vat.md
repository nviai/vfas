# Team C – Sales and purchases, tax, receivables/payables: business notes, sources, open questions, requests to the core

## Business decisions

| # | Issue | Approach | Rationale / notes |
|---|---|---|---|
| C1 | VAT filing period | Based on the **posting date** of posted vouchers (not the invoice date) | Matches the books; input invoices declared late are posted in the period in which they are declared. Stated clearly on the listings. |
| C2 | 8% VAT rate (Decree 174/2025) | Declared in [32]/[33] together with the 10% group, with the actual 8% tax amount; prepare the tax-reduction appendix (Form 01, Appendix III, Decree 174/2025) with reduced tax = value × 2% | Current guidance while the tax reduction applies. The "KHAC" rate (other than 0/5/8/10) is provisionally declared in [32]/[33] so nothing is left out. |
| C3 | [23]/[24] purchases | Also include **non-deductible** invoices; [25] only the deductible part | Per the 01/GTGT line instructions ([23] total value of goods and services purchased, [25] the part eligible for deduction). |
| C4 | Returns / price reductions | Stored as a positive VatRecord in group `adjust_down`; the return and the listings subtract it | The engine does not allow negative amounts in vat_records. |
| C5 | [37]/[38] | [37] = **decrease** adjustment of deductible tax from previous periods (increases the amount payable), [38] = **increase** adjustment; formula [40a] = [36] − [22] + [37] − [38] − [39a] | Some articles name them the other way round; the formula on the return is authoritative. |
| C6 | [22] | Takes [43] from the immediately preceding period's return saved in the software (latest version); manual entry allowed when the previous period was not prepared here | |
| C7 | Reopening a return | A previous period cannot be reopened once the next period is finalized (the next period uses its [43]), nor once it has supplementary returns; corrections after filing go through a supplementary return (D-v2-33) | Avoids mismatched carried-forward amounts. |
| C8 | Corporate income tax (CIT) | Reference worksheet for the main 03/TNDN lines (A1, B1, B8, B12, C2–C9, D, E, F) – the line codes are internal codes, NOT the codes on the official return; the return to be filed is prepared in HTKK | Rates per Law 67/2025/QH15 (15% for ≤ 3 billion, 17% for ≤ 50 billion prior-year revenue, otherwise 20%); can be chosen manually (subsidiaries/affiliates that do not qualify). |
| C9 | Revenue and expenses for CIT / AI | Movements on accounts 511/515/711/632/635/642/811 **excluding closing (KC) vouchers** | Closing entries zero out the balances. |
| C10 | Debt aging | FIFO per partner; lines of the same voucher (goods amount + VAT) are combined into one item; due date = `due_date` of the purchase/sales document or voucher date + `payment_terms_days` | D-v2-17. |
| C11 | XML invoices | Trade discounts (TChat=3) are deducted from lines with the same VAT rate; note lines (TChat=4) are ignored; VAT rounding differences (≤ 10 VND or 2 VND/line) are pushed to the last line according to the `LTSuat` summary table; duplicate detection by seller tax code + template number + series + number (leading zeros stripped) | Digital signatures are not verified (stated clearly in the UI). |
| C12 | Filing calendar | Monthly VAT/PIT: 20th of the following month; quarterly: last day of the first month of the next quarter; provisional CIT: 30th of the first month of the next quarter; CIT finalization + financial statements: last day of the 3rd month after year end. Saturday/Sunday deadlines move to Monday (public holidays not yet handled). Deadlines more than 45 days past are not shown as reminders | Tax Administration Law 2019, Decree 126/2020. |
| C13 | Vietnamese number format | Manually entered price fields (item catalog, Excel import) read "500.000" as five hundred thousand (`parse_vn_decimal`); machine-generated values from the JS grid read "1.5" as 1.5 (`parse_decimal`) | Avoids confusing 1.125 from JS (one point one two five) with Vietnamese-format 1.125 (one thousand one hundred twenty-five). |

## HTKK XML (needs verification)
- The `HSoThueDTu/HSoKhaiThue/TTinChung + CTieuTKhaiChinh/ctNN` structure follows commonly seen HTKK export files; **no official XSD could be found** to check against. `maTKhai = 842`, `pbanTKhaiXML = 2.7.1` and the names of the sub-groups (`GiaTriVaThueGTGTHHDVMuaVao`, `HHDVBRaChiuTSuat10`…) are unverified. They are isolated in `app/features/vat/htkk.py` (constants `MA_TKHAI`, `PBAN_XML`, `LAYOUT`) for quick fixes once a real HTKK sample file is available. The UI states clearly "mở bằng HTKK để kiểm tra trước khi nộp" (open in HTKK to check before filing).
- Current 01/GTGT form: Circular 80/2021 as amended by Circular 40/2025 (applies from tax year 2026) – advisory articles mention no changes to lines [21]–[43]; the changes are mainly in [09]–[11] (administrative area).

## References
- MISA – 01/GTGT return form, line formulas: https://sme.misa.vn/345655/to-khai-01-thue-gtgt/
- Thư viện pháp luật – line [36]: https://thuvienphapluat.vn/ma-so-thue/phap-luat-thue/huong-dan-dien-chi-tieu-36-tren-to-khai-thue-gtgt-mau-so-01gtgt-223680.html
- MISA – tax-reduction appendix under Decree 174/2025: https://sme.misa.vn/348277/phu-luc-giam-thue-gtgt/ ; https://sme.misa.vn/345453/nghi-dinh-174-ve-giam-thue-gtgt/
- Thư viện pháp luật – VAT reduction in 2026: https://thuvienphapluat.vn/ma-so-thue/phap-luat-thue/nam-2026-con-ap-dung-muc-thue-suat-thue-gtgt-8-hay-khong-989951-218063.html
- Decision 1450/QĐ-TCT (e-invoice data components): https://thuvienphapluat.vn/van-ban/thue-phi-le-phi/quyet-dinh-1450-qd-tct-2021-thanh-phan-chua-du-lieu-nghiep-vu-hoa-don-dien-tu-490526.aspx (full text not accessible – XML tag names are based on the common HDon structure: TTChung/KHMSHDon/KHHDon/SHDon/NLap, NDHDon/NBan/NMua/DSHHDVu/HHDVu/TToan/THTTLTSuat/LTSuat)
- CIT Law 67/2025/QH15 – 15%/17% rates: https://amis.misa.vn/246961/ap-dung-thue-suat-15-voi-doanh-nghiep-doanh-thu-duoi-3-ty/

## Requests / notes for the core and other teams
1. **Contract for team D (inventory), E (fixed assets)**: `SalesLine` (sales_lines) and `PurchaseLine` (purchase_lines) have exactly the columns in MODULES.md; `entry.source in ("sales", "purchases")`, `entry.source_id = SalesDoc.id / PurchaseDoc.id`; TL/TM (sales/purchase returns) share the same tables with `doc.kind == "return"`, `entry.doc_type` TL/TM.
2. **Adjusting vouchers** (`POST /entries/{id}/reverse`) for BH/MH create an entry with `source="sales"/"purchases"` but **without** a SalesDoc/PurchaseDoc (`source_id=None`). Team C's detail pages can display it (entry lines only); the inventory team's validator must skip it when `source_id` is empty.
3. **Dashboard**: use `app.features.vat.calendar.deadlines(company, today, db)` → list of `{title, label, due, days, period, url, status ('pending'|'submitted'), kind}`.
4. Shared helpers that could move into the core later: `app/features/sales/common.py::resolve_view_period` (month/quarter/year/custom period per `ui.period_selector`), `parse_vn_decimal`, `parse_money`.
5. The partner detail page (the core's `partners/detail.html`) already links to `/debts/partner/{id}` – the route exists.
