# Team E (fixed assets, payroll) – parameter sources, open questions, requests to the core

## 1. Default payroll & tax parameters (`app/features/payroll/params.py::DEFAULTS`)

Verified via web searches on 14/10/2026 (secondary sources: press, advisory sites, local government portals; **not yet checked
against the original texts in the Official Gazette** – users can edit them at `/payroll/settings` and add new values by effective date).

| Parameter | Value | Effective | Legal basis | Confidence |
|---|---|---|---|---|
| Family deductions | 15,500,000 / 6,200,000 VND/month | 01/01/2026 (tax year 2026) | Resolution 110/2025/UBTVQH15 | high (many sources, incl. a Ministry of Finance Q&A) |
| Previous family deductions | 11,000,000 / 4,400,000 | 01/07/2020 | Resolution 954/2020/UBTVQH14 | high |
| 5-bracket tax schedule | ≤ 10M 5%; 10–30M 10%; 30–60M 20%; 60–100M 30%; > 100M 35% | 01/01/2026 | PIT Law 109/2025/QH15 – the law takes effect 01/7/2026, but salaries/wages of resident individuals apply from tax year 2026 | medium–high (one source gives 15%, 25% for brackets 2, 3; most give 10%, 20%) |
| 7-bracket tax schedule | 5/10/15/20/25/30/35% with thresholds 5/10/18/32/52/80M | until 31/12/2025 | PIT Law 2007, amended 2012 | high |
| Insurance rates | Employee 8 / 1.5 / 1; employer 17.5 / 3 / 1; trade union fee 2% | 2026 (unchanged) | Social Insurance Law 2024, Decree 158/2025, trade union fee: Decree 191/2013 | high (trade union fee may be reduced to 1.6% for up to 6 months under Decree 105 – not a default) |
| Base salary (reference level) | 2,340,000 → **2,530,000** | 01/07/2024 → **01/07/2026** | Decree 73/2024 → Decree 161/2026/ND-CP (15/5/2026) | medium–high |
| Social/health insurance cap | 20 × base salary (46.8M → 50.6M from 01/7/2026) | | Social Insurance Law 2024 | high |
| Regional minimum wage | I 5,310,000; II 4,730,000; III 4,140,000; IV 3,700,000 | 01/01/2026 | Decree 293/2025/ND-CP | high |
| Previous regional minimum wage | 4,960,000 / 4,410,000 / 3,860,000 / 3,450,000 | 01/07/2024 | Decree 74/2024/ND-CP | high |
| Unemployment insurance cap | 20 × regional minimum wage | | Employment Law | high |
| 10% withholding (contracts < 3 months, no contract) | threshold 2,000,000 → **5,000,000** VND/payment | → 01/07/2026 | Circular 111/2013 → Decree 253/2026/ND-CP, Article 50(2) | medium (one primary source) |
| Non-residents | 20% of taxable income | | PIT Law | high |

Sources used: KPMG Vietnam (Resolution 110/2025), thuvienphapluat.vn, hcc.nghean.gov.vn, hatinh.gov.vn, EY Tax Alert 05/26,
easyhrm.vn, thue.man.net.vn, sme.misa.vn, amis.misa.vn.

## 2. Calculation conventions (documented in `payroll/calc.py`)
- Parameters apply as of the **last day of the payroll month** (not the payment date). The Ministry of Finance's guidance is that December 2025 salaries paid
  in January 2026 belong to tax year 2026 – in that case the accountant prepares the payroll with a suitable date or adds a manual adjustment.
- Insurance is computed on the full month's insurable salary, **not prorated by working days**; the 2% trade union fee is on the capped social insurance salary.
- The 10% withholding test uses the month's total taxable income on the payroll (other payments are not aggregated).
- Tax-exempt income consists only of the "phụ cấp không chịu thuế" (non-taxable allowance) entered in the employee profile; caps for meal and phone allowances are not checked automatically.
- Each amount is rounded to the dong (ROUND_HALF_UP).

## 3. Forms
- Fixed asset register **S09-DNN**, fixed asset card **S11-DNN**, depreciation calculation and allocation table **06-TSCĐ**, payroll payment sheet
  **02-LĐTL** – per advisory sources (MISA, ketoanleanh, hoatieu), not yet checked against the original TT133 appendix.
- Tools and supplies/prepaid expense allocation table, payslips: no form code shown (not mandatory).

## 4. Requests / notes for the integration step
1. **FKs to `journal_entries`** have no `ON DELETE`: `fixed_assets.entry_id/disposal_entry_id`, `prepaid_items.entry_id`,
   `asset_runs.entry_id`, `payroll_runs.entry_id/payment_entry_id`. The modules clear the references themselves via the `entry.deleted` hook
   (`assets/hooks.py`, `payroll/hooks.py`). The baseline migration keeps these FKs as they are.
2. **Salary payment vouchers** create **draft** `PC` (Debit 334 / Credit 1111) or `BN` (Debit 334 / Credit 1121) vouchers, `source="payroll"`,
   `meta.kind="salary_payment"` – the detail pages of the cash/bank modules must be able to display them (as per MODULES.md).
3. Print templates use a dedicated layout `assets/templates/print/_layout.html` (copied from `core/templates/print/tt133/_layout.html`) so as
   not to depend on the old print folder that will be deleted; if the core moves a shared print layout into core, change `extends` in 6 files.
4. Icons missing from `icons.html`: `play`, `pause`, `hourglass` (replaced with `zap`, `ban`, `clock`).
5. Suggestion for Ctrl+K (`/search`): add fixed assets (`/assets/{id}`) and employees (`/payroll/employees/{id}`, only with the
   `payroll.view` permission).
6. New data checks: `assets_vs_ledger`, `prepaid_vs_ledger`, `depreciation_missing`, `allocation_missing`,
   `payroll_unposted` – the dashboard can use them as "to-do" items.
