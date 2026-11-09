# Experimental single-contact SMS outbox — default OFF

This stage connects the native SMS-Gate transport to persistent messages, a WordPress cron callback and delivery polling. It is **not native-certified or production-ready**, and does not complete the original send/campaign/bulk/scheduling workflows. No real HTTP request or SMS was sent during the offline checks.

## Activation boundary

Unless the hosting operator explicitly defines `SENDER_SUITE_ENABLE_SMS_OUTBOX` as boolean `true`, the POST send path returns 503 and no SMS worker hook is installed. A configured sender alone never enables delivery. Do not enable this experimental flag on a production messaging site before native security/concurrency/recovery/provider qualification. Status reads remain available for retained native outbox rows even when delivery is disabled.

When enabled, the worker shares the existing five-minute cron recurrence, registers its own `sender_suite_sms_outbox` hook and schedules outside the application's enqueue transaction. Deactivation clears that hook without deleting history. Real WP cron dispatch, low-traffic hosting and scheduler recovery have not been qualified. This is not a persistent high-throughput campaign engine.

## Implemented API subset

`POST /wp-json/sender-suite/v1/send/` accepts **JSON only**, with exactly:

- `contact_id`: existing, explicitly opted-in contact;
- `gateway_setting_id`: explicitly selected native cloud sender;
- `body`: final literal text, subject to the five-segment safety limit;
- `Idempotency-Key` header: 8–128 ASCII letters/digits or `._:-`.

The application session and application's admin role are required. A WordPress administrator cookie alone is insufficient. URL payloads are rejected. Requests are limited to 8 KiB; text is bounded to 1,600 Unicode characters/6,400 bytes, and any braces are rejected until template-send integration is completed. The selected sender must be enabled, cloud SMS-Gate and have no configured daily cap: **nonzero daily caps fail rather than being ignored**. Automatic sender routing/failover is not implemented.

The result is **202 queued**, never a synchronous “sent” claim. An exact idempotent replay returns the original row and current state; reusing the key with different contact/sender/text returns 409. Keys are scoped to the authenticated user and stored as hashes. A unique message key and unique per-channel conversation identity supply the final database race protection; a conflicting insert may surface retryable 503 instead of 409. Failed transaction commits produce no durable queued message and never call a provider.

`GET /wp-json/sender-suite/v1/send/messages/{message_id}` is a native extension, not an extra source contract. It returns only state, provider identity, timestamps and `needs_review`. Owners and application admins may read it; unrelated viewers receive 404. Message content, credential fingerprints, passwords, ciphertext and private metadata are omitted. Responses are no-store.

The original query-based raw-number/list/scheduled send variants are **not implemented here**. Requests for `phone_number`, `list_id` or `schedule_at` fail explicitly. Original UI integration, template personalization, campaigns, sequences, bulk and scheduled execution still need implementation.

## Durable state machine

1. **Enqueue transaction:** rechecks explicit `has_consented=true`, `consent_status=opted_in`, opt-out, suppression, undeliverable status and the existing Nigerian mobile-number filter; stored numbers must already be canonical E.164 to avoid legacy formatting/suppression ambiguity. Persist one queued message and, when needed, one SMS conversation. No network call or accepted-send counters occur here.
2. **Serialized worker:** acquire the native queue advisory lock; refuse to run inside a caller's transaction/savepoint. Select one queued native row. Check contact/suppression/phone again and recheck sender admission. A hash of the encrypted credential envelope and sender connection fields binds the message to its enqueue-time configuration; notes-only changes do not invalidate it. Raw credentials never enter persisted metadata. The worker also verifies stored text against the enqueue-time request digest; changed bodies fail before submission.
3. **Claim transaction:** atomically set sending, attempt count 1 and an interrupted-claim deadline, then commit. Only after that commit may the transport make one POST. The worker does not inherit an API transaction.
4. **Acknowledgement transaction:** persist an actual provider ID only when one was returned. Update contact/conversation counters once for an acknowledged submission, not for a queue entry or timeout. Pending provider states remain sending; an observed sent state has a sent timestamp. Immediate terminal states flow through the existing receipt reconciler. Duplicate provider identities cause review rather than wrong attribution.
5. **Polling:** known pending/sent identities are checked with GET, no sooner than five minutes after the previous observation. Each tick may submit one queued row and poll one due row; a backlog of new rows therefore does not starve every status check. Exact-ID and explicit-status validation is shared with the transport. Poll/receipt races preserve the first terminal state and avoid duplicate failure effects. Failed poll commits can safely repeat GET; they never repeat POST.
6. **Ambiguity:** a timeout, lost/malformed response or interrupted claim becomes unknown/manual review. A failed final DB commit leaves the committed claim intact; after its deadline it becomes unknown, **never queued**. The gateway may already have accepted it. A 401/429 or other explicit rejection becomes failed; even those POSTs are not automatically retried in this conservative first worker.

Unsubmitted queue entries expire after one hour rather than sending unexpectedly late. Unresolved known-provider observations stop after 24 hours and become manual-review unknown while retaining the actual provider ID.

A worker crash before the actual POST may therefore result in a message that was never sent but still requires review. That is intentional: without a proved provider-side idempotency/lookup protocol, silently resending is unsafe. There is no automatic “exactly once delivery” claim. A full operator recovery UI, safe reconciliation of unknown identities and any provider-specific guaranteed-idempotent retry protocol remain outstanding.

## Concurrency/security limits still requiring qualification

The admission checks occur immediately before committing the claim, **not atomically with the external carrier accepting the request**. An opt-out/disable can arrive while a request is in flight. Native locking coordination across consent edits, campaign workers, deletions, gateway configuration and callbacks still needs review/testing. Future send engines must share the same identity/claim safety invariants; this worker alone cannot certify them.

The adapter tests simulate advisory lock availability, unique indexes and statement failures; they do not exercise real concurrent PHP processes, MySQL connection loss/reconnect, native isolation/locking/collation, DNS/TLS/cURL or actual gateway semantics. The feature stays default-off for that reason. Stored source metrics may count outgoing rows including queued messages; do not confuse those source definitions with carrier delivery counts.

## Evidence

- `sms-outbox-test.php`: 126 assertions per PHP runtime, with a fake pinned gateway and synthetic SQLite unique indexes. Covers idempotent enqueue, pre/post-claim consent and sender checks, no network before durable claims, sender-change binding, counter effects, every worker commit boundary, interrupted claims, ambiguous/rejected/terminal outcomes, poll/callback races and safe GET retry after commit failure.
- `sms-outbox-routes-test.php`: 40 assertions per runtime with the flag enabled, synthetic sessions/WordPress scheduling and a fake transport. Covers own-app/admin/origin/query/body/key guards, truthful 202 responses, private status, scheduling/commit failures, replay/conflicts, callback registration and deactivation.
- Default-off hook and dispatcher regression checks verify that ordinary activation does not install a sending worker or accept a send.
- `native-sms-outbox.php` and the proposed native CI workflow add **unexecuted** isolated mysqli/WordPress enqueue/claim/idempotency/blocked-network/expired-claim probes. They hard-require the test database and external-network/cron sinks. They do not certify live sending, actual cron execution, concurrency or a complete crash matrix.

All checks above preserve original backend/frontend source. There is no completed plugin release or interim ready ZIP.
