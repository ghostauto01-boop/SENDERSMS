# Changelog

## Unreleased — follow-up steps: every parameter honoured, editable, chainable

A campaign's follow-up chain was write-only: the POST ignored most of what it was
sent, the row defaulted to a 48-hour SMS that nobody asked for, and once created a
step could not be edited or removed at all. The sending engine, meanwhile, raised
`NameError` on the first due task, so **no follow-up ever went out**. This release
fixes the engine and makes the whole chain a first-class API. Full contract:
`docs/FOLLOWUP-STEPS.md`.

- **`POST /ads/campaigns/{id}/followup-steps` honours the request.** `delay_hours`
  used to be discarded (Pydantic ignored the unknown field) and the step was stored
  with the hardcoded `wait_hours: 48`; `action`, `subject` and `body` were only
  stored when the client happened to use the column names. The endpoint now takes
  the documented `delay_hours` (kept in step with the legacy `wait_hours`, and a 422
  if the two disagree), stores the time, action, subject and body it was given,
  defaults `action` to `send_email` on an email campaign (and `send_sms` otherwise)
  and returns the whole step instead of `{"id": …}` — with both `wait_hours` and
  `delay_hours` in the payload so neither client has to guess.
- **Steps can be edited and deleted.**
  `PATCH /ads/campaigns/{id}/followup-steps/{step_id}` updates any subset (delay,
  action, condition, subject, body, active flag, order, template) and
  `DELETE …/{step_id}` soft-deletes: the step stops sending, its pending follow-ups
  are cancelled, and its history survives (`?hard=true` removes the row). Editing
  the wait moves the follow-ups already queued for that step, so a corrected delay
  takes effect immediately instead of only for the next contact. The old
  `/ads/followup-steps/{id}` routes stay as aliases, and the campaign-scoped ones
  404 on a step belonging to a different campaign. At last
  `GET /ads/campaigns/{id}/followup-steps` lists a chain in order and
  `GET /ads/followup-steps/{step_id}` returns one step with its pending/sent counts.
- **Bad input is a 422, not a silent default.** `action` and `condition` are
  validated against the values the engine actually supports, `delay_hours` is
  bounded (0–8760), and the app's standard error envelope names the offending field
  (`delay_hours: Input should be greater than or equal to 0`).
- **The engine actually sends.** `process_followups` referenced an undefined
  `email_campaign`, so the first due task raised `NameError` and every follow-up on
  every campaign died there — the "follow-ups never arrive / arrive blank" report.
  The channel is now decided per step (`send_email` sends email even on an SMS
  campaign; a `send_sms` step on an email campaign still goes out as email so an
  older step is never dropped), consent checks follow that channel, and a step with
  no body is cancelled with a reason instead of being counted as sent.
- **More than one step works.** The chain used to queue every step up front off the
  campaign's own send time and then ignore order. Steps are now chained: step 1 is
  queued when the campaign sends, step N+1 is queued when step N's message actually
  goes out, due `delay_hours(N+1)` later, and each step re-checks its own condition
  against the message before it. A reply, opt-out, bounce or suppression therefore
  ends the chain exactly where it is detected, and the `(step_id, contact_id)`
  unique row still guarantees one send per contact per step.
- **Conditions beyond `no_reply`.** `opened_no_click`, `clicked_no_reply`,
  `negative_reply` and `positive_reply` are supported alongside the existing
  sentiment, lead-status, open/click and meeting tests. Replies are classified with
  the existing keyless AI classifier (`Message.ai_sentiment`, the ads reply
  classifier as fallback); opens and clicks come off the previous message, so the
  same condition works on both channels.
- **Personalization works in follow-ups.** `subject` and `body` are rendered for the
  contact at send time through the same renderer creatives use — on email inside
  `queue_email`, on SMS through `render_for_contact` — so `{{contact}}`,
  `{{business_name}}` and every imported custom field resolve. `{{contact}}` is now
  a first-class greeting alias (full name → business name → "there") everywhere in
  the app, not just in follow-ups.
- **Follow-up template library.** `POST/GET /ads/followup-templates` (plus GET /
  PATCH / DELETE by id) save a reusable step; a follow-up step created with
  `template_id` inherits its subject, body, action, condition and delay (explicit
  request fields still win), and the template's `usage_count` records the reuse. The
  campaign's Automation tab can start a step from a saved template and save the one
  being written as a new template.
- **New columns stay additive.** `ads_followup_steps` gains `template_id`,
  `updated_at` and the `deleted_at` soft-delete marker; `ads_followup_templates` is
  new. Both arrive via `create_all` on a fresh database and the startup schema
  repair on an existing one.

## Unreleased — the right inbox for the channel, an email Conversations page, and Gmail Google sign-in

Three complaints from one screen, fixed together because they share a cause: the
email channel was bolted onto an app whose links, pages and reply setup were all
written for SMS.

- **Clicking an email campaign's reply no longer opens the SMS inbox.** Every
  deep link into an inbox said `/inbox`, whatever the campaign had sent on, so an
  email conversation opened a page with no thread for that person — an empty chat
  list and no way to answer. The channel now decides the destination:
  `GET /overview/campaigns` builds `inbox_url`/`replies_url` per campaign channel
  (`/email-inbox?campaign_id=…&view=replied` vs `/inbox?campaign_id=…&replied=1`),
  the campaign replies drawer routes each row (and its footer button, which now
  says *Open in Email Inbox* / *Open in SMS Inbox*), and the Campaigns page,
  Overview stat cards and the Send page's "view in inbox" all go through one
  helper (`utils/inboxRoute.ts`) instead of hardcoding a path.
  - `GET /campaigns/{id}/conversations` and `/{id}/performance` are scoped to the
    campaign's own channel. A contact can hold an SMS thread and an email thread,
    and an email campaign's "leads"/"replies" used to count the text conversation
    too — the numbers and the list now agree. Each row also carries `channel` and
    `contact_email`, so an email lead shows an address instead of an empty phone
    line and no longer gets a WhatsApp call button there is no number for.
- **New: Email Inbox → Conversations** (`/email-inbox?view=replied`, in the
  sidebar). Only the people who actually wrote back — a thread qualifies when it
  has an inbound message, so campaign mail nobody answered stays on the *All
  mail* page. Backed by `replied_only` on `GET /email/inbox/conversations`, which
  also gained `campaign_id` and `contact_id` (the deep link a campaign's reply
  row sends: the list arrives filtered and that person's thread opens itself) and
  now reports `replied_total` and `thread_total` so both tabs carry a real count.
  The MCP `email_inbox` tool takes the same two filters.
- **Gmail replies now sign in with Google, from inside the app, for as many
  inboxes as you want.** Full walkthrough: `docs/GMAIL-OAUTH.md`.
  - **Settings → Gmail replies** (new tab, deep-linkable as `/settings?tab=gmail`)
    shows the exact values the Google Cloud Console asks for — the authorized
    redirect URI for *this* deployment, the JavaScript origin, the scope — each
    with a copy button, next to a numbered setup guide (project → enable the
    Gmail API → consent screen with **test users** → **Web application** OAuth
    client → paste the credentials → connect → add another → never-spam filter)
    and the failure modes with their fixes (`redirect_uri_mismatch`,
    `access_blocked`, no `refresh_token`, `invalid_client`, `invalid_grant`).
  - The OAuth client is saved in the app: `GET`/`PUT /settings/google-oauth`,
    stored encrypted in `system_settings`, secret write-only (never returned),
    client ID reported masked. Values saved here win over
    `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, which still work; every read
    reports the source. "Connect with Google" was previously dead on any
    deployment whose operator could not edit server variables.
  - **Several Gmail inboxes:** the consent URL now asks for
    `prompt=consent select_account`, so Google always shows the account chooser
    instead of silently reusing the signed-in account, and each inbox gets its own
    refresh token, poll interval and counters. The mailbox also stores the client
    secret it was connected with, and `access_token_for` falls back to the
    app-saved client, so the poller (which sees no request and no environment
    override) can still refresh a mailbox connected from the UI.
  - The consent round-trip returns you to the page you started on: `return_to`
    travels inside the signed, 10-minute `state` (same-origin paths only — an
    absolute or protocol-relative one is dropped, so the redirect cannot be
    turned into an open one).
  - The IMAP **app password** path is unchanged and still offered, as the fallback
    for anybody who does not want a Google Cloud project; the Setup Guide now
    leads with Google sign-in and documents the app password in the same step.

## Unreleased — reliability, imports, contact hygiene, and compliance

- **P0-6 follow-up — sender-domain authentication is informational, not a launch gate.** Campaigns retain the existing configured email send route. Valid free-mail From addresses can be saved, and neither a free-mail address nor a missing/pending domain-authentication record prevents campaign launch; the provider's response at delivery time remains authoritative. A usable email account/API key and the existing audience, consent, and sending-rule checks still apply. Optional domain setup and its DNS guidance remain documented in `docs/SENDING-DOMAINS.md`.
  - The `SendingDomain` API remains available: `POST/GET/DELETE /email/domains`, `POST /email/domains/{id}/verify` and the tools `register_sending_domain`, `list_sending_domains`, `verify_sending_domain`. Its plan includes the Brevo verification code, DKIM and exactly **one** DMARC record: the domain's existing record is read and the `rua` merged into it (a policy is never weakened, several existing records become one), and the plan says to replace, never add. Free-mail domains remain unsupported for this optional DNS-registration helper because their DNS is not controlled by the sender.
  - The optional warm-up ramp (5/day in week one, 10, 18, 25/day from week four) remains available for registered/authenticated domains; launch eligibility no longer depends on domain-authentication status.
  - §E: `POST /contacts/quarantine/generated` (tool `quarantine_generated_addresses`) tags every pattern-generated address `pattern_generated` + `sendable:false` — a dry run unless `dry_run=false`, idempotent, never deletes. RFC 8058 one-click headers were already sent (confirmed on the wire in the simulator).
  - **Not done:** custom tracking domain, the DKIM=pass integration test, any run against a live Brevo or real DNS.
- **Sprint 2 (QA findings C1–C11).**
  - **C1 — preview honours `contact_id`.** `POST /templates/preview` takes `contact_id` and renders through `variable_service.render_preview`, the registry-aware path the senders use (imported columns, aliases, fallbacks); explicit sample values still override. The `render_text_preview` tool gains `contact_id` and loses the hidden sample defaults that would have beaten the contact's own values; `preview_template` hands the contact over by id instead of copying nine fields.
  - **C2 — unknown variables are no longer silently deleted.** Saving a template that uses a variable that does not exist is a 422 (`UNKNOWN_TEMPLATE_VARIABLES`, with a "did you mean"); a preview shows `[UNKNOWN VARIABLE: name]` and lists it in `unknown_variables`/`warnings`; campaign `validate` reports it. A real send still removes it rather than print a marker to a customer (default `unknown="drop"`) — you can no longer reach that state through the API. A column imported since the last registry scan counts as known.
  - **C3 — `reply_to_conversation` works.** The endpoint read `body`/`template_id` from the query string while the tool sent a JSON body, so every call was a 422. It now takes either, answers on the conversation's own channel (an email thread is answered by email — it used to text the contact), and an SMS reply (or direct send, scheduled send, or inbound text) is attached to the contact's **text** thread, not whichever conversation they had first.
  - **C4 — follow-ups.** `list_followups` had no parameters, so the endpoint's "due today, SMS only" defaults applied and a follow-up scheduled for next week, or an email one, never appeared. New `view=pending` (every follow-up not yet sent), the tool defaults to `pending` + both channels (the page's defaults are unchanged), responses carry `page/per_page/next_page`, a bogus `view`/`channel` is a 422, and `DELETE /followups/{id}` (and the `delete_followup` tool) removes one that has not gone out (a sent one is a 409; a campaign's follow-up ends that contact's automation, as skipping it does).
  - **C5 — no invented phone numbers.** Emailing a typed address, scheduling an email to one, and inbound mail from an unknown sender created contacts with a made-up `phone_number` (`email:<address>` cut to 20 characters) and `country: Nigeria`. They now have neither. A startup repair (and `scripts/migrate_existing_db.sql`) clears existing placeholder phones. The `country` default is gone from the contact tools.
  - **C6 — one set of numbers.** `dashboard/stats`, `analytics/*`, `email/stats` and `email/overview` count through `services/message_metrics` (see `docs/METRICS.md`): the same scope, window edge, and meaning of delivered/failed; every response carries `scope` and `definitions`. **Behaviour change:** the analytics endpoints and `send/history` are SMS-only unless `channel` says otherwise (email rows used to inflate them); the funnel and charts no longer count a cancelled message as failed; email rates have one decimal like the others.
  - **C7 — tool contracts.** A test walks every tool against the app's OpenAPI and fails on a parameter the endpoint does not read, a required field the tool cannot send, an enum value the endpoint rejects, or an unlisted default mismatch (`docs/TOOL-CONTRACTS.md`). It found and the sprint fixed: `create_campaign` offering `daily_limit`/`min_delay` (the classic engine stores but never reads them), `email_inbox.unread_only` (ignored; now implemented). The dispatcher now **applies** a tool's declared default when an argument is omitted (it only advertised it), and refuses an out-of-enum value before calling the app.
  - **C8 — validation.** A bogus `channel` on a template is a 422 (it silently became `sms`); an email template needs a subject; a list name that is taken (ignoring case and spaces) is a 409 naming the list (create, rename, and an import that creates a list), a blank one a 422; inbox filters reject a bogus `channel`.
  - **C9 — refused sends say why.** Email and SMS send responses carry `skipped`, `skipped_reason`, `skipped_reasons` (counts by code), `skipped_details` and a plain `message`. An SMS list send that hit the rate limit part-way no longer drops the recipients it never reached from the response.
  - **C11 — HTTP semantics.** `PATCH /campaigns/{id}` works (same operation as `PUT`; it was a 405); an invalid conversation mark is a 422 listing the valid ones (it was a 405) and the tool refuses it before calling the app. (A missing campaign has been a 404 since P0-1.)
- **C10 — webhook token and device id are masked in reads; the token can be rotated.** Full description: `docs/WEBHOOK-SECURITY.md`.
  - The Brevo webhook token (the credential for the inbound/events endpoint) was returned in full by every account read, so it ended up in logs, HAR files and assistant transcripts. `webhook_url`, `webhook_path` and the setup guide now carry `token=****a1b2`; a new `webhook_token_masked` field is the hint alone. A gateway device `id` in `/inbox/device-info` and `/inbox/poll-debug` is reduced the same way.
  - New `POST /email/accounts/{id}/webhook/reveal` (the full URL, not cacheable, logged — the dashboard's copy buttons call it on click) and `POST /email/accounts/{id}/webhook/rotate` (a new token; **the old one stops working immediately**, so paste the new URL into Brevo straight away). Both are refused to the MCP assistant bridge (`403 SECRET_NOT_AVAILABLE_TO_ASSISTANTS`), including through the generic `api_request` tool: `call_app` marks its requests with `X-Sendsms-Via: mcp`.
  - The webhook compares tokens with `hmac.compare_digest` and also accepts the token in an `X-Webhook-Token` or `Authorization: Bearer` header (keeps it out of access logs). New tokens are 256-bit. Email Manager gets a *Rotate token* button.
  - **Behaviour change:** API clients that read `webhook_url` from an account to paste it somewhere must call `…/webhook/reveal` instead.
- **P0-5 — an address nobody has verified is not sendable.** Full description: `docs/EMAIL-VERIFICATION.md`.
  - **The bug:** `unknown` (never checked, or the verifier could not decide) was treated like `deliverable` by every send path. In a 5-contact reproduction (3 verified, 2 never checked) `validate` said valid, `start` queued everyone, and 2 of the 5 messages went to unverified addresses.
  - New contact field `email_verdict` (`deliverable | risky | undeliverable | unknown`), stored with the existing `email_verified`, `email_verified_at` and `email_confidence`. Outreach (classic campaigns, the Ads Manager, follow-ups, bulk mail) is never sent to `unknown`; `risky` (catch-all, role, disposable) is sent **last**; one-to-one mail, replies and reply addresses are not gated. **Behaviour change:** a contact imported from a CSV is `unknown` until it is verified, so it is skipped by campaigns until then. `EMAIL_REQUIRE_VERIFIED=false` switches the gate off for closed test environments.
  - `validate` (classic and Ads Manager) now **fails** an email campaign whose audience contains unknown contacts, with the count (`audience.unverified`); `start` refuses it. The audience screening and the send path now share one rule (screening used to lack the `inferred` check). Editing a contact's address clears its verdict.
  - Validator: results carry a normalised `status` (`valid | invalid | risky | unknown`) and `confidence`; `apply_verdict` persists the verdict and confidence (an `unknown` result never undoes an earlier proof). `GET /validator/status` reports whether a mailbox can really be confirmed (`smtp_enabled`, `smtp_reason`, `can_confirm_mailboxes`): it needs a Reacher service, or an outbound port 25 that answers with an SMTP greeting (a bare TCP connect is not enough — in the test sandbox the connection is accepted and then closed with no banner).
  - `POST /validator/batch` takes up to 25 rows (was 5). New queued, resumable jobs: `POST /validator/jobs`, `GET /validator/jobs[/{id}]`, `POST /validator/jobs/{id}/cancel` verify any number of contacts in the background and write every verdict as it goes. MCP tools `validator_status`, `verify_contacts`, `verification_job`.
  - Audience population for a classic campaign is set-based (it issued several queries per contact) and queues deliverable contacts before risky ones.
- **P0-4 — nothing is silently truncated; every tool response is valid JSON.** Full description: `docs/MCP-RESPONSES.md`.
  - **The bug:** the MCP layer returned `text[:6000]` of an endpoint's JSON and cut a non-JSON body (the CSV export) to its first 4,000 characters. A 5,000-contact fixture gave 26 complete rows from `export_contacts_csv` and an unparseable half-string from `search_contacts per_page=100`, and nothing said either result was partial.
  - Every tool result's text is now ONE valid JSON object, success or error: `{ok, http_status, request, truncated, has_more, returned, total, next_cursor, cut, note, data}`. A result that is too large (30,000 characters) is shortened by **whole items**, never mid-item, and says so: `cut` records what was asked for and what was kept, and `next_cursor` is a request that continues with no gap and no repeat. `structuredContent` is bounded the same way and carries `_truncated` when anything was cut. `truncated` means more results exist beyond this response, so it is false on the last page. **Behaviour change:** clients that parsed the old `METHOD path → HTTP n` text must read the envelope (the endpoint's own payload is `data`). Failures are `{ok:false, http_status, error, message}`. Resources (`sendsms://…`) are bounded by the same rules instead of `[:12000]`.
  - New `GET /api/v1/contacts/export` (the MCP `export_contacts_csv` now calls it): keyset-paginated by contact id with an opaque `cursor`, `limit` (1–500), `max_bytes`, `format=csv|json` and the same filters as `/export/csv`. Each page reports `returned`, `total`, `truncated` and `next_cursor`; only the first page has the header row, so the pages concatenate into one CSV. A cursor from different filters, or a malformed one, is a 422. 1,297 contacts yield 1,297 rows. The streaming `/export/csv` is unchanged (it now reads tags in one query instead of one per contact).
  - Page-size ceilings are enforced instead of silently clamped: `per_page` above the documented maximum is a 422 (`/inbox/conversations` 500, `/webhooks/logs` 200, `/mcp/activity?limit=` 200, contacts 100, email history 200). The MCP tool schemas now carry `minimum`/`maximum`, a test keeps them equal to the OpenAPI ones, and `search_contacts`/`list_campaigns` default to 25.
  - `GET /contacts/` also reports `page`, `per_page` and `next_page`, and its ordering has an `id` tiebreaker (a bulk import gives many contacts the same `created_at`, so paging could skip or repeat one).
- **P0-3 — outbound throttles on by default, for email too; bounce circuit breaker.** Full description: `docs/SENDING-RULES.md`.
  - Sending-rule defaults are now ON: daily limit, a 09:00–17:00 window, weekends off. Email gets a cap tied to the mailbox count: `email_daily_per_mailbox` (default 30, env `EMAIL_DEFAULT_DAILY_LIMIT`) × usable mailboxes; an account's own `daily_limit` can only lower it. SMS and email volume are counted separately. **Behaviour change:** a campaign started at night or on a weekend now waits for the window (the start response says so and when it will go).
  - The gate is now channel-aware and also governs email. `_send_one` used to skip it for email ("no SIM to protect"). Producers (the classic engine and the Ads Manager) now ask `SendingGate.room()` how many messages may go out and create only that many; a message past the cap or window is *deferred*, not failed. One-to-one replies are never throttled.
  - Held mail: a paused campaign sends nothing (mail created before the pause is held and released on resume); stopping cancels queued mail.
  - `validate` (classic and Ads Manager) reports a `projection` with sendable count, daily cap, sending days, calendar days and projected finish date.
  - Circuit breaker (`services/circuit_breaker.py`, both campaign systems): pauses an email campaign above 2% bounces/refusals or 0.10% spam complaints over a rolling 7 days (minimum 20 sends), records the reason in `paused_reason`, checks on each bounce webhook, after provider refusals and on a 5-minute beat/inline sweep. Resuming (`/resume`, `/start`, ads `/launch`, bulk resume) needs `acknowledge_breaker=true`, which also restarts the window.
  - One-time startup step applies the safe defaults to a database whose throttles are all still at the old open values; an operator's own configuration is never touched.
  - New `PUT /settings/sending-rules` parameters: `email_daily_per_mailbox`, `breaker_enabled`, `breaker_bounce_pct`, `breaker_complaint_pct`, `breaker_min_sample`, `breaker_window_hours`. `GET …/status` now includes `email` capacity/usage and the breaker. Settings → Sending Rules shows and saves them; the Ads Manager and Campaigns pages show why a campaign is paused and confirm before an override.
  - **Also fixed (found while reproducing):** an email campaign that carried its own message and no template (and no attachments) raised `UnboundLocalError` on its first contact, so it could never send. It is fixed together with the throttles on purpose: fixing it alone would have released an unthrottled send.
- **P0-2 (writes).** The MCP campaign tools that act on one campaign (`pause`, `resume`, `start`, `validate`, `delete`, `schedule`, `update`, `campaign_analytics`) now take `kind`. Without it the id is looked up first; an id that exists in both systems is refused with the candidates listed instead of acting on whichever was found. Ads Manager campaigns are routed to their own endpoints (`start` → `launch`); actions the Ads Manager does not have are explained.
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
