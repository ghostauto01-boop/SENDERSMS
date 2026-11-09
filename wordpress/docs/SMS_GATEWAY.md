# Native SMS-Gate configuration and transport — partial sending foundation

## Implemented

The application-session-authenticated `/wp-json/sender-suite/v1/settings/gateway/phones` APIs now list/create cloud phone configurations; `/{phone_id}` updates/deletes; `/{phone_id}/test` performs a **non-sending** connection probe. Only application administrators may mutate or probe. Viewer roles may read redacted metadata. WordPress administrator cookies do not replace application authentication. Credential inputs require bounded JSON; URL parameters other than WordPress's `rest_route` are rejected even on reads. Requests over 8 KiB are rejected.

Passwords and optional stored webhook secrets use the existing site/table/row/field-bound AES-GCM vault. They are never returned as ciphertext or plaintext. Fixed masks reveal no password suffix. Secret text is bounded to 128 Unicode characters and 256 UTF-8 bytes so envelopes fit the source columns. Username, device, phone, notes, SIM, priority and daily-limit values are bounded. `sim_slot` is accepted as a creation alias. SIM is strictly 1 or 2. Only cloud mode is supported by this transport; local/private network gateways are not enabled implicitly.

Mutations claim a common transactional configuration fence before changing defaults or rows. Conditional row versions and strictly increasing per-row timestamps protect edits/probes. Default changes, row creation, encryption and response generation belong to the dispatcher's transaction. Failures, including a final commit failure, roll back settings. Missing-fence insert races may surface 503 rather than 409; no native concurrency qualification is claimed. The first-party response projection does not load raw provider error text. Corrupt or source-Fernet credentials require explicit replacement—there is no plaintext fallback.

`SmsGate` is an actual PHP cURL transport implementation with:

- Source-shaped POST `/messages` payloads: text, one E.164 recipient, SIM, optional device and TTL 3600.
- Public HTTPS/443 only, DNS/public-address admission and pinned connection addresses using the existing conservative URL resolver. No redirects, ambient proxy or netrc; verified TLS and fixed allowed operations.
- WordPress external-network kill-switch and HTTP-policy veto checks. A policy short-circuit is treated as a block, not an unpinned cached provider response.
- Bounded connection/total time, headers and response bytes; no raw provider responses/errors in return values or logs.
- Explicit `accepted`, `rejected` and `unknown` outcomes. A successful HTTP status without a usable provider ID is **unknown**. Redirects, 5xx, truncated/malformed responses and timeouts never invent a provider ID. POSTs are never automatically retried.
- GET `/messages/{encoded_id}` polling with an exact returned-ID check and an explicit state allowlist. `Undelivered` is not classified as delivered by substring matching.
- GET `/devices` probes that require a successful JSON device-list shape. They establish API reachability/authentication only, not handset connectivity, recipient deliverability or verified carrier delivery.

## Intentional source differences

The original provider could generate a random local ID after an apparently successful response with no provider ID, accept redirects as success, and classify any status containing `deliver` as delivered. The native transport does none of those things. A connection test in the original provider POSTed an actual diagnostic SMS with validation disabled; the native test never sends any SMS, and rejects `test_recipient`/`test_message` input.

The original deployment's protected Render environment sender is not fabricated or imported from environment files. `render_sender_active` is false. Only native database phones are listed. Routing metadata identifies the intended default-phone selection; no routing engine or automatic SIM failover is implemented, and `sim_failover` is false. Stored daily limits and priorities are configuration only until the outbox/worker enforces them. Phone-specific stored webhook secrets are not yet connected to the separately implemented global status-receipt endpoint; webhook registration and phone-specific callbacks remain missing.

## Not an operational send workflow yet

**No public send/enqueue path or SMS delivery worker is enabled by these changes.** No provider was contacted and no SMS was sent during development. The transport alone must not be represented as a finished SMS application. Before exposing it to users, finish and qualify:

1. Atomic enqueue, durable sender/recipient/message identity and idempotency replay.
2. Consent, opt-out, suppression, number validity and limits at enqueue and immediately before delivery.
3. Durable claims/leases, bounded retries only where safe, and an explicit unknown-acknowledgement recovery policy. Do not resend a potentially accepted POST after a crash or failed DB commit.
4. Post-commit external effects, status callback/poll reconciliation and exactly-once counter effects.
5. Sender routing, cancellation, scheduled/bulk sends, campaign/sequence integration and native concurrency/recovery tests.

The existing original send endpoint remains unimplemented; there is no fake successful send response.

## Fresh evidence

- `generate_sms_gate_fixtures.py`: actual selected original provider AST with fake httpx, synthetic credentials and fixed UUID; ten send cases and eight poll states, plus the original diagnostic request shape. No original application/config module imports.
- `sms-gateway-test.php`: 147 assertions per runtime, including source request payloads, ambiguous responses, no invented IDs/retries, status identity, private/mixed DNS blocking, input limits, credential encryption/binding, default changes, CAS/commit-failure rollback, corrupted-key repair and non-sending probes. All network transport is fake.
- Dispatcher tests now total 774 assertions per runtime, including own-session/admin/origin/URL/body guards, redaction, encrypted replacement, commit failure, safe probes and delete.
- Proposed isolated native HTTP probes now cover credential storage/replacement/deletion and the network sink. They have **not executed** and are not evidence.

These tests do not establish native cURL/DNS/TLS, real gateway behavior, WordPress HTTP, MySQL/MariaDB concurrency, outbox recovery, handset status or browser certification. The complete migration and release remain incomplete.
