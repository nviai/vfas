# VFAS user guide

- [1. Roles and permissions](#1-roles-and-permissions)
- [2. AI assistant](#2-ai-assistant)
- [3. API and OpenAI-style integration](#3-api-and-openai-style-integration)
- [4. Keyboard shortcuts](#4-keyboard-shortcuts)

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
