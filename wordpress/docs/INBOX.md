# Native stored inbox and controls — unreleased

The WordPress database now supplies stored conversation/message reads, labels/read state, campaign attribution/filter metadata, template previews and sequence-pause controls. This is **not** provider ingestion, a reply transport, a complete email mailbox or a completed native frontend. Original application files remain untouched; no release archive was produced.

## Implemented contracts

Under `/wp-json/sender-suite/v1/`:

- `GET inbox/conversations`: channel/status/search/campaign/replied filters and pagination.
- `GET inbox/campaign-filters`: first-touch classic/Ads campaign lead/reply/unread buckets, unattributed counts and channel totals.
- `GET inbox/conversations/{conversation_id}`: stored text messages/contact metadata and transactional unread acknowledgement.
- `GET inbox/conversations/{conversation_id}/templates/{template_id}/preview`: active-template personalization and source SMS segment estimate, without sending or marking read.
- `POST inbox/conversations/{conversation_id}/mark-interested`, `/mark-not-interested`, `/mark-close`, `/mark-opportunity`: explicit operator labels mirrored to the contact's lead status.
- `POST inbox/conversations/{conversation_id}/status`: validated label changes; moving to active preserves unread state when messages are unread. This generic label action does not mirror a lead status, matching the source.
- `POST inbox/conversations/{conversation_id}/mark-unread`: save unread label and at least one unread badge.
- `POST inbox/conversations/{conversation_id}/stop-sequence`: save the pause flag and execute the source's contact-scoped cancellation statements atomically.
- `POST inbox/conversations/{conversation_id}/resume-sequence`: clear the stored pause flag only.

These are **12 source method/path contracts through five route patterns**, all still partial until native qualification/integration. Own-app authentication, same-origin checks, no-store responses and the shared outer transaction apply. Source authenticated-user business permissions are retained; a WordPress administrator cookie is not an app-session bypass. This does not complete role/account management.

## Behaviour and important boundaries

### Lists and attribution

- Channel defaults to SMS, supports email/all and rejects invalid values. Legacy null channels are treated as SMS in queries. A real schema/data migration for historical nulls is not certified.
- Status unread includes an unread label or positive unread count; tab counts intentionally ignore other filters and apply only the chosen channel, matching the source.
- Text search includes contact names/business/phone and message preview, with Nigerian phone fragments such as `0803...` matching `+234803...`. Source SQL wildcard and Unicode-decimal fragment behaviour are retained. Email-address search is not silently added to this source SMS-inbox endpoint.
- Returned message counters/previews are stored metadata, not fabricated from an assumed successful sync. Dedicated email/mailbox routes, subject/HTML/attachments, receiving and counter reconciliation remain open.
- Listing/opening repairs missing first/last campaign attribution from the earliest/latest **50 outgoing messages**, including failed/cancelled messages, as in the source. Existing touches are not overwritten. Campaigns and Ads labels are batched; failures do not masquerade as an empty healthy inbox.
- Filters run **before** list-page attribution repair. Legacy unattributed threads may first need an unfiltered listing/open before a campaign filter finds them. Campaign-filter metadata itself does not run repair, matching the source.
- Unknown/deleted campaign labels are omitted; those stored campaign IDs do not automatically become unattributed. Source classic-campaign precedence is retained for malformed dual-attribution rows.

### Opening and acknowledgement

Opening a thread clears its unread count. Only null/empty/unread labels become `read`; deliberate interested/opportunity/closed labels survive. A conditional write compares prior timestamp, counters, latest-message time, pause state and attribution fields, and advances a monotonic timestamp when changing state. Already-read unchanged GETs do not issue heartbeat writes.

Messages are admitted **before** unread state is changed. Failure, oversized output, a detected stale acknowledgement or a late database error rolls back read-state/attribution changes rather than swallow unread messages. This is synthetic conflict/transaction evidence, not proof against real simultaneous ingestion or provider callbacks; the eventual receive/sending paths must update their parent conversation atomically.

Message output is an explicit field projection. HTML bodies, attachment payloads and raw provider responses are not exposed by this source SMS-inbox view. Nonempty raw `last_error` is replaced with a generic presence message rather than leak provider URLs/credentials. The original diagnostic text is not modified in storage. The future UI must still render message/contact text safely and separately qualify HTML/attachment handling.

### Labels and sequence controls

Explicit mark actions mirror source lead stages, including intentional operator changes to a previously protected customer/closed contact. The calendar's separate booking-stage protection is not an invisible prohibition on explicit inbox labels.

Stopping a sequence applies the actual source predicates **across the contact's channels**:

- Follow-ups in `pending` or `sending` become `cancelled`.
- Classic campaign-contact rows in `pending`, `queued` or `sent` become `replied`, with `next_action_at` cleared.

The source's `replied` value here is a terminal workflow marker, **not proof that a new incoming message arrived**. Completed/failed/delivered rows and other contacts are left alone. Both statements and the pause flag share one transaction. Failure of the second statement rolls back earlier cancellation and the flag.

Resume only clears `sequence_paused`; it does not recreate cancelled follow-ups, restore campaign-contact state or claim a worker resumed. No sending/sequence worker is activated. Stopping cannot retract provider requests already in flight or guarantee cancellation of work concurrently created outside the future shared claim protocol. Native concurrency, idempotency, deadlock/recovery and actual execution safety remain open gates.

## Resource and compatibility limits

- Source list size 1–500, with stable null-last message time/ID ordering. New ID tie-breaking is deterministic.
- Conversation detail admits at most 1,000 messages and an 8 MiB application UTF-8 JSON estimate. It fails 413 instead of silently truncating or clearing unread state. Large-thread pagination/export is still required.
- List output uses the same 8 MiB estimate; reduce page size on admission failure. These estimates are **not** native peak-memory, WordPress wire-size, SQL packet or latency benchmarks.
- Status JSON requests are capped at 4 KiB; filters are typed/bounded. Custom contact JSON must be an object; malformed/non-object legacy values become `{}`.
- Campaign-filter scans use 200-row ID cursor batches, projected incoming-message existence and a 10,000-bucket guard. Source channel-wide aggregation still requires native performance qualification.
- Native API base/HTTP transport differs from the original Axios base. The original frontend is untouched, not declared integrated or certified.

## Executed evidence

`generate_inbox_fixtures.py` extracts selected **actual original** inbox/attribution code and the two source `_stop_seq` SQL statements without importing the original app, sender class/dependencies, config/environment or providers. It uses 24 synthetic seeds, fixed UTC time, SQLite and omitted foreign keys/relationships.

- **38 source operations**, comparing responses and persistent conversation/contact/follow-up/campaign-contact state.
- **97 assertions per PHP runtime**, including source attribution/campaign/channel/search/label/read/pause behaviour, compound-write rollback, synthetic stale acknowledgement/conflicts, raw-error redaction, active-template personalization, cancellation irreversibility, unchanged-read timestamps, 50-message attribution windows and oversize-thread refusal preserving unread state.
- **614 shared dispatcher assertions per runtime**, including inbox session/origin/path controls, typed filters, persistence, private previews, contact mirroring, pause/cancellation, rollback and no message creation.

Known UTC timestamp representations normalize; monotonic timestamps differ intentionally. Source raw-error strings normalize to the explicit native redaction. Reference native HTTP probes are added for isolated CI but **not executed**. Zero provider requests and zero sent messages.

Still missing: replies/scheduling, SMS-Gate/other provider config and inbox export/polling/sync, authenticated inbound/status webhooks and reconciliation, complete native email/mailbox/call workflows, sequence execution, native inbox UI, real WordPress/MySQL/MariaDB/HTTP/browser tests, data migration and all operational/security/recovery acceptance gates. No ready-plugin claim.
