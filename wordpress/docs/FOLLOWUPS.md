# Standalone follow-up scheduling and controls — incomplete workflow

Implemented in native PHP/WordPress: stored manual follow-up preparation; SMS/email/all views; local-day boundaries and live overdue flags; transactional skip, guarded delete and guarded reschedule. **There is no sending/notification/sequence execution worker or successful send-now queue operation.** No gateway/email connection or provider request is performed. Preparing a pending row is not a promise that it will execute. Native frontend, installation/HTTP/MySQL/concurrency qualification and the rest of the application remain unfinished.

## Routes and access

All paths below are under `/wp-json/sender-suite/v1` (the equivalent WordPress query transport is also supported). All require the existing **own-application** session/origin checks, no-store/request-ID responses and common outer transaction. A WordPress administrator cookie is not a bypass. Like the source, these data/control routes are not restricted to application admins; that is distinct from the separate provider/signing settings policy.

| Method | Path | Behavior |
|---|---|---|
| GET | `/followups` or `/followups/` | Defaults: `view=due_today`, `channel=sms`, `page=1`, `per_page=25`. |
| POST | `/followups/` | Prepare a future pending manual follow-up; returns 201 and the source-style item. Native also accepts the no-slash alias. |
| DELETE | `/followups/{id}` | Remove an unattempted/non-sending row and settle a valid linked continuation; returns 204. |
| POST | `/followups/{id}/skip` | Pending only; marks skipped and settles an eligible linked continuation. |
| POST | `/followups/{id}/reschedule` | Uses source query parameter `new_time`, **not** JSON. Sets future pending time and clears error after the history/consent/pause/link guards below. |
| POST | `/followups/{id}/send-now` | Missing ID -> 404, nonpending -> 409, pending -> **503**. Nothing is queued, claimed or sent. |

There are **six partially implemented original method/path contracts**, through three route patterns. The send-now path is registered to fail honestly but remains a **missing substantive source handler** in the ledger: a 503 is not a recreated queue/transport. Ads Manager tasks and `/campaign-followups` management are separate original APIs and are not implemented by these routes.

## Preparation and views

Input retains the original manual scheduling fields: positive `contact_id`, timezone-aware `scheduled_at`, nonblank trimmed `message_text` (up to 5,000 Unicode code points before trimming), `channel` (SMS/email, lowercased), optional subject (up to 500 code points), optional unified sender ID, `notify_on_due` (default false) and `max_attempts` (1–10, default 3). Unknown properties are not generic table assignments: callers cannot inject campaign/sequence links, status, attempts or message IDs. Invalid UTF-8/NUL text is rejected. Request bodies are bounded at 65,536 bytes. Dates must use an explicit-offset/Z ISO date-time compatible with native SQL date ranges; the full permissive Pydantic/Python date-input language, numeric epoch input and arbitrary fractional precision are not reproduced.

The contact must exist. SMS respects its SMS opt-out; email respects email opt-out, requires an email address and nonblank subject. Schedule time must be strictly future in UTC. These preparation-time checks do not authorize a later send: future workers must recheck suppression, consent, pause/reply state, sender availability and policy at execution time.

Email preparation without a sender is supported **only when both stored email account/mailbox inventories are empty**, matching the source's disconnected preparation case. Explicit missing sender IDs return 404. Existing explicit senders or any unresolved default inventory return 503, without a placeholder row or silently choosing the wrong From address. Native sender/default/credential resolution, including inactive accounts and signed mailbox IDs, is not implemented; the source's broader sender selection is not claimed here. SMS preparation does not require a live gateway or create a conversation/message/send job.

Views retain source semantics:

- `due_today`: pending, scheduled within today's local midnight bounds.
- `overdue`: pending before **today's local midnight**, not every timestamp earlier than the present moment.
- `upcoming`: pending at/after the next local midnight.
- `pending`: all pending dates; `completed`: sent/delivered; `skipped`: skipped/cancelled/failed; `all`: all states.
- `is_overdue` is calculated live as pending and scheduled strictly before now, even for a row in `due_today`. The stored legacy overdue flag is not trusted.
- SMS/email/all filters are validated; legacy null channel is treated as SMS. Names prefer person, business, phone, then email; absent contacts display Unknown. Raw nonempty provider errors are replaced with a generic error-presence message, not exposed.
- Page size is 1–100, page is 1–1,000,000. The response retains total/items/page/per_page/next_page. Ordering adds ID as a deterministic scheduled-time tie breaker; source tie order is not universally specified.

`Config` now activates **nine** encrypted auth/scheduling settings, including `DEFAULT_TIMEZONE` (default `Africa/Lagos`). It accepts recognized PHP IANA timezone identifiers; invalid stored/updated values fail validation rather than silently choosing UTC. This setting is consumed by follow-up day views, not by an implemented quiet-hours/delivery scheduler. A dedicated settings UI/API to administer it remains outstanding; existing internal configuration persistence/setup preserves it. Local next-midnight calculation uses a calendar day, not a fixed 86,400-second increment. Source comparisons cover New York 23/25-hour DST days, Lagos, UTC, Nepal's fractional offset and Samoa.

## Transactional controls and intentional source safety differences

Direct service mutations, like public dispatch, must be wrapped in `Database::transaction`. The follow-up claim/change/delete and linked campaign-contact update share that transaction. Detected stale conditional writes return 409; database failures return 503. A linked-row failure rolls back the child mutation, not just the parent. Follow-up/membership timestamps advance monotonically even with a repeated/fixed clock, so a no-op-looking reschedule still makes a conditional version change.

- Skip requires pending. Existing pending/queued/sent/completed membership is completed and `next_action_at` cleared. Reply/opt-out/cancelled/other terminal membership labels are preserved instead of overwritten.
- Delete/reschedule require pending/skipped/cancelled/failed **and no attempt trace**: zero attempts, null executed time and null message ID. Sent, delivered, sending and attempted history cannot be purged/rearmed through these controls. The source forbids sent/sending deletion but permits much broader rescheduling/deletion; native intentionally hardens this to avoid duplicate-send/history loss.
- Reschedule checks current channel-specific opt-out/address state, rejects unsupported stored channels, and refuses to rearm while **any conversation for that contact is sequence-paused**, including a different channel. Creation remains disconnected preparation, not pause bypass for actual delivery.
- A linked continuation must reference an existing campaign-contact with matching campaign/contact identity. Missing/unlinked automation ownership fails rather than acknowledging an automation that cannot be settled. Reschedule may queue eligible pending/queued/sent/completed membership, but cannot overwrite replied/opted-out/failed/cancelled/other terminal membership. Resuming an inbox flag alone does not make an ended campaign resumable.
- Reschedule retains attempts/execution/history fields; it does not reset an attempted failure into a fresh provider request. Manual retry/recovery for attempted or ended automations needs a separate, still-unimplemented audited workflow.

These are application guards plus synthetic fault evidence, **not** proof that an existing/in-flight external sender is stopped. Native workers must implement compatible atomic claims, idempotency and consent/pause checks. Campaign lifecycle completion/counters, sequence step ownership/version migration, imported inconsistent history, contention/deadlocks and crash/retry recovery still require implementation/qualification. No automatic scheduling, notification delivery or sending cron is registered by this module.

## Bounds, privacy and evidence

Listings batch contact lookup. SQL projects only needed follow-up fields and error presence; it does not fetch raw provider error strings into the listing response. A bounded page preflight rejects estimated raw payload above 8 MiB before loading message text, and serialized JSON is independently capped at 8 MiB. This is not streaming or real native host/wire/memory qualification: JSON escaping and WordPress/SAPI response buffering still require testing. Retention, large-thread/export UI and operational recovery remain release work.

`generate_followup_fixtures.py` executes selected **actual original** follow-up DTO/API/timezone/naming AST with fixed-clock synthetic SQLAlchemy/SQLite, stripping application imports and injecting the clock into the serializer's local datetime import. Its sender test double represents explicitly empty inventory only; it is not source sender/transport proof. The fixture records 47 operations, 11 DTO cases and six timezone boundary cases. Relationships/FKs/concurrency are not simulated as native qualification.

`followups-test.php`: **212 assertions per PHP-WASM 7.4/8.3 runtime**, including source responses/state, timezones, history/attempt guards, consent/pause protection, terminal/link ownership, statement/conditional-write faults across all compound mutations, preparation failure, monotonic timestamps, honest send-now refusal, redaction, page bounds and encrypted active-timezone validation/reload. Shared dispatcher tests add own-app authentication/origin checks, 201/204, source query shape, path-ID protection, body/field bounds, no-store responses, failure rollback and proof of no message/sending-worker creation.

`native-http.py` now contains preparation/list/skip/reschedule/delete and unavailable-send-now probes against an isolated native site. **They have not run here.** No actual native PHP/MySQL/WordPress installation, HTTP/browser workflow, background follow-up worker, provider connectivity, send or notification certification is claimed. The complete application and release package remain unfinished; no interim ZIP is produced.
