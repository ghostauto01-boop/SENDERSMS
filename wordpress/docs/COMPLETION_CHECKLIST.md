# Complete remaining-work checklist — INCOMPLETE

Build: **0.11.0-dev**. WordPress is the native application backend; no original Python service is the proposed runtime. Original repository application files remain unchanged.

**412 missing handlers, 96 partially implemented handlers, zero native-certified contracts, 508 source method/path contracts in total.** A partial handler has actual implementation, but does not mean its complete workflow, UI, dependencies and native behavior are certified. Every endpoint remains in the release backlog until those gates pass. Raw handler counts are not a percent-complete estimate.

Fresh offline evidence: 38,736 functional PHP-WASM assertions, 76 first-party syntax checks, 165 unchanged original frontend hashes. Offline evidence is **not** real WordPress/MySQL/HTTP/provider/browser certification. This checklist is the full current implementation/release backlog, not a claim that software can be guaranteed to run perfectly on every host.

## Implementation order and acceptance gates

### 1. Native installation, data and operational safety
- [ ] Execute real WordPress activation, repeat activation, version upgrades and failure recovery on isolated supported WordPress/PHP/MySQL/MariaDB hosts.
- [ ] Verify all 73 source tables, 1,120 columns, indexes, 61 FKs, nullability/defaults, uniqueness, Unicode/collation, SQL modes, SQL packet limits, date/time precision and transactions on native engines.
- [ ] Implement/review complete legacy-data migration into WordPress, relationships/IDs, consent/opt-outs, encrypted values, attachments, rollback, backups and restore; never overwrite the original application.
- [ ] Validate schema drift/upgrade locks, partial-install repair, deactivation/reactivation, retained data, explicitly authorized uninstall/erasure and table-prefix/multisite behavior.
- [ ] Execute real Argon2id/AES-GCM/key-rotation, cookie/session expiry, role authorization, operator reset and atomic login rate-limit tests; no WordPress-admin cookie bypass for own-app APIs.
- [ ] Document and test PHP extensions, TLS CA trust, DNS/HTTP restrictions, upload/post/memory/time limits, MySQL advisory locks, DB proxies/reconnections, cron configuration and low-traffic/disabled-cron recovery.

### 2. Full native API/workflow implementation
- [ ] Finish all exact methods/paths in the endpoint inventory below with source-compatible validation, responses, permissions and substantive behavior; never replace missing features with fake successful stubs.
- [ ] Complete settings/provider wiring beyond the eight initial encrypted auth settings; all source configuration families, redacted outputs, secret storage, URL validation and rotation.
- [ ] Complete remaining profile/account/session workflows and roles/permissions.
- [ ] Certify implemented contact/list deletion, dependency cleanup and bulk actions on native hosts; certify implemented JSON/CSV exports and complete enrichment, email verification, aliases/suppression and export/data-management workflows. Avoid deleting shared calendar attendees or unrelated records.
- [ ] Validate upload preview, synchronous imports, background jobs/progress/error reports and URL imports on native hosts, including consent preservation, atomic batches, crashes, duplicate IDs/memberships, locks, public-address pinning/TLS, limits and retention. These APIs are implemented, not native-certified.
- [ ] Implement actual SMS transports, provider/device/gateway configuration, one-to-one/bulk sends, scheduling, delivery/status reconciliation, bounded retries and error handling.
- [ ] Implement complete email accounts/mailboxes/transports, domains/DNS verification, SMTP/provider/OAuth paths, sender selection, bulk/one-to-one mail, attachments, threading, suppression, unsubscribe/list headers, bounce/open/click tracking and inbox ingestion.
- [ ] Implement both classic campaigns and Ads Manager: drafts, audiences/sets/creatives, assignment/targeting, transitions, budgets/throttles, queues, pause/resume/stop/delete, attribution, follow-up steps/templates/tasks and reconciliation.
- [ ] Finish sequence execution (builder CRUD/validation/version snapshots now implemented), standalone/campaign follow-ups, due/overdue handling, quiet hours, timezone/DST behavior, retries and stop-on-reply/opt-out policies.
- [ ] Implement inbox/conversation reads, replies, read/unread state, attachments, searches/filters, campaign attribution, polling/sync and inbound-message lifecycle.
- [ ] Implement calls/CallGate: configuration, handsets/devices, dial requests, call logs and authenticated event/webhook lifecycle. Call analytics alone is not calling.
- [ ] Implement webhook registration/verification, exact raw-body signature handling, replay protection, deduplication, event ordering, rate limits, retries and redacted logs.
- [ ] Wire auto-reply/If-Then rule execution, per-contact cooldowns, counters and supported actions into actual inbound events. Rule CRUD/classification/non-sending tests already exist; action execution does not.
- [ ] Complete calendar scheduling/attendees/invitations, invoices/items/payments/PDF/email workflows and safe deletion semantics.
- [ ] Complete dashboards/overview/unified managers and reporting. Five analytics read APIs now exist; complete dashboard, email/manager analytics and native validation remain outstanding.
- [ ] Complete notification delivery, browser push subscriptions, provider/browser permission handling, setup guide and live setup summaries. Notification read-state APIs alone do not deliver notifications.
- [ ] Complete AI assistant/agent sessions, files/memory/skills/tools, external-provider configuration, explicit action permissions and safe tool execution; complete MCP endpoints and authentication, SSE/NDJSON streaming, disconnects, buffering/proxy timeouts and browser cancellation.
- [ ] Complete validator/verification tools, diagnostics, persisted system errors and accurate worker/queue health (not source Redis/Celery status fabricated for WordPress).

### 3. Messaging and worker safety before enabling sends
- [ ] Enforce consent, channel opt-outs, suppression, unsubscribe/bounce states and recipient validity consistently at enqueue **and** delivery time.
- [ ] Prove duplicate-send prevention/idempotency, provider-ack vs DB-commit ambiguity handling, bounded retries, claims/leases and recovery across concurrent workers, crashes and lost connections.
- [ ] Reconcile late/duplicate/out-of-order events, campaign/contact/message attribution and counters across both campaign engines and channels.
- [ ] Enforce quiet hours, scheduling/timezones, per-provider/account/device throttles and safe cancellation/paused states.
- [ ] Verify attachment handling, CSV/formula export safety, stored HTML/rendering sanitization, untrusted inbound content, path/SSRF protections and size/resource caps.
- [ ] Review secrets, operator-only data, public unsubscribe/tracking links, webhook tokens, raw CSV/error retention, provider-error scrubbing and logging/privacy/backups.
- [ ] Test with isolated fake provider servers first; perform authorized staging-provider delivery/reconciliation only with explicit test credentials/recipients. Never send unsolicited real messages as a development check.

### 4. Complete WordPress frontend integration
- [ ] Adapt copied/build assets without modifying original frontend source; map every original API request to the native REST backend with correct base paths and deployment subdirectories.
- [ ] Mount the complete application UI in WordPress with working routes/history, assets, deep links, authentication/roles, login/logout/cookie behavior, loading/error states and navigation.
- [ ] Wire every original page/wizard/control, including upload/URL imports, managers, inboxes, settings, analytics, calls, follow-ups, calendar, invoices, agents and diagnostics; hide/disable genuinely unsupported actions, not pretend they work.
- [ ] Verify real browser multipart uploads, raw CSV/downloads, attachments, previews, pagination/filtering, mobile/responsive behavior, accessibility and permission boundaries.
- [ ] Validate REST reserved-query hardening, JSONP/output flags, CSRF/origin checks and compatibility with relevant WordPress themes/plugins/cache/security policies.

### 5. Native release proof and final plugin file
- [ ] Execute structured isolated native installation and HTTP reference runners. They are present but have **not run**; the current GitHub connection previously rejected workflow creation for lack of workflow-writing permission.
- [ ] Add/execute complete browser journeys, native constraint/FK/lifecycle tests, failure injection, concurrent workers, recovery, DST/locale/timezone cases and large-data performance/hosting tests.
- [ ] Reconcile every source contract and substantive workflow against fresh native evidence; partial/mock tests cannot certify release readiness.
- [ ] Confirm licenses/notices, dependency integrity, original-source preservation, security review, backup/rollback procedures and operator installation/configuration documentation.
- [ ] Produce and verify the final reproducible WordPress upload ZIP **after** the gates pass; staging activation/update tests, checksums and release evidence must accompany it.
- [ ] Deliver the completed plugin; upload to Google Drive if still requested at completion. No completed release or Drive upload has occurred.

## Current package meaning

`sender-suite-development.zip` is an **older incomplete development snapshot**, not the requested finished application. Its archive layout/CRC are checked, but real WordPress activation and production operation have not been verified. Do not install it on a production messaging site. No new interim archive was produced for ongoing lifecycle work. The existing ZIP does not contain all current working-tree changes; it is not a ready release.

## API inventory by source module

All counts below are derived from `docs/contracts.json`. Missing = no native handler; partial = implementation exists but native/full-workflow certification remains open.

| Source module | Missing | Partial | Fully certified |
|---|---:|---:|---:|
| ads | 85 | 0 | 0 |
| ai | 0 | 1 | 0 |
| ai_agent | 36 | 0 | 0 |
| analytics | 0 | 5 | 0 |
| auth | 0 | 5 | 0 |
| automations | 0 | 7 | 0 |
| autoreply | 0 | 7 | 0 |
| calendar | 17 | 0 | 0 |
| calls | 11 | 0 | 0 |
| campaign_followups | 9 | 0 | 0 |
| campaigns | 18 | 0 | 0 |
| contacts | 3 | 22 | 0 |
| dashboard | 2 | 0 | 0 |
| email | 41 | 0 | 0 |
| followups | 7 | 0 | 0 |
| guide | 3 | 0 | 0 |
| inbox | 20 | 0 | 0 |
| invoices | 26 | 0 | 0 |
| lists | 1 | 12 | 0 |
| mail | 8 | 0 | 0 |
| mailbox | 14 | 0 | 0 |
| main | 10 | 1 | 0 |
| mcp | 42 | 0 | 0 |
| notifications | 5 | 5 | 0 |
| overview | 3 | 0 | 0 |
| send | 7 | 0 | 0 |
| sequences | 0 | 6 | 0 |
| settings | 24 | 8 | 0 |
| system | 2 | 0 | 0 |
| templates | 0 | 10 | 0 |
| validator | 8 | 0 | 0 |
| variables | 1 | 7 | 0 |
| webhooks | 9 | 0 | 0 |

## Every remaining source contract

This exhaustive list includes partially implemented routes because none is yet native-certified. Detailed source locations are retained in `docs/REMAINING_WORK.md` and `docs/contracts.json`.

| Method | Original path | Remaining state |
|---|---|---|
| DELETE | `/api/v1/ads/audiences/{audience_id}` | Missing implementation |
| DELETE | `/api/v1/ads/calendar/{event_id}` | Missing implementation |
| DELETE | `/api/v1/ads/campaigns/{campaign_id}` | Missing implementation |
| DELETE | `/api/v1/ads/campaigns/{campaign_id}/audience/{assignment_id}` | Missing implementation |
| DELETE | `/api/v1/ads/campaigns/{campaign_id}/followup-steps/{step_id}` | Missing implementation |
| DELETE | `/api/v1/ads/creatives/{creative_id}` | Missing implementation |
| DELETE | `/api/v1/ads/followup-steps/{step_id}` | Missing implementation |
| DELETE | `/api/v1/ads/followup-templates/{template_id}` | Missing implementation |
| DELETE | `/api/v1/ads/sets/{set_id}` | Missing implementation |
| DELETE | `/api/v1/ads/suppression/{entry_id}` | Missing implementation |
| DELETE | `/api/v1/ai-agent/files/{file_id}` | Missing implementation |
| DELETE | `/api/v1/ai-agent/memory/global` | Missing implementation |
| DELETE | `/api/v1/ai-agent/memory/{session_id}` | Missing implementation |
| DELETE | `/api/v1/ai-agent/sessions/{session_id}` | Missing implementation |
| DELETE | `/api/v1/ai-agent/skills/{skill_id}` | Missing implementation |
| DELETE | `/api/v1/automations/{automation_id}` | Implemented partially; native/full-workflow proof required |
| DELETE | `/api/v1/autoreply/{rule_id}` | Implemented partially; native/full-workflow proof required |
| DELETE | `/api/v1/calendar/{meeting_id}` | Missing implementation |
| DELETE | `/api/v1/calls/webhooks/{webhook_id}` | Missing implementation |
| DELETE | `/api/v1/campaign-followups/{followup_id}` | Missing implementation |
| DELETE | `/api/v1/campaigns/{campaign_id}` | Missing implementation |
| DELETE | `/api/v1/contacts/{contact_id}` | Implemented partially; native/full-workflow proof required |
| DELETE | `/api/v1/email/accounts/{account_id}` | Missing implementation |
| DELETE | `/api/v1/email/domains/{domain_id}` | Missing implementation |
| DELETE | `/api/v1/email/suppression/{entry_id}` | Missing implementation |
| DELETE | `/api/v1/followups/{followup_id}` | Missing implementation |
| DELETE | `/api/v1/invoices/{invoice_id}` | Missing implementation |
| DELETE | `/api/v1/lists/{list_id}` | Implemented partially; native/full-workflow proof required |
| DELETE | `/api/v1/mailbox/{mailbox_id}` | Missing implementation |
| DELETE | `/api/v1/mcp/clients/{client_pk}` | Missing implementation |
| DELETE | `/api/v1/mcp/tokens/{token_id}` | Missing implementation |
| DELETE | `/api/v1/sequences/{sequence_id}` | Implemented partially; native/full-workflow proof required |
| DELETE | `/api/v1/settings/callgate/webhooks/{webhook_id}` | Missing implementation |
| DELETE | `/api/v1/settings/gateway/phones/{phone_id}` | Missing implementation |
| DELETE | `/api/v1/settings/gateway/webhooks/{webhook_id}` | Missing implementation |
| DELETE | `/api/v1/templates/{template_id}` | Implemented partially; native/full-workflow proof required |
| DELETE | `/api/v1/variables/{variable_id}` | Implemented partially; native/full-workflow proof required |
| DELETE | `/connectors/{key}/mcp` | Missing implementation |
| DELETE | `/mcp` | Missing implementation |
| DELETE | `/mcp/` | Missing implementation |
| GET | `/.well-known/oauth-authorization-server` | Missing implementation |
| GET | `/.well-known/oauth-authorization-server/{path:path}` | Missing implementation |
| GET | `/.well-known/oauth-protected-resource` | Missing implementation |
| GET | `/.well-known/oauth-protected-resource/{path:path}` | Missing implementation |
| GET | `/.well-known/openid-configuration` | Missing implementation |
| GET | `/.well-known/openid-configuration/{path:path}` | Missing implementation |
| GET | `/api/v1/ads/activity` | Missing implementation |
| GET | `/api/v1/ads/audiences` | Missing implementation |
| GET | `/api/v1/ads/audiences/{audience_id}` | Missing implementation |
| GET | `/api/v1/ads/audiences/{audience_id}/preview` | Missing implementation |
| GET | `/api/v1/ads/calendar` | Missing implementation |
| GET | `/api/v1/ads/campaigns` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}/activity` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}/analytics` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}/audience` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}/followup-steps` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}/jobs` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}/optimization` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}/send-logs` | Missing implementation |
| GET | `/api/v1/ads/campaigns/{campaign_id}/sets` | Missing implementation |
| GET | `/api/v1/ads/contacts/{contact_id}/timeline` | Missing implementation |
| GET | `/api/v1/ads/creatives/{creative_id}/analytics` | Missing implementation |
| GET | `/api/v1/ads/creatives/{creative_id}/versions` | Missing implementation |
| GET | `/api/v1/ads/export/{kind}` | Missing implementation |
| GET | `/api/v1/ads/followup-steps/{step_id}` | Missing implementation |
| GET | `/api/v1/ads/followup-templates` | Missing implementation |
| GET | `/api/v1/ads/followup-templates/{template_id}` | Missing implementation |
| GET | `/api/v1/ads/followups` | Missing implementation |
| GET | `/api/v1/ads/overview` | Missing implementation |
| GET | `/api/v1/ads/r/{assignment_id}` | Missing implementation |
| GET | `/api/v1/ads/reference` | Missing implementation |
| GET | `/api/v1/ads/search` | Missing implementation |
| GET | `/api/v1/ads/sets/{set_id}/creatives` | Missing implementation |
| GET | `/api/v1/ads/sets/{set_id}/preview` | Missing implementation |
| GET | `/api/v1/ads/suppression` | Missing implementation |
| GET | `/api/v1/ai-agent/activity` | Missing implementation |
| GET | `/api/v1/ai-agent/files` | Missing implementation |
| GET | `/api/v1/ai-agent/files/{file_id}` | Missing implementation |
| GET | `/api/v1/ai-agent/health` | Missing implementation |
| GET | `/api/v1/ai-agent/memory/global` | Missing implementation |
| GET | `/api/v1/ai-agent/memory/{session_id}` | Missing implementation |
| GET | `/api/v1/ai-agent/models` | Missing implementation |
| GET | `/api/v1/ai-agent/presets` | Missing implementation |
| GET | `/api/v1/ai-agent/sessions` | Missing implementation |
| GET | `/api/v1/ai-agent/sessions/{session_id}` | Missing implementation |
| GET | `/api/v1/ai-agent/settings` | Missing implementation |
| GET | `/api/v1/ai-agent/skills` | Missing implementation |
| GET | `/api/v1/ai-agent/skills/{skill_id}` | Missing implementation |
| GET | `/api/v1/ai-agent/tools` | Missing implementation |
| GET | `/api/v1/analytics/by-campaign` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/analytics/calls` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/analytics/funnel` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/analytics/overview` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/analytics/timeseries` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/auth/access` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/auth/me` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/automations` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/automations/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/automations/{automation_id}` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/autoreply` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/autoreply/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/autoreply/{rule_id}` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/calendar` | Missing implementation |
| GET | `/api/v1/calendar/` | Missing implementation |
| GET | `/api/v1/calendar/day` | Missing implementation |
| GET | `/api/v1/calendar/tags` | Missing implementation |
| GET | `/api/v1/calendar/tags/all` | Missing implementation |
| GET | `/api/v1/calendar/upcoming` | Missing implementation |
| GET | `/api/v1/calendar/{meeting_id}` | Missing implementation |
| GET | `/api/v1/calendar/{meeting_id}/preview` | Missing implementation |
| GET | `/api/v1/calls/active` | Missing implementation |
| GET | `/api/v1/calls/config` | Missing implementation |
| GET | `/api/v1/calls/logs` | Missing implementation |
| GET | `/api/v1/calls/webhooks` | Missing implementation |
| GET | `/api/v1/campaign-followups/campaigns` | Missing implementation |
| GET | `/api/v1/campaign-followups/campaigns/{campaign_id}` | Missing implementation |
| GET | `/api/v1/campaign-followups/{followup_id}/log` | Missing implementation |
| GET | `/api/v1/campaign-followups/{followup_id}/preview` | Missing implementation |
| GET | `/api/v1/campaigns` | Missing implementation |
| GET | `/api/v1/campaigns/` | Missing implementation |
| GET | `/api/v1/campaigns/reconcile` | Missing implementation |
| GET | `/api/v1/campaigns/{campaign_id}` | Missing implementation |
| GET | `/api/v1/campaigns/{campaign_id}/analytics` | Missing implementation |
| GET | `/api/v1/campaigns/{campaign_id}/conversations` | Missing implementation |
| GET | `/api/v1/campaigns/{campaign_id}/performance` | Missing implementation |
| GET | `/api/v1/contacts` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/contacts/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/contacts/enrich/status` | Missing implementation |
| GET | `/api/v1/contacts/export` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/contacts/export/csv` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/contacts/import/jobs/{job_id}` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/contacts/import/jobs/{job_id}/errors.csv` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/contacts/tags/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/contacts/{contact_id}` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/contacts/{contact_id}/activity` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/dashboard/charts` | Missing implementation |
| GET | `/api/v1/dashboard/stats` | Missing implementation |
| GET | `/api/v1/email/accounts` | Missing implementation |
| GET | `/api/v1/email/accounts/{account_id}/deliverability` | Missing implementation |
| GET | `/api/v1/email/accounts/{account_id}/senders` | Missing implementation |
| GET | `/api/v1/email/contacts/{contact_id}/engagement` | Missing implementation |
| GET | `/api/v1/email/domains` | Missing implementation |
| GET | `/api/v1/email/domains/{domain_id}` | Missing implementation |
| GET | `/api/v1/email/events` | Missing implementation |
| GET | `/api/v1/email/health` | Missing implementation |
| GET | `/api/v1/email/history` | Missing implementation |
| GET | `/api/v1/email/inbox/conversations` | Missing implementation |
| GET | `/api/v1/email/inbox/conversations/{conversation_id}` | Missing implementation |
| GET | `/api/v1/email/inbox/unread-count` | Missing implementation |
| GET | `/api/v1/email/messages/{message_id}/events` | Missing implementation |
| GET | `/api/v1/email/overview` | Missing implementation |
| GET | `/api/v1/email/reference` | Missing implementation |
| GET | `/api/v1/email/senders` | Missing implementation |
| GET | `/api/v1/email/stats` | Missing implementation |
| GET | `/api/v1/email/suppression` | Missing implementation |
| GET | `/api/v1/email/unsubscribe` | Missing implementation |
| GET | `/api/v1/followups` | Missing implementation |
| GET | `/api/v1/followups/` | Missing implementation |
| GET | `/api/v1/guide` | Missing implementation |
| GET | `/api/v1/guide/` | Missing implementation |
| GET | `/api/v1/guide/summary` | Missing implementation |
| GET | `/api/v1/health` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/health/db` | Missing implementation |
| GET | `/api/v1/inbox/campaign-filters` | Missing implementation |
| GET | `/api/v1/inbox/conversations` | Missing implementation |
| GET | `/api/v1/inbox/conversations/{conversation_id}` | Missing implementation |
| GET | `/api/v1/inbox/conversations/{conversation_id}/templates/{template_id}/preview` | Missing implementation |
| GET | `/api/v1/inbox/device-info` | Missing implementation |
| GET | `/api/v1/invoices` | Missing implementation |
| GET | `/api/v1/invoices/analytics` | Missing implementation |
| GET | `/api/v1/invoices/currencies` | Missing implementation |
| GET | `/api/v1/invoices/links` | Missing implementation |
| GET | `/api/v1/invoices/payments` | Missing implementation |
| GET | `/api/v1/invoices/public/{token}` | Missing implementation |
| GET | `/api/v1/invoices/public/{token}/verify` | Missing implementation |
| GET | `/api/v1/invoices/settings` | Missing implementation |
| GET | `/api/v1/invoices/{invoice_id}` | Missing implementation |
| GET | `/api/v1/invoices/{invoice_id}/events` | Missing implementation |
| GET | `/api/v1/invoices/{invoice_id}/payments` | Missing implementation |
| GET | `/api/v1/lists` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/lists/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/lists/{list_id}/contacts` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/lists/{list_id}/stats` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/mail/messages` | Missing implementation |
| GET | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}` | Missing implementation |
| GET | `/api/v1/mail/overview` | Missing implementation |
| GET | `/api/v1/mailbox` | Missing implementation |
| GET | `/api/v1/mailbox/` | Missing implementation |
| GET | `/api/v1/mailbox/google/callback` | Missing implementation |
| GET | `/api/v1/mailbox/google/start` | Missing implementation |
| GET | `/api/v1/mailbox/report` | Missing implementation |
| GET | `/api/v1/mailbox/{mailbox_id}` | Missing implementation |
| GET | `/api/v1/mcp/activity` | Missing implementation |
| GET | `/api/v1/mcp/bridge` | Missing implementation |
| GET | `/api/v1/mcp/clients` | Missing implementation |
| GET | `/api/v1/mcp/connectors` | Missing implementation |
| GET | `/api/v1/mcp/endpoints` | Missing implementation |
| GET | `/api/v1/mcp/execution-health` | Missing implementation |
| GET | `/api/v1/mcp/tokens` | Missing implementation |
| GET | `/api/v1/notifications` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/notifications/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/notifications/push/subscriptions` | Missing implementation |
| GET | `/api/v1/notifications/push/vapid-key` | Missing implementation |
| GET | `/api/v1/notifications/unread-count` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/overview/campaigns` | Missing implementation |
| GET | `/api/v1/overview/campaigns/{campaign_id}` | Missing implementation |
| GET | `/api/v1/overview/metrics` | Missing implementation |
| GET | `/api/v1/send/history` | Missing implementation |
| GET | `/api/v1/send/scheduled` | Missing implementation |
| GET | `/api/v1/sequences` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/sequences/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/sequences/{sequence_id}` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/settings/access` | Missing implementation |
| GET | `/api/v1/settings/appearance` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/settings/callgate` | Missing implementation |
| GET | `/api/v1/settings/callgate/webhooks` | Missing implementation |
| GET | `/api/v1/settings/compliance` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/settings/gateway` | Missing implementation |
| GET | `/api/v1/settings/gateway/phones` | Missing implementation |
| GET | `/api/v1/settings/gateway/webhooks` | Missing implementation |
| GET | `/api/v1/settings/google-oauth` | Missing implementation |
| GET | `/api/v1/settings/notifications` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/settings/notifications/muted-senders` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/system/errors` | Missing implementation |
| GET | `/api/v1/system/health` | Missing implementation |
| GET | `/api/v1/templates` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/templates/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/templates/categories` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/templates/{template_id}` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/validator/jobs` | Missing implementation |
| GET | `/api/v1/validator/jobs/{job_id}` | Missing implementation |
| GET | `/api/v1/validator/status` | Missing implementation |
| GET | `/api/v1/variables` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/variables/` | Implemented partially; native/full-workflow proof required |
| GET | `/api/v1/variables/contact/{contact_id}/profile` | Missing implementation |
| GET | `/api/v1/webhooks/callgate` | Missing implementation |
| GET | `/api/v1/webhooks/logs` | Missing implementation |
| GET | `/api/v1/webhooks/smsgateway` | Missing implementation |
| GET | `/apple-touch-icon.png` | Missing implementation |
| GET | `/connectors/{key}/mcp` | Missing implementation |
| GET | `/connectors/{key}/oauth/authorize` | Missing implementation |
| GET | `/connectors/{key}/oauth/register/{client_id}` | Missing implementation |
| GET | `/favicon.svg` | Missing implementation |
| GET | `/icon-192.png` | Missing implementation |
| GET | `/icon-512.png` | Missing implementation |
| GET | `/icon-maskable-192.png` | Missing implementation |
| GET | `/icon-maskable-512.png` | Missing implementation |
| GET | `/manifest.json` | Missing implementation |
| GET | `/mcp` | Missing implementation |
| GET | `/mcp/` | Missing implementation |
| GET | `/mcp/health` | Missing implementation |
| GET | `/oauth/approved` | Missing implementation |
| GET | `/oauth/authorize` | Missing implementation |
| GET | `/oauth/register/{client_id}` | Missing implementation |
| GET | `/sw.js` | Missing implementation |
| GET | `/{fp:path}` | Missing implementation |
| PATCH | `/api/v1/ads/audiences/{audience_id}` | Missing implementation |
| PATCH | `/api/v1/ads/calendar/{event_id}` | Missing implementation |
| PATCH | `/api/v1/ads/campaigns/{campaign_id}` | Missing implementation |
| PATCH | `/api/v1/ads/campaigns/{campaign_id}/followup-steps/{step_id}` | Missing implementation |
| PATCH | `/api/v1/ads/creatives/{creative_id}` | Missing implementation |
| PATCH | `/api/v1/ads/followup-steps/{step_id}` | Missing implementation |
| PATCH | `/api/v1/ads/followup-templates/{template_id}` | Missing implementation |
| PATCH | `/api/v1/ads/sets/{set_id}` | Missing implementation |
| PATCH | `/api/v1/ai-agent/sessions/{session_id}` | Missing implementation |
| PATCH | `/api/v1/campaigns/{campaign_id}` | Missing implementation |
| PATCH | `/api/v1/email/accounts/{account_id}` | Missing implementation |
| PATCH | `/api/v1/invoices/{invoice_id}` | Missing implementation |
| PATCH | `/api/v1/mailbox/{mailbox_id}` | Missing implementation |
| POST | `/api/v1/ads/audiences` | Missing implementation |
| POST | `/api/v1/ads/audiences/{audience_id}/attach` | Missing implementation |
| POST | `/api/v1/ads/audiences/{audience_id}/duplicate` | Missing implementation |
| POST | `/api/v1/ads/calendar` | Missing implementation |
| POST | `/api/v1/ads/campaigns` | Missing implementation |
| POST | `/api/v1/ads/campaigns/bulk` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/archive` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/audience/bulk-remove` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/complete` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/contacts` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/dispatch` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/duplicate` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/followup-steps` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/jobs/retry` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/launch` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/optimize` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/pause` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/rebuild-audience` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/resume` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/sets` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/simulate` | Missing implementation |
| POST | `/api/v1/ads/campaigns/{campaign_id}/validate` | Missing implementation |
| POST | `/api/v1/ads/contacts/bulk` | Missing implementation |
| POST | `/api/v1/ads/contacts/{contact_id}/action` | Missing implementation |
| POST | `/api/v1/ads/creatives/bulk` | Missing implementation |
| POST | `/api/v1/ads/creatives/{creative_id}/duplicate` | Missing implementation |
| POST | `/api/v1/ads/creatives/{creative_id}/promote` | Missing implementation |
| POST | `/api/v1/ads/followup-templates` | Missing implementation |
| POST | `/api/v1/ads/followups` | Missing implementation |
| POST | `/api/v1/ads/followups/process` | Missing implementation |
| POST | `/api/v1/ads/followups/{task_id}/{action}` | Missing implementation |
| POST | `/api/v1/ads/sets/preview` | Missing implementation |
| POST | `/api/v1/ads/sets/{set_id}/creatives` | Missing implementation |
| POST | `/api/v1/ads/sets/{set_id}/duplicate` | Missing implementation |
| POST | `/api/v1/ads/sets/{set_id}/pause-losers` | Missing implementation |
| POST | `/api/v1/ads/suppression` | Missing implementation |
| POST | `/api/v1/ads/track/click` | Missing implementation |
| POST | `/api/v1/ai-agent/actions/{action_id}/approve` | Missing implementation |
| POST | `/api/v1/ai-agent/actions/{action_id}/cancel` | Missing implementation |
| POST | `/api/v1/ai-agent/activity/{action_id}/undo` | Missing implementation |
| POST | `/api/v1/ai-agent/files` | Missing implementation |
| POST | `/api/v1/ai-agent/sessions` | Missing implementation |
| POST | `/api/v1/ai-agent/sessions/{session_id}/chat` | Missing implementation |
| POST | `/api/v1/ai-agent/sessions/{session_id}/files` | Missing implementation |
| POST | `/api/v1/ai-agent/settings/test` | Missing implementation |
| POST | `/api/v1/ai-agent/skills` | Missing implementation |
| POST | `/api/v1/ai-agent/skills/json` | Missing implementation |
| POST | `/api/v1/ai-agent/transcribe` | Missing implementation |
| POST | `/api/v1/ai/classify` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/auth/admin` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/auth/login` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/auth/logout` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/automations/` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/automations/test` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/autoreply/` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/autoreply/test` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/calendar/` | Missing implementation |
| POST | `/api/v1/calendar/{meeting_id}/cancel` | Missing implementation |
| POST | `/api/v1/calendar/{meeting_id}/complete` | Missing implementation |
| POST | `/api/v1/calendar/{meeting_id}/confirm` | Missing implementation |
| POST | `/api/v1/calendar/{meeting_id}/no-show` | Missing implementation |
| POST | `/api/v1/calendar/{meeting_id}/send-invite` | Missing implementation |
| POST | `/api/v1/calendar/{meeting_id}/send-reminder` | Missing implementation |
| POST | `/api/v1/calls/end` | Missing implementation |
| POST | `/api/v1/calls/log-direct` | Missing implementation |
| POST | `/api/v1/calls/register-webhook` | Missing implementation |
| POST | `/api/v1/calls/start` | Missing implementation |
| POST | `/api/v1/calls/test` | Missing implementation |
| POST | `/api/v1/campaign-followups/campaigns/{campaign_id}` | Missing implementation |
| POST | `/api/v1/campaign-followups/campaigns/{campaign_id}/process-now` | Missing implementation |
| POST | `/api/v1/campaign-followups/{followup_id}/run` | Missing implementation |
| POST | `/api/v1/campaigns/` | Missing implementation |
| POST | `/api/v1/campaigns/{campaign_id}/duplicate` | Missing implementation |
| POST | `/api/v1/campaigns/{campaign_id}/pause` | Missing implementation |
| POST | `/api/v1/campaigns/{campaign_id}/resume` | Missing implementation |
| POST | `/api/v1/campaigns/{campaign_id}/schedule` | Missing implementation |
| POST | `/api/v1/campaigns/{campaign_id}/start` | Missing implementation |
| POST | `/api/v1/campaigns/{campaign_id}/stop` | Missing implementation |
| POST | `/api/v1/campaigns/{campaign_id}/validate` | Missing implementation |
| POST | `/api/v1/contacts/` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/bulk` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/clean` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/enrich` | Missing implementation |
| POST | `/api/v1/contacts/import/csv` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/import/jobs` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/import/preview` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/import/url` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/quarantine/generated` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/validate-emails` | Missing implementation |
| POST | `/api/v1/contacts/{contact_id}/email-opt-in` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/{contact_id}/email-opt-out` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/contacts/{contact_id}/sms-opt-in` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/email/accounts` | Missing implementation |
| POST | `/api/v1/email/accounts/{account_id}/default` | Missing implementation |
| POST | `/api/v1/email/accounts/{account_id}/test` | Missing implementation |
| POST | `/api/v1/email/accounts/{account_id}/test-send` | Missing implementation |
| POST | `/api/v1/email/accounts/{account_id}/webhook/reveal` | Missing implementation |
| POST | `/api/v1/email/accounts/{account_id}/webhook/rotate` | Missing implementation |
| POST | `/api/v1/email/domains` | Missing implementation |
| POST | `/api/v1/email/domains/{domain_id}/verify` | Missing implementation |
| POST | `/api/v1/email/inbox/conversations/{conversation_id}/read` | Missing implementation |
| POST | `/api/v1/email/inbox/conversations/{conversation_id}/reply` | Missing implementation |
| POST | `/api/v1/email/inbox/conversations/{conversation_id}/status` | Missing implementation |
| POST | `/api/v1/email/messages/{message_id}/retry` | Missing implementation |
| POST | `/api/v1/email/preview` | Missing implementation |
| POST | `/api/v1/email/send` | Missing implementation |
| POST | `/api/v1/email/senders-preview` | Missing implementation |
| POST | `/api/v1/email/suppression` | Missing implementation |
| POST | `/api/v1/email/test-send` | Missing implementation |
| POST | `/api/v1/email/unsubscribe` | Missing implementation |
| POST | `/api/v1/followups/` | Missing implementation |
| POST | `/api/v1/followups/{followup_id}/reschedule` | Missing implementation |
| POST | `/api/v1/followups/{followup_id}/send-now` | Missing implementation |
| POST | `/api/v1/followups/{followup_id}/skip` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-close` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-interested` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-not-interested` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-opportunity` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/mark-unread` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/reply` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/resume-sequence` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/status` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/stop-sequence` | Missing implementation |
| POST | `/api/v1/inbox/conversations/{conversation_id}/{mark}` | Missing implementation |
| POST | `/api/v1/inbox/messages/{message_id}/feedback` | Missing implementation |
| POST | `/api/v1/inbox/poll-debug` | Missing implementation |
| POST | `/api/v1/inbox/poll-now` | Missing implementation |
| POST | `/api/v1/inbox/register-webhook` | Missing implementation |
| POST | `/api/v1/inbox/sync-full` | Missing implementation |
| POST | `/api/v1/invoices` | Missing implementation |
| POST | `/api/v1/invoices/links` | Missing implementation |
| POST | `/api/v1/invoices/paystack/webhook` | Missing implementation |
| POST | `/api/v1/invoices/public/{token}/pay` | Missing implementation |
| POST | `/api/v1/invoices/settings/test` | Missing implementation |
| POST | `/api/v1/invoices/{invoice_id}/duplicate` | Missing implementation |
| POST | `/api/v1/invoices/{invoice_id}/pay` | Missing implementation |
| POST | `/api/v1/invoices/{invoice_id}/payments` | Missing implementation |
| POST | `/api/v1/invoices/{invoice_id}/remind` | Missing implementation |
| POST | `/api/v1/invoices/{invoice_id}/rotate-link` | Missing implementation |
| POST | `/api/v1/invoices/{invoice_id}/send` | Missing implementation |
| POST | `/api/v1/invoices/{invoice_id}/void` | Missing implementation |
| POST | `/api/v1/lists/` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/lists/{list_id}/clean` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/lists/{list_id}/contacts` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/lists/{list_id}/contacts/add-all` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/lists/{list_id}/contacts/delete` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/lists/{list_id}/contacts/remove` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/lists/{list_id}/validate-emails` | Missing implementation |
| POST | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}/delete` | Missing implementation |
| POST | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}/flags` | Missing implementation |
| POST | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}/move` | Missing implementation |
| POST | `/api/v1/mail/messages/{mailbox_id}/{provider_message_id}/reply` | Missing implementation |
| POST | `/api/v1/mail/refresh` | Missing implementation |
| POST | `/api/v1/mailbox` | Missing implementation |
| POST | `/api/v1/mailbox/` | Missing implementation |
| POST | `/api/v1/mailbox/{mailbox_id}/check` | Missing implementation |
| POST | `/api/v1/mailbox/{mailbox_id}/filters` | Missing implementation |
| POST | `/api/v1/mailbox/{mailbox_id}/sync` | Missing implementation |
| POST | `/api/v1/mailbox/{mailbox_id}/test-send` | Missing implementation |
| POST | `/api/v1/mcp/connectors/{key}` | Missing implementation |
| POST | `/api/v1/mcp/connectors/{key}/revoke` | Missing implementation |
| POST | `/api/v1/mcp/connectors/{key}/test` | Missing implementation |
| POST | `/api/v1/mcp/tokens` | Missing implementation |
| POST | `/api/v1/notifications/push/subscribe` | Missing implementation |
| POST | `/api/v1/notifications/push/test` | Missing implementation |
| POST | `/api/v1/notifications/push/unsubscribe` | Missing implementation |
| POST | `/api/v1/notifications/read-all` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/notifications/{event_id}/read` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/send/` | Missing implementation |
| POST | `/api/v1/send/messages/{mid}/retry` | Missing implementation |
| POST | `/api/v1/send/scheduled/{sid}/cancel` | Missing implementation |
| POST | `/api/v1/send/scheduled/{sid}/retry` | Missing implementation |
| POST | `/api/v1/send/validate` | Missing implementation |
| POST | `/api/v1/sequences/` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/settings/callgate/register-webhook` | Missing implementation |
| POST | `/api/v1/settings/callgate/test` | Missing implementation |
| POST | `/api/v1/settings/gateway/phones` | Missing implementation |
| POST | `/api/v1/settings/gateway/phones/{phone_id}/register-webhook` | Missing implementation |
| POST | `/api/v1/settings/gateway/phones/{phone_id}/test` | Missing implementation |
| POST | `/api/v1/settings/gateway/register-webhook` | Missing implementation |
| POST | `/api/v1/settings/gateway/test` | Missing implementation |
| POST | `/api/v1/settings/notifications/{provider}/test` | Missing implementation |
| POST | `/api/v1/templates/` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/templates/preview` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/templates/{template_id}/duplicate` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/templates/{template_id}/to-email` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/validator/batch` | Missing implementation |
| POST | `/api/v1/validator/jobs` | Missing implementation |
| POST | `/api/v1/validator/jobs/{job_id}/cancel` | Missing implementation |
| POST | `/api/v1/validator/selection` | Missing implementation |
| POST | `/api/v1/validator/self-test` | Missing implementation |
| POST | `/api/v1/variables/` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/variables/preview` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/variables/sync` | Implemented partially; native/full-workflow proof required |
| POST | `/api/v1/webhooks/brevo` | Missing implementation |
| POST | `/api/v1/webhooks/brevo/{account_id}` | Missing implementation |
| POST | `/api/v1/webhooks/callgate` | Missing implementation |
| POST | `/api/v1/webhooks/smsgate/inbound` | Missing implementation |
| POST | `/api/v1/webhooks/smsgateway` | Missing implementation |
| POST | `/api/v1/webhooks/smsgateway/phone/{phone_id}` | Missing implementation |
| POST | `/connectors/{key}/mcp` | Missing implementation |
| POST | `/connectors/{key}/oauth/authorize` | Missing implementation |
| POST | `/connectors/{key}/oauth/register` | Missing implementation |
| POST | `/connectors/{key}/oauth/revoke` | Missing implementation |
| POST | `/connectors/{key}/oauth/token` | Missing implementation |
| POST | `/mcp` | Missing implementation |
| POST | `/mcp/` | Missing implementation |
| POST | `/oauth/authorize` | Missing implementation |
| POST | `/oauth/register` | Missing implementation |
| POST | `/oauth/revoke` | Missing implementation |
| POST | `/oauth/token` | Missing implementation |
| PUT | `/api/v1/ai-agent/memory/global` | Missing implementation |
| PUT | `/api/v1/ai-agent/memory/{session_id}` | Missing implementation |
| PUT | `/api/v1/ai-agent/presets` | Missing implementation |
| PUT | `/api/v1/ai-agent/settings` | Missing implementation |
| PUT | `/api/v1/ai-agent/settings/voice` | Missing implementation |
| PUT | `/api/v1/automations/{automation_id}` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/autoreply/{rule_id}` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/calendar/{meeting_id}` | Missing implementation |
| PUT | `/api/v1/calls/config` | Missing implementation |
| PUT | `/api/v1/campaign-followups/{followup_id}` | Missing implementation |
| PUT | `/api/v1/campaigns/{campaign_id}` | Missing implementation |
| PUT | `/api/v1/contacts/{contact_id}` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/invoices/settings` | Missing implementation |
| PUT | `/api/v1/lists/{list_id}` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/sequences/{sequence_id}` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/settings/access` | Missing implementation |
| PUT | `/api/v1/settings/appearance` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/settings/callgate` | Missing implementation |
| PUT | `/api/v1/settings/compliance` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/settings/gateway/phones/{phone_id}` | Missing implementation |
| PUT | `/api/v1/settings/gateway/routing` | Missing implementation |
| PUT | `/api/v1/settings/gateway/sim` | Missing implementation |
| PUT | `/api/v1/settings/google-oauth` | Missing implementation |
| PUT | `/api/v1/settings/notifications/muted-senders` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/settings/notifications/{provider}` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/templates/{template_id}` | Implemented partially; native/full-workflow proof required |
| PUT | `/api/v1/variables/{variable_id}` | Implemented partially; native/full-workflow proof required |
