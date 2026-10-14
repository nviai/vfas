# Requests from team F (AI assistant, API) to the core / UI team

1. **Company data export (`app/cli.py::export_company`)**: the AI configuration lives in `companies.settings["ai"]`
   (key `api_key_enc`, encrypted). The export should remove `settings.ai.api_key_enc` (it currently only drops the non-existent `ai_settings` table).
2. **Baseline migration**: add the `ai_conversations`, `ai_messages`, `ai_usage` tables (models in `app/features/ai/models.py`).
3. **New environment variable** (add to `.env.example`, README): `AI_ALLOW_PRIVATE_URLS=1` lets the assistant call internal
   servers (Ollama, vLLM on the LAN) when `ALLOW_SIGNUP=true`. Default: SaaS mode (open sign-up) blocks internal,
   loopback and link-local addresses and requires https (SSRF protection); self-hosted installs (no open sign-up) allow them.
4. **UI team – `partials/ai_chat.html`**: on the full-screen `/ai` page the chat panel's close button (×) does nothing.
   Proposal: a `chat.embedded = true` variable to hide the close button; the `/ai` router will pass this variable.
5. **API rate limiting** (60 requests/minute/token) is currently kept in process memory. With multiple workers each worker
   counts separately – it needs to move to the DB/Redis for an exact limit.
6. **`base_context.ai_enabled`** only checks the `enabled` flag; `get_config(company).ready` could be used to hide the assistant button
   when the configuration is incomplete (the panel currently still shows the reason and a link to the settings, so this is optional).
