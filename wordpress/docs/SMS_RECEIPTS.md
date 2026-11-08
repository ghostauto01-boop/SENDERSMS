# SMS-Gate delivery receipts — implemented subset, NOT a sending release

This module authenticates and reconciles **stored outgoing SMS-Gate SMS delivery statuses only**. It does not connect to a device/cloud API, send SMS, register a subscription, receive SMS/MMS, poll a gateway, execute campaigns or implement the native frontend. No provider credentials or live provider requests were used in its tests. Native WordPress/MySQL/HTTP/concurrency qualification remains outstanding.

## Routes and configuration

Under `/wp-json/sender-suite/v1` (or WordPress's equivalent `rest_route` transport):

| Method | Path | Access and behavior |
|---|---|---|
| GET | `/settings/gateway/webhook-signing` | Own-application session required. Returns `configured` and `status_events_only`, never a secret/envelope. `configured` means a nonempty value is stored, not that decryption/connectivity is healthy. |
| PUT | `/settings/gateway/webhook-signing` | Own-application **admin** session and same-origin checks. JSON only: `{"secret":"..."}`. Exactly one field, body at most 2,048 bytes. The WordPress `rest_route` transport parameter is ignored; other query parameters are rejected. |
| POST | `/webhooks/smsgateway` | Machine callback, HMAC authenticated, not session/WordPress-cookie authenticated. Public registration is not permission to submit unsigned data. Responses use the shared no-store/request-ID envelope. |

A nonempty signing key must be valid UTF-8, 32–512 bytes, without Unicode whitespace, separator or control characters. An explicit empty string disables the callback. It is encrypted through Config/Vault using a purpose bound to the table prefix, setting row ID and key. The callback never treats a legacy plaintext setting, session secret, notification credential or environment variable as a fallback key. Unconfigured/unreadable signing storage or unconfigured application credentials fail closed with 503. Replacing an unreadable setting is an explicit administrator operation, not silent migration.

Only one application-wide key is currently supported. Phone-specific callbacks, overlapping rotation keys, gateway credential/device setup, connection testing, subscription registration and key-management UI are **not implemented**. Rotation changes the accepted key immediately. Coordinate both ends and retry handling; old-key deliveries are not accepted after rotation. Do not enable inbound subscriptions against this status-only endpoint.

## Authentication and admission

The retained source signature algorithm is HMAC-SHA256 over **the exact raw request bytes followed by stripped `X-Timestamp`**, with the UTF-8 signing key. The signature header is stripped and lowercased before constant-time comparison. JSON is not reserialized before verification. The timestamp tolerance is an absolute 300 seconds, including future clock skew.

Native headers have bounded lengths. Timestamp syntax is deliberately restricted to 1–12 ASCII digits after stripping; the source Python integer parser also permits some additional forms, which are not accepted here. Missing/invalid/stale signatures return 401. Invalid authentication produces no receipt/business-row writes. This is a bounded timestamp window plus event idempotency, not a claim that timestamp checks alone prevent replays.

The application admission limit is 65,536 raw-body bytes and JSON nesting depth 32. Oversize bodies return 413. This does **not** prove a web server/WordPress SAPI memory or wire-size limit: the host must bound incoming request bodies before application loading. Signed malformed objects, identifiers and invalid supplied timestamps return 422. The source silently falls back to receipt time for malformed dates; native rejects rather than recording a fabricated date. Valid receipt times normalize to UTC; missing dates use receipt-processing time. Date selection retains source order: `deliveredAt`, `sentAt`, `failedAt`, `cancelledAt`.

Supported events: `sms:sent`, `sms:delivered`, `sms:failed`, `sms:cancelled`. The payload must be an object, nested under `payload` or supplied flat, with a nonempty string `messageId` (at most 255 bytes). Optional top-level `id` must also be a bounded nonempty string. ASCII controls/invalid UTF-8 are rejected. Numeric identifiers are not guessed/coerced. All other events, including inbound SMS/MMS and heartbeat/start events, return **503**, without persistence/ingestion or fake success.

## Idempotency, ordering and atomic reconciliation

The outer database transaction owns the entire operation: authentication/config read, event lookup, message transition, contact hygiene, campaign counters/membership, and processed event insert. Services invoked directly must be wrapped in `Database::transaction`, just like the public dispatcher. Unique `webhook_events.idempotency_key` is required; its native installation and concurrent behavior remain to be qualified.

- Native event keys are namespaced SHA256 hashes of the explicit delivery ID, or of the exact raw body when no ID is supplied. They are not the source's truncated `wh-{id or messageId}` key, which can collide across statuses.
- A previously processed ID and identical raw-body digest returns `{ok:true,duplicate:true}` without business writes. Reusing the same explicit ID with different bytes returns 409, including differently formatted JSON. Provider event-identity/retry behavior must be verified before enabling this on a live integration.
- A missing committed message returns 503 and leaves no receipt row, allowing a send/commit race to be retried. This intentionally differs from the source's successful `matched:false` acknowledgement. Retries must carry a currently valid timestamp/signature; gateway retry/resigning behavior is not verified.
- Exactly one stored outgoing message with provider `smsgate`, channel `sms` (legacy null is treated as SMS) and exact provider message ID is required. Ambiguous IDs return 409. An additional decoded-byte comparison prevents SQL collation/PAD SPACE matching from broadening message identity. Other providers, incoming messages and email cannot be reconciled accidentally.
- Nonterminal state rank follows the source. A late `sent` cannot lower a terminal status. Missing first sent/delivered/failed timestamps may be filled for a repeated same status; existing first timestamps never move.
- **Intentional safety divergence: first terminal status wins.** Once delivered/failed/cancelled, a different terminal receipt does not replace the outcome, recount aggregates or quarantine a previously delivered contact. The source permits equal-rank terminal replacement and can count conflicting outcomes. Native conflicting receipts are acknowledged and recorded as processed without changing that outcome; they are not automatically resolved. Manual correction/recovery tooling is still missing. This policy must be reviewed against real gateway semantics before release.
- Each new failed/cancelled message outcome quarantines its contact and increments `delivery_fail_count` once, using a generic status reason. Source repeated failure receipts increment contact failures again, even when campaign counters do not move; native deliberately does not. Raw provider reasons and previous provider error strings are not propagated. Delivered never silently clears a contact's existing quarantine.
- A new terminal outcome increments campaign delivered or failed count once (cancelled counts as failed). Existing queued/sent campaign membership settles delivered/failed; pending/replied/other states are preserved. Repeated same terminal status may settle an existing queued/sent membership, without inferring missing aggregate counts. Missing campaign/counter exhaustion fails rather than committing a partial update. Counters are guarded at signed 32-bit maximum.
- Message state/identity/timestamps, contact failure count and membership status are conditionally checked. Campaign increments use a bounded atomic increment. Detected races return 409; database/ledger/counter failures return 503. The whole transaction rolls back, permitting the original receipt to retry. Source processing-error success acknowledgements are deliberately not reproduced.

These guards are application semantics plus synthetic fault evidence, **not real concurrent InnoDB/deadlock/isolation certification**. They do not guarantee in-flight provider cancellation, repair imported inconsistent terminal/counter history, resolve terminal contradictions, or reconcile Ads Manager counters/workers.

## Privacy, operations and outstanding release work

Processed receipt storage contains provider/event metadata, the internal matched message ID and SHA256 of raw bytes, not raw JSON, headers, signature, key, incoming text or raw provider reasons. The digest is not anonymization; receipt metadata/IDs/digests still require appropriate DB/backups permissions and retention. Rejected/unmatched/failed callbacks are not falsely persisted as processed. Operational diagnostics, bounded retention, key rotation overlap, endpoint rate limits, dead-letter/replay/operator recovery tools, device-specific scope, gateway retries and real gateway error/clock behavior remain unfinished. Apply host-level request/rate controls before deployment; no production readiness is claimed.

## Evidence and limitations

`generate_sms_receipts_fixtures.py` executes selected **actual original** signature, delivery processor and hygiene AST against fixed-clock synthetic SQLAlchemy/SQLite. It never imports the full sender/provider application or invokes transport methods. The committed fixture contains 60 source HMAC vectors and 19 source-compatible reconciliation operations; terminal/failure hardening is tested separately and is not misrepresented as source equivalence.

`sms-receipts-test.php` runs on PHP-WASM 7.4/8.3 with synthetic SQLite: source-state/HMAC comparisons; multipart/replay/late status handling; unsupported/invalid/unmatched admission; provider/channel scoping; encrypted purpose-bound signing storage; configuration disable/fault behavior; and injected failures across every compound write followed by retry. Dispatcher tests additionally cover application-admin/session/origin boundaries, WordPress query transport, unsigned refusal, sessionless signed status delivery and unsupported inbound 503. Hook tests verify registration without adding unsupported routes. `run_checks.py` requires structured success, not WASM exit status alone.

`native-http.py` has additional private signing setup, unsigned, unsupported-inbound and unmatched-retry smoke probes. **They have not been executed here.** No real gateway/SMS, native PHP/MySQL/WordPress installation, live HTTP callback, real concurrency/crash recovery, frontend or browser certification is implied. The full application is still incomplete; no new plugin ZIP is provided.
