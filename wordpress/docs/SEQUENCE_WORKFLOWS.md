# Native sequence builder and version library

**Implemented data/validation APIs, not an executing sequence engine.** No SMS/email provider, follow-up worker, or new sending cron hook is invoked by this module.

## Routes

Under `/wp-json/sender-suite/v1/`, own-app login/origin checks and the common transaction/error/no-store wrapper apply:

- `GET sequences` and `GET sequences/`: page/per_page, name search and optional exact SMS/email filter; current-version steps sorted by step order. Source behavior for an unrecognized list channel is preserved: it does not filter, unlike the create/update channel enum.
- `POST sequences/`: validated creation, returns 201 with success/id/trimmed name.
- `GET sequences/{sequence_id}`: current model and step data; missing IDs return 404.
- `PUT sequences/{sequence_id}`: metadata changes or immutable version creation. Explicit null description clears it; omitted/null steps do not replace it. Unknown model properties are ignored as in the original Pydantic schemas.
- `DELETE sequences/{sequence_id}`: only unused sequences; returns empty 204. Any classic campaign or follow-up reference blocks deletion with 409, including completed/draft campaigns. Explicit bounded deletion cleans steps and saved snapshots inside the same transaction; native FKs still require certification.

These routes do not expose arbitrary tables or allow request body/query IDs to override path IDs.

## Step validation

Source-derived DTO constraints include 1–50 explicitly provided steps, orders/branch targets 0–49, wait durations 1–8760 hours, positive template IDs, config 10,000 characters, subject 500 and condition values 255. Omitted create steps retain the source default-factory behavior: the service rejects them with 400; explicitly empty lists fail DTO validation with 422.

The service sorts steps and requires consecutive orders starting at zero. Supported steps are send_sms, send_email, wait, condition and stop. Each sequence needs at least one send on its own channel. Send content must be a written message or valid template; source-compatible config parsing accepts JSON `message_body`, a JSON string, or a legacy plain string. Inline SMS messages are bounded at 5,000 code points; the service retains the original 100,000 email limit even though the API config DTO is narrower.

Email steps require a subject. Waits require positive durations. Conditions use the five original reply/delivery/failure/opt-out conditions, and branches must point forward to an existing later step, preventing backward loops. Referenced templates are checked for existence, activity/nonblank body, matching/legacy-any channel, and email subject when applicable. Storing an optional email sender ID does not certify that sender, credentials or transport: execution must validate actual sender availability and recipient policies before a future send.

## Snapshots, updates and safety

A step edit saves the previous step set as a source-compatible JSON snapshot, retains old versioned steps, inserts the new version and advances current_version. Metadata-only edits do not advance the version. The JSON snapshot's whitespace is not a byte-for-byte Python serialization promise; its structure/values are compared.

Running/scheduled/paused classic campaigns block channel changes. As an intentional additional safety restriction, a channel-only edit must also validate existing steps against the requested channel. The original permits an unused sequence to be flipped without replacing its SMS/email steps; this native module refuses that mismatch. A channel change with valid replacement steps remains supported when no live campaign blocks it. This is a disclosed safety difference, not a universal HTTP parity claim.

A conditional current_version update detects a losing step-edit race; all inserted snapshots/steps roll back on conflict. Faults during step insertion or deletion also roll back the whole API transaction. This is offline-tested control flow, **not native concurrency proof**. Metadata-only races, live campaign start/edit/delete interleavings, native constraints and database reconnection still require host testing. No exactly-once execution or sending claim follows from a library version check.

## Evidence

`tools/generate_sequence_fixtures.py` executes the actual selected original sequence schemas, service and API AST: 16 message-body helpers, 42 DTO adapter cases, and 40 sequential operations comparing responses and complete selected sequence/step/version persistent state. Fixtures reconstruct only the source's two sequence delete-orphan relationships, omit other foreign keys, and normalize known UTC timestamps after SQLite loses timezone metadata. For update DTO comparisons, per-step model defaults are materialized for service attribute access rather than recursively excluding unset nested fields.

Comparison and native safety/fault checks total **159 assertions per PHP runtime**, including rollback on step writes, rollback on a simulated version conflict, transactional delete failure, protected references and channel-only hardening. Dispatcher checks add auth/origin, 201/204, pagination, path-ID protection, revisions, rollback and proof that no stored messages were generated. SQL lock/version races, real WordPress requests, MySQL cascades, hosting compatibility and frontend behavior remain unverified. The native HTTP reference now includes sequence-builder smoke checks but has not run.

## Still missing from the requested full migration

Sequence execution, campaign enrollment/snapshot consumption, wait/condition evaluation, stop-on-reply/opt-out, actual sends, complete delivery reconciliation, sequence-generated follow-up scheduling (standalone preparation/control APIs now exist; see `FOLLOWUPS.md`), quiet hours, claims/leases, crash recovery and duplicate-send prevention remain outstanding. The sequence builder is not the full item 2 in the completion checklist. All other incomplete application areas and native release gates remain in `COMPLETION_CHECKLIST.md`.
