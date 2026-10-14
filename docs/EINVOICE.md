# Issuing e-invoices through a provider

Status: **implemented** (module `app/features/einvoice`, October 2026) for Viettel S-Invoice, MISA meInvoice, VNPT
Invoice and BKAV eHoadon, plus a simulator for demos and tests. This document records the legal research, the design and
what was verified against which source.
Confidence: [VB] read in the original legal text · [NCC] official provider documentation or WSDL · [CODE] working
open-source client · [TP3] third-party source · [SL] inference.

## 0. How it works

1. **Settings → Hóa đơn điện tử** (`/einvoice/settings`, owner/chief accountant): provider, environment (test or
   production), API account (secrets encrypted with `SECRET_KEY`), template number (1 = VAT invoice, 2 = sales invoice)
   and series (`C26TAA`, a new one every year), "post the voucher after issuing".
2. On a **draft** sales voucher the "Hóa đơn điện tử" card offers **Phát hành HĐĐT**. The invoice is dated today
   (never back-dated) and built from the voucher lines; its number is written to the voucher and its VAT lines before
   posting, because posted vouchers and VAT lines are immutable.
3. A draft **sales return/allowance** (TL) whose original sale was issued here produces a **decrease adjustment
   invoice** (Viettel, BKAV; MISA and VNPT do not publish how to express a decrease, so it is refused with a message).
4. Every attempt is an outbox row (`einvoice_issues`) written **before** the provider is called, with a fresh
   idempotency key (`transactionUuid` / `RefID` / `Fkey` / `PartnerInvoiceStringID`). A timeout leaves the row
   `unknown`; it is looked up by key, never sent blindly again. Refusals (`failed`) can be fixed and issued with a new
   key. Invoices without the tax authority code yet (`signed`) are polled with backoff: `python -m app.cli
   einvoice-poll` from cron, or **Kiểm tra trạng thái** on the voucher.
5. Once the code is assigned the signed XML is downloaded and kept under `DATA_DIR/attachments/einvoice/` with its
   SHA-256 (it is the legal original); PDF/XML are also downloadable on demand.
6. An issued invoice locks its voucher: no edit, delete or cancel (corrections go through an adjustment invoice). A tax
   authority refusal (`failed` after signing) unlocks it again.

| Provider | Protocol | Issue | Lookup / tax code | Adjustment | Evidence |
|---|---|---|---|---|---|
| Viettel S-Invoice | REST/JSON, cookie token | `createInvoice` (HSM) | `searchInvoiceByTransactionUuid` | ✅ type 5, decrease lines | [CODE] Odoo `l10n_vn_edi_viettel` field names, Viettel sample DTOs, a 2026 production plugin |
| MISA meInvoice | REST/JSON v3, Bearer + `CompanyTaxCode` | `publishhsm` | status by RefID / TransactionID | ❌ not documented | [NCC] doc.meinvoice.vn; no open-source client |
| VNPT Invoice | SOAP, per-customer host | `ImportAndPublishInv` (HSM accounts) | `listInvByCusFkey`, `GetMCCQThueByFkeys` | ❌ TT78 XML not published | [NCC] WSDL, [CODE] two clients + VNPT's add-in |
| BKAV eHoadon | SOAP `ExecCommand`, gzip + AES-256-CBC | CmdType 112 + 205 (HSM sign) | 800 / 850 | ✅ CmdType 124 | [NCC] WSDL + Bkav guide, [CODE] four clients |
| Simulator | in process | – | – | ✅ | only with `EINVOICE_SIMULATOR=true`, no legal value |

**Before production:** none of the adapters could be run against a provider's test account while writing them (no
credentials). Issue test invoices in the provider's test environment and compare them on the provider's site. Known
open points: MISA's flag for the 8% rate in 2025–2026 (`IsTaxReduction43` is sent), the negative tax-rate codes of
VNPT/BKAV (KCT = −1, KKKNT = −2; Viettel uses the opposite), and whether VNPT refuses a repeated Fkey. Not implemented:
replacement invoices, form 04/SS (no provider offers a documented API), USB-token/SmartCA signing (HSM only).

## 1. Legal framework (changed from 01/7/2026)
- **Decree 254/2026/ND-CP** and **Circular 91/2026/TT-BTC** (issued 30/6/2026, effective 01/7/2026) replace Decree 123/2020, Decree 70/2025,
  Circular 32/2025 [VB].
- SMEs use **invoices with a tax authority code** (letter C) [VB/SL]. The seller creates and digitally signs the invoice on the service provider's
  site **or in its own software**, and sends it through the service provider for the Tax Department to assign a code (Article 12.1.b, Decree 254) [VB]
  → accounting software may call the provider's API **using the customer's own account/contract**; the seller bears legal
  responsibility for the invoice.
- If the software only calls the provider's API (the provider builds the XML, signs with an HSM, transmits to the Tax Department), no rule was found requiring the software to register with
  the Tax Department [SL – needs confirmation from a tax adviser]. Building the XML ourselves and only hiring a T-VAN for transmission would most likely require being a "solution
  provider" ("tổ chức cung cấp giải pháp") under Article 12.1, Circular 91 (≥ 5 IT staff, testing with a T-VAN, filing with the Tax Department).
- A direct connection to the Tax Department is only for invoices without a code at ≥ 1 million invoices/month → not suitable.
- The business's own digital signature; the certificate used for signing must be listed on form 01/ĐKTĐ-HĐĐT; remote signing (HSM/remote signing) is valid (Decree 23/2025).
- **Handling errors (Article 10, Circular 91)** [VB]: errors not affecting amounts/tax → notice on form 04/SS-HĐĐT; wrong tax code,
  goods, amounts, tax rate → **adjustment** or **replacement** (a buyer that is an organization needs a written agreement), later corrections keep
  the method chosen; returned goods → adjustment invoice. The new form 04/SS **no longer has "Hủy" (Cancel)** → invoices that already have a code cannot be cancelled.
- Symbols: the template code is 1 digit (1 = VAT invoice, 2 = sales invoice…); the series has 6 characters, e.g. `C26TAA` (C/K, 2-digit year, type T/D/L/M/…, 2 letters of
  your choice) → a new series is needed each year; the invoice number has ≤ 8 digits, restarts at 1 every year and increases continuously per series [VB].

## 2. Providers

| Provider | API | Authentication | Digital signing | Deduplication | Test |
|---|---|---|---|---|---|
| MISA meInvoice [NCC] | REST/JSON, public documentation at doc.meinvoice.vn | `appid` (issued by MISA) + tax code + user/pass → token valid 15 days | One-step HSM (`publishhsm`, SignType 2/3) or USB token via SignedService | `RefID` (duplicates still return `Success=true`, read the error code) | testapi.meinvoice.vn |
| Viettel S-Invoice [TP3] | REST/JSON | `/auth/login` → access_token | HSM within a single `createInvoice/{MST}` call; USB token via get-hash/insert-signature | `transactionUuid` (3 days) | yes, test tax code |
| VNPT Invoice [TP3] | SOAP | Account/ACpass + user/pass | getDigest/Confirm; SmartCA | `Fkey` | documentation on registration |
| BKAV eHoadon [TP3] | SOAP, gzip + AES payload | PartnerGUID/Token | HSM/USB | `PartnerInvoiceID` | wsdemo |
| EasyInvoice, FPT, M-Invoice, CyberBill, Thái Sơn | API available, documentation not public | | | | |

None of the providers offers webhooks in the documentation reviewed → status must be polled. All require partner registration/agreements
(appid, partner key, web service user). MISA is also a competing accounting software vendor – risk of not being granted an `appid` [SL].

## 3. Design (as implemented)
- `EInvoiceProvider` interface: `authenticate`, `list_series`, `issue(canonical, idem_key)`, `get_status`,
  `download(xml|pdf)`, `adjust`, `replace`, `send_error_notice_04ss`; capability flags `hsm_sync`, `hsm_async`, `usb_hash`, `batch`.
- `CanonicalInvoice` built from the sales document (`SalesDoc`); each adapter maps it to its own payload.
- Per-company configuration: provider, account (encrypted like the AI API key), default template/series; token PINs are never stored.
- States: DRAFT → QUEUED → SUBMITTED → SIGNED → WAITING_CQT → ISSUED (REJECTED/UNKNOWN); polling worker with backoff
  (5s, 15s, 1', 5'… ≤ 24h); the invoice is sent to the buyer only once it has the Tax Department code.
- **Idempotency**: generate the key and write an outbox table row before calling the API; unique (company, provider, key); on timeout, look up
  by key before resending; serialize per (tax code, series) and do not allow back-dating invoices.
- Store the signed XML with the Tax Department code (the legal original) + PDF + raw request/response, with SHA-256; reuse the existing `HDon` parser to
  check the returned XML; write the series/number/Tax Department code to the sales document and `VatRecord`.
- Adjustment/replacement creates a new document linked to the original invoice (consistent with the existing TL (sales return) workflow).
- Order of work: Viettel S-Invoice or MISA meInvoice (depending on the customer's contract) → VNPT/BKAV (SOAP, `zeep`).

## Sources
Decree 254/2026: https://datafiles.chinhphu.vn/cpp/files/vbpq/2026/7/254-ndcp.signed.pdf ·
Circular 91/2026 (republished copy): https://www.misaeshop.vn/wp-content/uploads/2026/07/TT91.pdf ·
EY: https://www.ey.com/vi_vn/technical/tax/tax-and-law-updates/nghi-dinh-254-2026-nd-cp-ve-hoa-don-dien-tu-va-chung-tu-dien-tu ·
MISA meInvoice API: https://doc.meinvoice.vn/api/ · Viettel S-Invoice: https://www.tlptech.vn/tai-lieu-mo-ta-webservice-hoa-don-dien-tu-viettel-api ·
Viindoo + S-Invoice: https://viindoo.com/documentation/15.0/applications/finance/accounting-and-invoicing/fiscal-localizations/issue-e-invoice-by-integrating-viindoo-with-viettel-s-s-invoice-service.html ·
BKAV: https://github.com/phanmn/ex_bkav_invoice · SePay eInvoice: https://developer.sepay.vn/en/einvoice-api/v1/tong-quan
