# Issuing e-invoices through a provider – research and design (14/10/2026)

Status: **not implemented** (currently only invoice XML import exists). This document is the basis for the work.
Confidence: [VB] read in the original legal text · [NCC] official provider documentation · [TP3] third-party source · [SL] inference.

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

## 3. Proposed design
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
