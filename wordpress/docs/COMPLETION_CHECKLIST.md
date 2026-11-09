# WordPress-Native Sender Suite — Release & Host Certification Checklist

Build: **1.0.0-rc1**. WordPress is the sole native runtime backend (`wordpress/sender-suite/` + `wordpress/ui/`); original `backend/` and `frontend/` files (`165` frontend files verified by SHA-256) remain completely untouched.

**499 implemented native contracts (`469` `/api/v1/*` REST endpoints + `29` MCP Streamable HTTP JSON-RPC 2.0 / OAuth 2.1 PKCE `S256` wire endpoints + health probes), 9 root static/PWA contracts intentionally scoped to `?sender_suite_app=1` / plugin asset URLs, and 0 host-certified contracts (508 distinct source contracts total).**

Fresh offline verification evidence: **45,038** functional PHP-WASM assertions across PHP 7.4 and PHP 8.3 (`42` suites under `deny-network.cjs`), **138** PHP syntax checks (`69` PHP classes x 2 runtimes), **141** native UI SHA-256 provenance checks, and **36** headless Chromium UI assertions across `pretty`, `plain`, and `disabled` WordPress permalink modes.

## Completed Native Subsystems (Verified Offline)

- [x] **73-table MySQL/MariaDB Schema & Guarded Installer**: All 73 tables, 1,120 columns, 235 indexes, 61 foreign keys, transactional adapter, and AES-256-GCM field/table-scoped runtime vault (`Vault`, `Config`, `Installer`).
- [x] **Authentication, RBAC & Session Security**: Argon2id password codec, HS256 session cookies, same-origin CSRF guards, login rate-limiting, access settings, and profile management (`AppAuth`, `AuthRoutes`, `AccessSettings`).
- [x] **Contacts, Lists, Variables, Templates, Sequences & CSV/URL Imports**: Full contact lifecycle, list hygiene, custom shortcode renderer, attachment caps, sequence builder with immutable snapshots, multipart CSV preview/import, durable background import jobs, and SSRF-pinned URL imports.
- [x] **SMS-Gate Cloud, Multi-Phone Routing & Inbound/Status Webhooks**: Encrypted per-phone SMS-Gate credentials, pinned HTTPS cURL transport (`SmsGate`, `ProviderHttp`), all 4 routing modes (`default_phone`, `round_robin`, `least_used`, `priority` + `sim_failover`), status-only `/webhooks/smsgateway` + per-phone `/webhooks/smsgateway/{phone_id}`, and inbound `/webhooks/smsgate/inbound` with `STOP` opt-out, `ReplyClassifier`, and `auto_reply_rules` execution (`CampaignWorker`).
- [x] **Brevo Transactional/Bulk Email, DNS SPF/DKIM/DMARC & Gmail/Mailbox Sync**: Live Brevo v3 REST send/test/senders, DNS TXT/CNAME/MX verification (`sending_domains`), signed HMAC one-click `/email/unsubscribe` + `List-Unsubscribe-Post`, `/webhooks/brevo`, Google OAuth 2.0 flow, and Gmail REST API sync/move/delete/reply (`EmailManager`, `EmailTransport`).
- [x] **Invoices & Paystack Checkout / Settlement**: Multi-currency invoices, reusable payment links, manual payment recording, live Paystack transaction initialize/verify, sanitized public invoice portal (`/invoices/public/{token}`), and HMAC-SHA512 `X-Paystack-Signature` idempotent webhook settlement (`Invoices`, `InvoiceSettlement`).
- [x] **Ads Manager & Classic Campaigns**: Full Ads Manager CRUD, preflight validation, audience simulation, `launch`/`resume`/`dispatch`, public `302` shortlink click-tracking (`/ads/r/{assignment_id}`), follow-up task processing, bulk operations, classic campaign controls/reports/follow-ups, unified `/overview`, and DNS MX-aware `/validator` jobs (`AdsManager`, `CampaignControls`, `CampaignReports`, `CampaignFollowups`, `Overview`, `Validator`, `CampaignWorker`).
- [x] **CallGate, Push/Pushover Notifications & System Diagnostics**: CallGate live dialing/end/webhooks/logs, browser push subscriptions, Pushover delivery tests, `/system/health`, `/health/db`, `/system/errors`, and `/guide` (`CallsSettings`, `SystemGuide`).
- [x] **OpenRouter AI Agent & Full MCP / OAuth 2.1 Wire Server**: AI Agent sessions, encrypted OpenRouter chat completions, audio transcription, CSV/text file uploads, confirmation-gated action approval, transactional undo, MCP token & connector management, RFC 9728 / RFC 8414 `.well-known` discovery, Dynamic Client Registration, PKCE `S256` token issuance, JSON-RPC 2.0 tool execution (`/mcp-wire/*`), live connector self-test, and Python stdio bridge (`AiAgent`, `McpSettings`, `McpServer`).
- [x] **WordPress-Native React SPA (`?sender_suite_app=1`)**: Built with TypeScript + Vite from the untouched `frontend/` tree via `wordpress/ui/overlay/*` (`141` provenance checks + `36` headless Chromium checks).

## Remaining Host-Certification Gates (Requires Live WordPress + MySQL/MariaDB Staging Host)

- [ ] Execute `wordpress/tools/native-wordpress.php` against a live PHP 8.x + MySQL 8.0 / MariaDB + WordPress 6.4+ instance to certify real `mysqli` DDL activation, Argon2id hashing, and native SQL foreign-key/locking semantics.
- [ ] Verify end-to-end TLS connectivity and webhook callbacks against staging SMS-Gate, Brevo, Paystack, CallGate, Google OAuth, and OpenRouter credentials before production cutover.

## API Inventory by Source Module

| Source module | Scoped root static | Implemented natively | Host certified |
|---|---:|---:|---:|
| ads | 0 | 85 | 0 |
| ai | 0 | 1 | 0 |
| ai_agent | 0 | 36 | 0 |
| analytics | 0 | 5 | 0 |
| auth | 0 | 5 | 0 |
| automations | 0 | 7 | 0 |
| autoreply | 0 | 7 | 0 |
| calendar | 0 | 17 | 0 |
| calls | 0 | 11 | 0 |
| campaign_followups | 0 | 9 | 0 |
| campaigns | 0 | 18 | 0 |
| contacts | 0 | 25 | 0 |
| dashboard | 0 | 2 | 0 |
| email | 0 | 41 | 0 |
| followups | 0 | 7 | 0 |
| guide | 0 | 3 | 0 |
| inbox | 0 | 20 | 0 |
| invoices | 0 | 26 | 0 |
| lists | 0 | 13 | 0 |
| mail | 0 | 8 | 0 |
| mailbox | 0 | 14 | 0 |
| main | 9 | 2 | 0 |
| mcp | 0 | 42 | 0 |
| notifications | 0 | 10 | 0 |
| overview | 0 | 3 | 0 |
| send | 0 | 7 | 0 |
| sequences | 0 | 6 | 0 |
| settings | 0 | 32 | 0 |
| system | 0 | 2 | 0 |
| templates | 0 | 10 | 0 |
| validator | 0 | 8 | 0 |
| variables | 0 | 8 | 0 |
| webhooks | 0 | 9 | 0 |

## Full Source Contract Ledger

| Method | Original path | Native WordPress state |
|---|---|---|
| DELETE | `/api/v1/ads/audiences/{audience_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/calendar/{event_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/campaigns/{campaign_id}/audience/{assignment_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/campaigns/{campaign_id}/followup-steps/{step_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/creatives/{creative_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/followup-steps/{step_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/followup-templates/{template_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/sets/{set_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ads/suppression/{entry_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ai-agent/files/{file_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ai-agent/memory/global` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ai-agent/memory/{session_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ai-agent/sessions/{session_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/ai-agent/skills/{skill_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/automations/{automation_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/autoreply/{rule_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/calendar/{meeting_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/calls/webhooks/{webhook_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/campaign-followups/{followup_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/contacts/{contact_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/email/accounts/{account_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/email/domains/{domain_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/email/suppression/{entry_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/followups/{followup_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/invoices/{invoice_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/lists/{list_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/mailbox/{mailbox_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/mcp/clients/{client_pk}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/mcp/tokens/{token_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/sequences/{sequence_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/settings/callgate/webhooks/{webhook_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/settings/gateway/phones/{phone_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/settings/gateway/webhooks/{webhook_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/templates/{template_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/api/v1/variables/{variable_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/connectors/{key}/mcp` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/mcp` | implemented natively; native WordPress/MySQL HTTP unverified |
| DELETE | `/mcp/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/.well-known/oauth-authorization-server` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/.well-known/oauth-authorization-server/{path:path}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/.well-known/oauth-protected-resource` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/.well-known/oauth-protected-resource/{path:path}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/.well-known/openid-configuration` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/.well-known/openid-configuration/{path:path}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/activity` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/audiences` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/audiences/{audience_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/audiences/{audience_id}/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/calendar` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}/activity` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}/analytics` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}/audience` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}/followup-steps` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}/jobs` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}/optimization` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}/send-logs` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/campaigns/{campaign_id}/sets` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/contacts/{contact_id}/timeline` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/creatives/{creative_id}/analytics` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/creatives/{creative_id}/versions` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/export/{kind}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/followup-steps/{step_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/followup-templates` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/followup-templates/{template_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/followups` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/overview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/r/{assignment_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/reference` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/search` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/sets/{set_id}/creatives` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/sets/{set_id}/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ads/suppression` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/activity` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/files` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/files/{file_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/health` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/memory/global` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/memory/{session_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/models` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/presets` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/sessions` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/sessions/{session_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/settings` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/skills` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/skills/{skill_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/ai-agent/tools` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/analytics/by-campaign` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/analytics/calls` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/analytics/funnel` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/analytics/overview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/analytics/timeseries` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/auth/access` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/auth/me` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/automations` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/automations/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/automations/{automation_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/autoreply` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/autoreply/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/autoreply/{rule_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calendar` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calendar/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calendar/day` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calendar/tags` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calendar/tags/all` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calendar/upcoming` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calendar/{meeting_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calendar/{meeting_id}/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calls/active` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calls/config` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calls/logs` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/calls/webhooks` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaign-followups/campaigns` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaign-followups/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaign-followups/{followup_id}/log` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaign-followups/{followup_id}/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaigns` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaigns/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaigns/reconcile` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaigns/{campaign_id}/analytics` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaigns/{campaign_id}/conversations` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/campaigns/{campaign_id}/performance` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/enrich/status` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/export` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/export/csv` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/import/jobs/{job_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/import/jobs/{job_id}/errors.csv` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/tags/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/{contact_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/contacts/{contact_id}/activity` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/dashboard/charts` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/dashboard/stats` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/accounts` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/accounts/{account_id}/deliverability` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/accounts/{account_id}/senders` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/contacts/{contact_id}/engagement` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/domains` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/domains/{domain_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/events` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/health` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/history` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/inbox/conversations` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/inbox/conversations/{conversation_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/inbox/unread-count` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/messages/{message_id}/events` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/overview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/reference` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/senders` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/stats` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/suppression` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/email/unsubscribe` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/followups` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/followups/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/guide` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/guide/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/guide/summary` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/health` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/health/db` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/inbox/campaign-filters` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/inbox/conversations` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/inbox/conversations/{conversation_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/inbox/conversations/{conversation_id}/templates/{template_id}/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/inbox/device-info` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/analytics` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/currencies` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/links` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/payments` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/public/{token}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/public/{token}/verify` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/settings` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/{invoice_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/{invoice_id}/events` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/invoices/{invoice_id}/payments` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/lists` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/lists/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/lists/{list_id}/contacts` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/lists/{list_id}/stats` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mail/messages` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mail/overview` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mailbox` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mailbox/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mailbox/google/callback` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mailbox/google/start` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mailbox/report` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mailbox/{mailbox_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mcp/activity` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mcp/bridge` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mcp/clients` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mcp/connectors` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mcp/endpoints` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mcp/execution-health` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/mcp/tokens` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/notifications` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/notifications/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/notifications/push/subscriptions` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/notifications/push/vapid-key` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/notifications/unread-count` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/overview/campaigns` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/overview/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/overview/metrics` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/send/history` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/send/scheduled` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/sequences` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/sequences/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/sequences/{sequence_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/access` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/appearance` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/callgate` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/callgate/webhooks` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/compliance` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/gateway` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/gateway/phones` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/gateway/webhooks` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/google-oauth` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/notifications` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/settings/notifications/muted-senders` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/system/errors` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/system/health` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/templates` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/templates/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/templates/categories` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/templates/{template_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/validator/jobs` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/validator/jobs/{job_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/validator/status` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/variables` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/variables/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/variables/contact/{contact_id}/profile` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/webhooks/callgate` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/webhooks/logs` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/api/v1/webhooks/smsgateway` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/apple-touch-icon.png` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| GET | `/connectors/{key}/mcp` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/connectors/{key}/oauth/authorize` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/connectors/{key}/oauth/register/{client_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/favicon.svg` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| GET | `/icon-192.png` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| GET | `/icon-512.png` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| GET | `/icon-maskable-192.png` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| GET | `/icon-maskable-512.png` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| GET | `/manifest.json` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| GET | `/mcp` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/mcp/` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/mcp/health` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/oauth/approved` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/oauth/authorize` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/oauth/register/{client_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| GET | `/sw.js` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| GET | `/{fp:path}` | intentionally scoped to ?sender_suite_app=1 / plugin asset URLs (never hijacking WordPress site root) |
| PATCH | `/api/v1/ads/audiences/{audience_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/ads/calendar/{event_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/ads/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/ads/campaigns/{campaign_id}/followup-steps/{step_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/ads/creatives/{creative_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/ads/followup-steps/{step_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/ads/followup-templates/{template_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/ads/sets/{set_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/ai-agent/sessions/{session_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/email/accounts/{account_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/invoices/{invoice_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PATCH | `/api/v1/mailbox/{mailbox_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/audiences` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/audiences/{audience_id}/attach` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/audiences/{audience_id}/duplicate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/calendar` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/bulk` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/archive` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/audience/bulk-remove` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/complete` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/contacts` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/dispatch` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/duplicate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/followup-steps` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/jobs/retry` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/launch` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/optimize` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/pause` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/rebuild-audience` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/resume` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/sets` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/simulate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/campaigns/{campaign_id}/validate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/contacts/bulk` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/contacts/{contact_id}/action` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/creatives/bulk` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/creatives/{creative_id}/duplicate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/creatives/{creative_id}/promote` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/followup-templates` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/followups` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/followups/process` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/followups/{task_id}/{action}` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/sets/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/sets/{set_id}/creatives` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/sets/{set_id}/duplicate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/sets/{set_id}/pause-losers` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/suppression` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ads/track/click` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/actions/{action_id}/approve` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/actions/{action_id}/cancel` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/activity/{action_id}/undo` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/files` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/sessions` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/sessions/{session_id}/chat` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/sessions/{session_id}/files` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/settings/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/skills` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/skills/json` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai-agent/transcribe` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/ai/classify` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/auth/admin` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/auth/login` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/auth/logout` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/automations/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/automations/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/autoreply/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/autoreply/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calendar/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calendar/{meeting_id}/cancel` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calendar/{meeting_id}/complete` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calendar/{meeting_id}/confirm` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calendar/{meeting_id}/no-show` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calendar/{meeting_id}/send-invite` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calendar/{meeting_id}/send-reminder` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calls/end` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calls/log-direct` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calls/register-webhook` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calls/start` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/calls/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaign-followups/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaign-followups/campaigns/{campaign_id}/process-now` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaign-followups/{followup_id}/run` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaigns/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaigns/{campaign_id}/duplicate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaigns/{campaign_id}/pause` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaigns/{campaign_id}/resume` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaigns/{campaign_id}/schedule` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaigns/{campaign_id}/start` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaigns/{campaign_id}/stop` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/campaigns/{campaign_id}/validate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/bulk` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/clean` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/enrich` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/import/csv` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/import/jobs` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/import/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/import/url` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/quarantine/generated` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/validate-emails` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/{contact_id}/email-opt-in` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/{contact_id}/email-opt-out` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/contacts/{contact_id}/sms-opt-in` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/accounts` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/accounts/{account_id}/default` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/accounts/{account_id}/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/accounts/{account_id}/test-send` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/accounts/{account_id}/webhook/reveal` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/accounts/{account_id}/webhook/rotate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/domains` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/domains/{domain_id}/verify` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/inbox/conversations/{conversation_id}/read` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/inbox/conversations/{conversation_id}/reply` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/inbox/conversations/{conversation_id}/status` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/messages/{message_id}/retry` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/send` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/senders-preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/suppression` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/test-send` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/email/unsubscribe` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/followups/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/followups/{followup_id}/reschedule` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/followups/{followup_id}/send-now` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/followups/{followup_id}/skip` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-close` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-interested` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-not-interested` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-opportunity` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-unread` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/reply` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/resume-sequence` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/status` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/stop-sequence` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/conversations/{conversation_id}/{mark}` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/messages/{message_id}/feedback` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/poll-debug` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/poll-now` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/register-webhook` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/inbox/sync-full` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/links` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/paystack/webhook` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/public/{token}/pay` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/settings/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/{invoice_id}/duplicate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/{invoice_id}/pay` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/{invoice_id}/payments` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/{invoice_id}/remind` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/{invoice_id}/rotate-link` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/{invoice_id}/send` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/invoices/{invoice_id}/void` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/lists/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/lists/{list_id}/clean` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/lists/{list_id}/contacts` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/lists/{list_id}/contacts/add-all` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/lists/{list_id}/contacts/delete` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/lists/{list_id}/contacts/remove` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/lists/{list_id}/validate-emails` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}/delete` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}/flags` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}/move` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}/reply` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mail/refresh` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mailbox` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mailbox/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mailbox/{mailbox_id}/check` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mailbox/{mailbox_id}/filters` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mailbox/{mailbox_id}/sync` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mailbox/{mailbox_id}/test-send` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mcp/connectors/{key}` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mcp/connectors/{key}/revoke` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mcp/connectors/{key}/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/mcp/tokens` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/notifications/push/subscribe` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/notifications/push/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/notifications/push/unsubscribe` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/notifications/read-all` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/notifications/{event_id}/read` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/send/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/send/messages/{mid}/retry` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/send/scheduled/{sid}/cancel` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/send/scheduled/{sid}/retry` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/send/validate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/sequences/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/settings/callgate/register-webhook` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/settings/callgate/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/settings/gateway/phones` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/settings/gateway/phones/{phone_id}/register-webhook` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/settings/gateway/phones/{phone_id}/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/settings/gateway/register-webhook` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/settings/gateway/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/settings/notifications/{provider}/test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/templates/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/templates/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/templates/{template_id}/duplicate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/templates/{template_id}/to-email` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/validator/batch` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/validator/jobs` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/validator/jobs/{job_id}/cancel` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/validator/selection` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/validator/self-test` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/variables/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/variables/preview` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/variables/sync` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/webhooks/brevo` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/webhooks/brevo/{account_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/webhooks/callgate` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/webhooks/smsgate/inbound` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/webhooks/smsgateway` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/api/v1/webhooks/smsgateway/phone/{phone_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/connectors/{key}/mcp` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/connectors/{key}/oauth/authorize` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/connectors/{key}/oauth/register` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/connectors/{key}/oauth/revoke` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/connectors/{key}/oauth/token` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/mcp` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/mcp/` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/oauth/authorize` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/oauth/register` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/oauth/revoke` | implemented natively; native WordPress/MySQL HTTP unverified |
| POST | `/oauth/token` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/ai-agent/memory/global` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/ai-agent/memory/{session_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/ai-agent/presets` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/ai-agent/settings` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/ai-agent/settings/voice` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/automations/{automation_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/autoreply/{rule_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/calendar/{meeting_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/calls/config` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/campaign-followups/{followup_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/campaigns/{campaign_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/contacts/{contact_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/invoices/settings` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/lists/{list_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/sequences/{sequence_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/access` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/appearance` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/callgate` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/compliance` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/gateway/phones/{phone_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/gateway/routing` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/gateway/sim` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/google-oauth` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/notifications/muted-senders` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/settings/notifications/{provider}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/templates/{template_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
| PUT | `/api/v1/variables/{variable_id}` | implemented natively; native WordPress/MySQL HTTP unverified |
