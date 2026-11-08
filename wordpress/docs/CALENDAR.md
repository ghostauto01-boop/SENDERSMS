# Native calendar persistence — unreleased

Calendar-only booking workflows now use the WordPress database directly. This is not a delivery engine or complete native frontend. Original application files are unchanged and no release ZIP was produced.

## Routes

Under `/wp-json/sender-suite/v1/`:

- `GET calendar` and `GET calendar/`: paginated meetings with range, upcoming, contact/conversation, status/type, tag and text filters.
- `POST calendar/`: create a calendar-only booking, attendees, SMS conversation association and pipeline changes.
- `GET calendar/upcoming`: next scheduled/confirmed meetings, optionally for one contact.
- `GET calendar/day`: UTC day agenda; preserves the source's inclusive range boundaries, including meetings exactly on the next midnight boundary.
- `GET calendar/tags` and `GET calendar/tags/all`: tag usage counts.
- `GET`, `PUT`, `DELETE calendar/{meeting_id}`: full record, partial edit/reschedule and transactional deletion.
- `GET calendar/{meeting_id}/preview`: personalized invite/reminder text and source-derived SMS segment estimate. Does not deliver anything.
- `POST calendar/{meeting_id}/confirm`, `/complete`, `/cancel`, `/no-show`: persisted status actions. Completion updates the primary contact's active pipeline stage; protected customer/closed/not-interested stages remain intact.

These cover **15 source method/path contracts**, through eight route patterns. Every contract remains partial until actual native qualification and integration. Own-app authentication, origin checking, no-store responses and the shared outer transaction apply. A WordPress administrator cookie does not bypass application authentication. Calendar permissions preserve the source's authenticated-user policy; the settings module's separate admin-write policy is not silently applied here.

## Creating a non-sending booking

For this development implementation, explicitly disable delivery:

```json
{
  "title": "Project review",
  "starts_at": "2026-11-09T10:00:00+01:00",
  "duration_minutes": 30,
  "contact_ids": [1],
  "tags": ["review"],
  "send_invite_sms": false,
  "send_invite_email": false,
  "send_sms_reminder": false,
  "send_email_reminder": false
}
```

Any requested invitation/reminder channel returns **503 before booking side effects**, including the source defaults that enable SMS invitations/reminders. Explicitly enabling a channel during update also returns 503. Existing stored flags remain readable metadata, not delivery guarantees. No reminder cron worker is installed and `/send-invite` and `/send-reminder` are not implemented. There is no fabricated gateway success or sent flag. A successful calendar-only create returns the source's zero-invite result because delivery was explicitly disabled.

The original calendar frontend still assumes sending defaults and the original API base. It is untouched, not declared compatible or integrated. The eventual native UI and delivery integration must address this boundary explicitly.

## Persistence semantics

- Valid attendees are deduplicated, invalid/nonexistent contact IDs are ignored as in the source, the first valid attendee becomes primary, existing attendee status/notified state is preserved, and removals are real deletions.
- Explicit conversation association contributes that conversation's contact. Without one, booking links/creates an SMS conversation for the first requested existing contact. Unlike the source's channel-agnostic lookup, it does not accidentally select an email conversation.
- Booking/rebooking moves unprotected contacts to `meeting`; protected stages are retained. The completion action graduates only the primary active-stage contact to `contacted`, matching the source implementation. Merely editing `status` in a generic PUT does not trigger that separate action side effect.
- Naive ISO dates/times are UTC; offsets normalize to UTC. A missing end is start plus duration. Moving the start without an explicit end preserves the previous duration; a duration-only edit changes the end. A moved start resets the source SMS invite/reminder history fields; it does not send or reset unrelated email history implicitly.
- Tags and reminder-minute lists retain source cleaning/deduplication behaviour for typed requests. Source message-body priority is custom body, active template, then default; previews use the existing variable alias/fallback renderer and UTC meeting placeholders.
- Meeting writes conditionally compare the prior record and advance a monotonic microsecond timestamp, including attendee-only updates. Contact pipeline writes conditionally compare the prior lead status. Related writes and deletes share one outer transaction. Failure or detected conflict rolls back the meeting, attendees, conversation and pipeline effects. These checks do not prove native concurrent isolation, deadlock recovery or exactly-once booking.

## Deliberate fixes and resource boundaries

- Contact filters use `EXISTS`, not a multiplying attendee join. One meeting is counted/returned once, even when its primary contact matches several joined attendee rows.
- Attendee replacement responses immediately reflect saved rows. The source can serialize a stale ORM collection after update; the source fixture retains that raw response and separately records a fresh source read for comparison of the corrected attendee response.
- Null required scalar fields and whitespace-only titles fail validation instead of reaching broken/null database states. Datetime input is strictly ISO with valid offsets/calendar dates, bounded to the SQL UTC year range 1000–9999; PHP relative dates and silent rollover are refused. This does not emulate all Pydantic epoch/basic-date coercions.
- Changed conversation/campaign/template references must exist. Meeting links on writes must be absolute HTTP(S), without URL credentials or control/space characters. They are never fetched. Legacy link data still needs migration review and the eventual browser UI must validate/escape all rendered links and text.
- Requests are limited to 2 MiB; text fields have source column bounds or a 100,000-character safety bound. Lists are capped at 1,000 input entries and 1,000 attendees per meeting. Oversized legacy attendee lists fail rather than silently truncating/replacing them.
- Pages are 1–500 records, with deterministic ID ties. Serialized meeting output is admitted against an 8 MiB cumulative page budget; oversized pages fail 413 and can be retried with a smaller page/range. Contacts are read in batches instead of a query per attendee. Tag scans use 50-row cursor batches and a 10,000-distinct-tag limit. These are admission guards, not native memory/latency/packet benchmarks.
- The source's SQL wildcard search/tag matching and inclusive day boundaries are retained. Native Unicode/collation equivalence is unqualified.

## Executed evidence and limits

`generate_calendar_fixtures.py` extracts selected **actual original source** functions, models, DTOs and constants without importing original application/config/provider modules. The oracle uses fixed time, isolated SQLite, omitted foreign keys and one reconstructed attendee relationship. An explicitly synthetic result-type adapter restores UTC information discarded by SQLite so source timezone-aware arithmetic can execute. That adapter is not native MySQL timezone evidence.

- **38 source operations**, response and selected persistent-state comparisons.
- **30 source DTO acceptance cases**, with explicit native null-field safety differences.
- **6 actual-source meeting-placeholder cases**, including midnight, noon and offset year rollover.
- **148 calendar assertions per PHP runtime**, including first-save and update/delete rollback, synthetic attendee-only conflicts, monotonic timestamps, protected pipeline stages, immediate attendee replacement, range/date/link validation, delivery refusal and oversized response admission.
- **539 shared dispatcher assertions per runtime**, including calendar session/origin controls, persistence, filters/day/tags/preview/status/delete, redacted failures, disabled delivery and the existing modules.

UTC timestamp representations normalize for source comparisons; `updated_at` is excluded because native monotonic timestamps intentionally differ from the fixed source clock. Fresh reference HTTP probes were added for isolated CI but were **not executed**. Zero provider messages were sent.

Still outstanding: native WordPress/MySQL/MariaDB installation, real constraints/collation/transactions/concurrency/reconnect/recovery tests, native browser calendar workflows, invitations/email/reminder scheduling and retries, delivery-time consent/suppression enforcement, timezone/DST UX, data migration and all release acceptance gates. No new sending worker or provider is enabled.
