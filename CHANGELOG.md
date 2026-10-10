# Changelog

## Unreleased — reliability, imports, contact hygiene, and compliance

- **P0-2 — one campaign directory across both systems.** The classic campaigns and the Ads Manager number their campaigns independently, so id 2 names two different campaigns, and the MCP `list_campaigns` / `get_campaign` only read the classic table.
  - `GET /api/v1/overview/campaigns` now takes `kind`, `channel`, `status`, `live`, `search`, `page` and `per_page` (max 200), and reports `total` and `next_page`. Every row carries `kind` (`campaign` or `ads`; `legacy` is accepted as an alias of `campaign`), `system` (`legacy` or `ads`) and `channel`. With no parameters the response is unchanged apart from the added fields.
  - New `GET /api/v1/overview/campaigns/{id}?kind=` returns the full definition plus metrics. An id that exists in both systems and is requested without `kind` is a 409 `AMBIGUOUS_CAMPAIGN_ID` listing the candidates, never a silent pick. An unknown id or a wrong kind is 404.
  - MCP `list_campaigns` and `get_campaign` use these endpoints (new optional `kind` argument).
  - `dashboard/stats.active_campaigns` (and `completed_campaigns`) count both systems, honour the `channel` filter, and add `active_campaigns_by_kind`. The classic and Ads Manager campaign payloads now include `kind`.
  - The API error envelope now passes through extra keys from a dict `detail` (for example `candidates`).
- **P0-1 — campaign lifecycle (legacy `/api/v1/campaigns`).**
  - `POST /campaigns/{id}/validate` is now a **report**: HTTP 200 with `valid`, `errors`, `warnings`, `audience` and `changed: false`. It no longer moves the campaign to `scheduled`. Previously a validated draft that carried a past launch time was picked up by the scheduler and sent. **Behaviour change:** callers that treated `validate` as "arm it" must call `/start` (now) or `/schedule` (later); a draft is validated inline by `/start`.
  - `scheduled` now has real exits. `POST /pause` works on a scheduled campaign and records `paused_from`, and `/resume` restores `scheduled` rather than starting to send. If the launch time passed while it was held, the time is dropped so it cannot fire on its own. `DELETE` works for a scheduled campaign (or one paused while scheduled) that has sent nothing.
  - `POST /schedule` with `scheduled_start_at: null` now returns the campaign to `draft`. It used to report "Schedule cleared" and leave it `scheduled`. Calls that change nothing return `changed: false` and say so.
  - `CampaignService.TRANSITIONS` is enforced (every status change goes through `transition()`). A missing campaign is now 404 (was 400) and a state conflict is 409. New columns `campaigns.paused_from`, `paused_at` and `paused_reason`; they are added at startup by `schema_repair` and listed in `scripts/migrate_existing_db.sql`.
  - Campaigns page: Validate shows the report, drafts get Start now, scheduled campaigns get Pause, Cancel schedule and Delete, and a paused campaign shows why it is paused.
- Made campaign launch preflight advisory: audience, creatives, send windows, and missing configuration are reported as warnings; provider checks do not gate launch. Launch now locks the campaign row and commits audience assignments, state, events, and activity together, with rollback on failure and idempotent retries.
- Added request IDs and structured API errors while retaining the legacy `detail` key. Unexpected server failures are logged with traceback and stored privately for administrator lookup at `GET /api/v1/system/errors`.
- Added contact CSV preview, a pollable import-job API with persisted progress and downloadable error CSV, safe public-URL import, bounded file sizes, progress in the import dialog, country inference only from explicit/domain clues, company duplicate suggestions, and import tag rules. The existing synchronous CSV endpoint remains available.
- Made contact country nullable so an unknown country remains unknown instead of defaulting to Nigeria. Existing country values are not overwritten.
- Added contact tags to create/update and made list-member responses consistent with the full contact response while preserving their pagination envelope.
- Added typed JSON input for contact enrichment; existing query-parameter clients remain supported.
- Added multiple account reply-to addresses for mailbox address matching/routing. The legacy `reply_to` field remains the primary address and the existing Brevo sender/from/reply-to sending flow is unchanged.
- Added privacy-first suppression of clearly identified inbound OTP/PIN, bank-account, and payment-card content before it is copied into webhook records, contacts, messages, or logs. Ordinary inbound replies continue through the existing inbox path.
- Added additive schema migration statements for the new fields and support tables.
