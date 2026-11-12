# Contact lifecycle, suppression and list cleanup

Unreleased implementation work. No new plugin ZIP was produced for this increment. These workflows are not native-certified and do not make the full application ready.

## Implemented own-app-authenticated routes

- `DELETE contacts/{contact_id}`: empty 204 after bounded transactional cleanup; missing contact 404.
- `POST contacts/bulk`: distinct existing IDs for status/tag/delete, or server-selected matching contacts for `scope=all` deletion. Filtering uses the existing search, lead status, tags, channel, list and email-state selector. All scope is not a fake success for unsupported operations.
- `POST contacts/clean`: SMS number hygiene; `delete_bad` explicitly opts into deletion.
- `POST contacts/quarantine/generated`: dry run by default; informational legacy tags on inferred addresses, with idempotent application. These tags do not establish deliverability or replace suppression.
- `POST contacts/{contact_id}/email-opt-out`, `email-opt-in`: email-specific flags/status and normalized-address suppression, without changing SMS consent or clearing bounce flags.
- `POST contacts/{contact_id}/sms-opt-in`: requires JSON boolean `consent_confirmed: true`; string/number truthiness is not consent. Clears matching SMS suppression and restores SMS consent only, without resetting SMS delivery-failure or email states.
- `GET contacts/{contact_id}/activity`: original page-length total semantics, pagination and code-point-safe 200-character message previews.
- `DELETE lists/{list_id}`: detach classic campaign references, remove scheduled messages and memberships, preserve contacts unless `delete_contacts=true` is explicit.
- `POST lists/{list_id}/contacts/delete`: permanently delete actual members only, even when the caller supplies unrelated IDs; supports server-side all/search scope.
- `POST lists/{list_id}/clean`: mark bad numbers and unlink by default; permanent deletion requires its explicit option.

Common application session/origin checks, path-ID parsing, error redaction, no-store headers and the outer API transaction apply. Original frontend source is not altered or represented as integrated.

## Destructive-operation safeguards and deliberate source differences

1. Source FK dependencies are handled before parent deletion: detach cross-references to messages before deleting messages; remove scheduled sends/follow-ups before contacts; clear retained mailbox/calendar conversation references before deleting conversations.
2. Remove selected attendee links, delete a primary-contact meeting only if no other attendee survives, and otherwise detach its primary identity. This deliberately fixes the original shared-meeting deletion bug. All selected links are removed before finalizing meeting deletion, including selections spanning 400-ID batches.
3. Retain invoices, calls, stored mailbox history and audit records with nullable identities detached. Retain **SMS and email suppression tombstones by address/number**, detaching deleted contact IDs. Unlike the original SMS cleanup, contact deletion does not erase opt-outs and permit a later import to bypass the global suppression list. This is not a complete privacy-erasure workflow or a guarantee that future unported sending code checks suppression.
4. Refuse deletion when an outgoing message is in `sending`, `dispatching` or `unknown` state, preserving evidence for delivery reconciliation. Refuse inconsistent legacy conversation/message ownership when deletion would cross into an unselected contact. These preflight checks are not a native lock/lease proof and cannot substitute for future worker coordination.
5. Fail and roll back if a required cleanup table or statement fails. Do not copy the original missing-table exception suppression into native destructive operations.
6. Reject unsupported bulk action/scope, blank tag names and oversized tag/status values instead of returning misleading successful no-ops or relying on server truncation.
7. Repair affected list counts after membership/identity deletion.
8. An import whose destination disappeared after queuing now fails **before importing rows**, rather than silently importing contacts without its requested list. Destination deletion during active worker execution and committed earlier batches still require native recovery/race tests.

Every mutation is called inside `DataRoutes`' transaction. Direct internal callers of `ContactActions` must also provide a transaction; the service is not an independently authenticated HTTP endpoint. No send worker or provider transport is enabled.

## Number hygiene

`NumberFilter` ports the original hard versus soft verdict separation. Hard verdicts reject structurally invalid/non-Nigerian/non-mobile or near-certain fake numbers. Soft sample/sequential/extreme-repeat patterns are only applied during explicit cleaning, not silently promoted to a live-send hard block. Real golden/repeated-digit mobile lines remain possible recipients.

Cleaning leaves email-only contacts alone, distinguishes existing quarantine from newly invalid numbers and previous failed/cancelled deliveries, and does not additionally label valid opted-out/suppressed numbers as bounced. Batched row reads and grouped atomic failure-counter increments avoid a per-contact query loop. Explicit deletion uses the shared dependency cleanup, not a bare contact delete. Actual host performance and concurrent counter/clean/send behavior remain unverified.

## Evidence and boundaries

- **269 actual-source pure number cases**, comparing hard verdict, normalization, reason/detail and independent soft flags.
- **31 actual-source lifecycle operations**, comparing response and persistent selected contact/list/tag/suppression/hygiene state after every operation. AST extraction omits original application/config imports and foreign keys and reconstructs only the source list delete-orphan relationship. Scenarios deliberately avoid adopting known destructive source bugs.
- Separate synthetic tests instantiate all 73 table definitions with source FK declarations enforced on **SQLite**, testing dependent cleanup, shared-calendar survival, nullable retained references, suppression retention, failed-write rollback, inconsistent ownership, in-flight deletion guard, missing import destinations and 405-contact cross-batch cleanup. SQLite FK checks are not MySQL/MariaDB/WordPress evidence; not all source indexes/collations/server modes are reproduced.
- 687 assertions per PHP runtime in this suite; 422 offline dispatcher assertions per runtime include session/origin protections, validation, real stored mutations and rollback. No providers were called or real messages sent.

Still outstanding: complete exports/enrichment/live email verification, all domain integrations, migration/erasure policy, worker claims/leases and concurrent deletion, actual WordPress/MySQL lifecycle and HTTP tests, frontend wiring/browser journeys and all other open release gates in `COMPLETION_CHECKLIST.md`. No ready release is implied.
