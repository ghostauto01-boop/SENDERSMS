# Stored classic campaign reporting — partial workflow

Own-application-authenticated native PHP handlers now exist for:

- `GET /wp-json/sender-suite/v1/campaigns` and `/campaigns/`
- `GET /wp-json/sender-suite/v1/campaigns/{campaign_id}`
- `GET /wp-json/sender-suite/v1/campaigns/{campaign_id}/analytics`
- `GET /wp-json/sender-suite/v1/campaigns/{campaign_id}/conversations`

These are **stored-data reads**, not a working campaign engine. They never launch, schedule, pause, resume, stop, delete, reconcile, enqueue, send or repair attribution. Campaign creation/editing, validation, state transitions, execution workers, performance reporting, Ads Manager workflows and the campaign frontend remain incomplete.

## Behavior

Listings default to SMS, support all/email channels, status/search filters, total counts, 1–100 rows per page, a bounded page number and stable updated-at/ID order. SQL is prepared, sorting is not caller-controlled, and search retains the source's `%`/`_` wildcard behavior. Native channel validation rejects typos with 422 instead of silently returning an empty list as the source does. All reads use the application's session; a WordPress administrator cookie is insufficient. Viewer roles can read reporting. Responses have no-store headers.

A source-AST-derived field allowlist reproduces `CampaignOut`, including kind `campaign`, sender options, scheduling, stored counters and UTC timestamps. Attachment output is metadata only; base64 payloads are not returned or fetched. Null legacy fields receive source DTO defaults. Structured sender options are depth-checked before decoding; corrupt/deep stored JSON fails with a repair error rather than an unchecked response. Private implementation columns are never exposed by generic table serialization.

Analytics reports the source service's **stored campaign counters**, delivery/reply rates and contact-status grouping. This is deliberately different from message-table recomputation; it does not certify that counters match provider records. Empty status maps stay JSON objects. Null stored counters fail with a repair error rather than inventing financial/operational history.

Conversation drill-down scopes both first-touch and last-touch campaign attribution to the campaign's channel. Optional `replied_only` filters threads with incoming messages. Sparse/missing contacts are tolerated; names prefer person, then business, phone, email, Unknown. It returns unread/message counts, preview, last-message timestamp and only the most recent inbound reply, including stored AI sentiment/intent. It does **not** mark threads read or opportunistically heal attribution. A bounded page subquery and latest-inbound correlated lookup avoid loading the source's entire inbound history. Timestamp ties use IDs deterministically. Conversation pages allow 1–200 rows.

## Safety bounds and differences

- Campaign definitions/attachments: selected raw page data is limited to 12 MiB **before projection**; encoded output is limited to 8 MiB. Oversized definitions fail with 413; callers can reduce page size. Single oversized rows require operator repair.
- Structured sender fields: JSON depth 32; malformed/deep values return 503 repair errors.
- Contact-status group count: at most 1,000, otherwise 413.
- Conversation preview/latest-reply bytes: 4 MiB before projection; encoded output 8 MiB. Old replies not selected as latest are not loaded.
- Page number: at most 1,000,000. Text query fields are bounded and arrays are rejected.
- Invalid channels are rejected, and stable ID tie-breaks are intentional additions to source behavior.

These are resource admission limits, **not** native SQL execution-plan/load/concurrency proof. Native MySQL/MariaDB index/query behavior, WordPress HTTP integration and complete browser workflows still require testing.

## Evidence

`generate_campaign_report_fixtures.py` loads selected actual source campaign read handlers, the source DTO and actual `get_campaign_stats` method. It uses fixed-clock SQLite with omitted ORM relationships/foreign keys, not original configuration/environment imports. Its 35 operations cover list filters/search/pagination, single reads, stored analytics, first/last attribution, cross-channel separation, reply-only filters, missing records/contacts and conversation pagination.

`campaign-reports-test.php` passes 116 assertions per runtime, including unchanged-table snapshots, metadata/base64 checks, large-definition/latest-reply preflight limits, scalar/range/depth guards, latest-reply selection and non-healing/non-clearing behavior. Dispatcher tests additionally exercise session requirements, no-store, role visibility, query/path protection, input bounds, missing IDs and unchanged campaign state. There is no campaign send/execution evidence, native certification, or ready-plugin claim.
