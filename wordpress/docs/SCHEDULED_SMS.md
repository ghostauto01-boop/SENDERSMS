# Scheduled single-contact SMS — experimental, default disabled

This adds to the restricted outbox, not full original scheduled-send parity. No ready plugin/release claim. `SENDER_SUITE_ENABLE_SMS_OUTBOX` must still be explicitly boolean true. No real SMS was sent during development.

## Implemented

- Existing JSON POST `/send/` accepts optional ISO `schedule_at`, explicit contact and gateway, final SMS text and Idempotency-Key. New dates must be future UTC instants no more than 365 days away. Offset dates normalize to UTC. Naive date input uses UTC, as in the selected source parser. Replays at/past the due date return the original state rather than create another job.
- Message, conversation and source-shaped scheduled row commit together. Response includes `scheduled_id` and normalized `schedule_at`. A queued response is not delivery.
- Frozen schedule participates in the request digest. Original unscheduled digests stay compatible. Worker queries skip future retry times and independently checks frozen due time; editing retry_at cannot make a job run early. Queue expiry is one hour after due time, rather than one hour after creation. Known-provider observation horizon is 24 hours after due time.
- Worker validates linked pending scheduled identity/date, fresh consent/suppression/phone/config/body integrity before committing a claim. Normal source row states reflect pending, sending, sent (provider acceptance, not carrier delivery), failed, cancelled or unknown/manual review. Callbacks also update linked history transactionally. Existing first-terminal reconciliation remains authoritative.
- GET `/send/scheduled`: source-shaped paginated/filterable/searchable history and contact names, pending ascending otherwise descending. Selected original Python AST is executed offline to compare these projections and cancellation/date behavior. Provider error text is deliberately redacted; full bodies are visible only within existing authenticated application access. Bounded text and response budgets fail closed; source SQL wildcard search behavior is retained.
- POST `/send/scheduled/{sid}/cancel`: own-app administrator only, pending only. Legacy unlinked pending schedules can be cancelled without triggering delivery. Linked native queue cancellation and scheduled state commit atomically.
- Native extension POST `/send/messages/{message_id}/cancel`: only queued, unclaimed native messages without provider identity; repeated native cancellation is idempotent. Does not enable the worker or require credentials. Claimed/acknowledged/unknown messages cannot be cancelled or resent. Existing origin/session/no-store guards apply.
- Crashed committed claims become unknown, never queued. Recovery updates linked history too. No automatic POST retry, and scheduled retry endpoints are intentionally not implemented yet.

## Evidence and remaining qualification

`scheduled-sms-test.php`: 71 assertions per runtime, 15 selected actual-source operations and six source date cases (valid-date normalization compared); synthetic SQLite/clock/unique indexes, fake gateway, commit/insert/update faults, simulated cancellation during a committed claim, callback failure projection, missed schedule expiry and interrupted acknowledgement. The route suite also checks scheduling/listing/cancel guards and projections. See fresh checks-summary.json for totals.

These tests do **not** prove MySQL lock ordering/isolation, real WordPress cron, real HTTP routes, provider behavior, native database migration, browser UI or source bulk/template/email/Ads/sequence scheduling parity. MySQL FK coordination and destructive-contact-operation races remain unresolved. A five-minute WP cron hook is traffic-dependent and processes a bounded number of jobs, not an exact-time scheduler. Poll callback races may require a later poll on rollback; no request resubmission occurs. No operator unknown-outcome recovery UI exists yet. Do not enable on production based only on offline evidence.
