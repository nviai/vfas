# VFAS user guide

- [1. Roles and permissions](#1-roles-and-permissions)
- [2. AI assistant](#2-ai-assistant)
- [3. API and OpenAI-style integration](#3-api-and-openai-style-integration)
- [4. Keyboard shortcuts](#4-keyboard-shortcuts)
- [5. Tax returns for HTKK](#5-tax-returns-for-htkk)
- [6. Accounting regimes](#6-accounting-regimes)

## 1. Roles and permissions

| | Owner / Director | Chief accountant | Accountant | Cashier | Viewer |
|---|---|---|---|---|---|
| View books, reports | ✓ | ✓ | ✓ | cash book | ✓ |
| Create, post, cancel vouchers | ✓ | ✓ | ✓ | cash receipts/payments | |
| Payroll, employees' personal data | ✓ | ✓ | ✓ | | |
| Opening balances, period locks | ✓ | ✓ | | | |
| Members, settings, AI, API tokens | ✓ | ✓ | | | |

Role names in the Vietnamese UI: "Chủ DN / Giám đốc", "Kế toán trưởng", "Kế toán viên", "Thủ quỹ", "Chỉ xem".

- Users without payroll permission see the ledgers of accounts 334/3335 aggregated per voucher (totals unchanged); they do not see
  individual salaries, ID card (CCCD) numbers or employees' bank account numbers.
- Platform administrators (superadmin) manage companies and users at `/admin` but **cannot see the books** unless they are
  members of the company.
- Every action is recorded in the **audit log** ("Nhật ký thao tác", cannot be deleted), stating who did it and whether via the web, the API or the AI assistant.

## 2. AI assistant

"Hệ thống → **Trợ lý AI**" (System → AI assistant): enable it, choose a provider, enter the API key (stored encrypted), click "Kiểm tra kết nối" (Test connection).

| Preset | Base URL | Suggested model |
|---|---|---|
| Anthropic Claude | `https://api.anthropic.com/v1/` | `claude-opus-5-5` |
| OpenAI | `https://api.openai.com/v1` | the model you use |
| OpenRouter | `https://openrouter.ai/api/v1` | per OpenRouter |
| Ollama (self-hosted) | `http://localhost:11434/v1` | a model you have pulled |
| Custom | any server supporting OpenAI Chat Completions + tools | |

- The assistant uses each module's **tools** (balances, detailed ledgers, financial statements, receivables/payables, debt aging, VAT, inventory, fixed assets…)
  with **exactly the permissions of the user asking**.
- Write tools only create **draft vouchers** and return a link so the user can review and post them.
- By default **no payroll data is sent** to the AI provider; enable it separately with the "Cho trợ lý đọc dữ liệu lương" (Let the assistant read payroll data) checkbox.
- Open the assistant with the "Trợ lý AI" (AI assistant) button or Ctrl+J; full-screen page at `/ai`.

> Using a foreign AI provider means transferring data abroad. Businesses need to assess this under the personal data
> protection rules; to keep data on your own server, use self-hosted Ollama.

## 3. API and OpenAI-style integration

"Hệ thống → **API & tích hợp**" (System → API & integrations): create a token (`vfas_…`, shown once) with scope `read` or `read + write_draft`.
Tokens cannot post or administer, and expire after 90 days by default. OpenAPI documentation: `https://<domain>/api/docs`.

```bash
curl -H "Authorization: Bearer vfas_xxx" "https://<domain>/api/v1/reports/balance?account=111"
```

**Use it as an OpenAI model**: any OpenAI client (SDK, Open WebUI, LibreChat, n8n…) can chat with the accounting assistant:

```python
from openai import OpenAI

client = OpenAI(base_url="https://<domain>/api/v1", api_key="vfas_xxx")
r = client.chat.completions.create(model="vfas-agent",
                                   messages=[{"role": "user", "content": "Tồn quỹ tiền mặt hôm nay bao nhiêu?"}])
print(r.choices[0].message.content)
```

(The sample question asks "What is today's cash on hand?")

**Use the books as tools for your own agent**: `GET /api/v1/tools` returns the list in OpenAI's `tools` format;
execute with `POST /api/v1/tools/{name}` with the JSON parameters as the body.

Limit: 60 requests/minute/token.

## 4. Keyboard shortcuts

| Key | Action |
|---|---|
| Ctrl+K | Search vouchers, partners, accounts, items, functions |
| Ctrl+J | Open the AI assistant |
| Alt+T / Alt+C | New cash receipt / cash payment voucher |
| Ctrl+S | Save draft |
| Ctrl+Enter | Save and post |
| Enter / Tab | Next field; at the end of a line, add a new line (entry grid) |
| ? | Shortcut list |

Amount fields accept shorthand: `2tr` = 2.000.000, `500k` = 500.000, `1,5tr` = 1.500.000 (Vietnamese number format: `.` thousands separator, `,` decimal; "tr" = triệu, million).

## 5. Tax returns for HTKK

| Return | Where | File |
|---|---|---|
| 01/GTGT VAT return | Thuế → Tờ khai 01/GTGT → XML | form 842, version 2.8.3 |
| Financial statements (TT133) | Sổ sách & báo cáo → Báo cáo tài chính (year view) → "Tờ khai BCTC dạng XML" | form 683: B01a, B02, B03, F01 |
| 03/TNDN CIT finalization | Thuế → Quyết toán TNDN → XML 03/TNDN | form 892, version 2.9.4, with appendix 03-1A |
| 05/QTT-TNCN PIT finalization | Lương → Báo cáo (whole year) → XML 05/QTT-TNCN | form 953, version 2.9.3, with 05-1 and 05-2 |

The files follow the validation schemas shipped with HTKK 5.6.4. Import them in HTKK ("Nhập tờ khai từ XML"), check the
figures and the lines VFAS cannot know (dependants in 05-3, real-estate transfers, tax incentives, related-party
adjustments…), then file from HTKK or the e-tax portal. The notes to the financial statements (B09) are attached as a
separate file.

## 6. Accounting regimes

| Regime | For | Chart of accounts | Financial statements |
|---|---|---|---|
| TT133/2016 | Small and medium-sized enterprises | Appendix 1 of TT133 | B01a-DNN, B02-DNN, B03-DNN, B09-DNN, trial balance F01-DNN |
| TT99/2025 | Every enterprise from fiscal year 2026 (SMEs may choose it) | Appendix II of TT99 (selling expenses 641, admin expenses 642x by cost element, unemployment insurance 3386, deposits 244/344…) | B01-DN (short/long term), B02-DN, B03-DN, B09-DN, trial balance S06-DN |
| TT58/2026 | Micro enterprises, fiscal years starting on or after 01/07/2026 | none prescribed – VFAS keeps double-entry books on the TT133 chart | B01-DNSN, B02-DNSN |

- Choose the regime when creating the company; it can be changed in **Cài đặt → Kế toán & thuế** while the company has
  no voucher and no opening balance (the chart of accounts is rebuilt).
- Default accounts follow the regime: salaries (TT133 6422 / TT99 6421), depreciation (6422 / 6424), tools and prepaid
  expenses (6422 / 6423), purchased services (6422 / 6427), unemployment insurance (3385 / 3386), CIT expense (821 /
  82111).
- **B01-DN (TT99)** splits receivables, payables, loans, prepaid expenses… into short and long term by remaining term.
  The books do not store maturities, so balances go to the short-term lines by default (investments in other entities
  to long term) and the statement lists the accounts that need a judgement. On the B01-DN page, **Trình bày ngắn hạn /
  dài hạn** sets the accounts (or detail accounts, e.g. a `34112` long-term loan) presented the other way.
- The XML of the financial statements for HTKK (form 683) exists for TT133 only.

