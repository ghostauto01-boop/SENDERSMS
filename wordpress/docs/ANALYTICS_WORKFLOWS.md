# Native analytics (read-only, incomplete migration)

Own-app-authenticated, no-store GET routes under `/wp-json/sender-suite/v1/analytics/`:

- `overview`: message/channel/window counts and rates, opt-outs, follow-up creation and interested contacts.
- `timeseries`: per-day sent/delivered/failed/reply counts and rates from a single grouped query.
- `funnel`: sent/delivered/replied/interested stages, failed/cancelled totals and up to eight failure/quarantine reasons (80 Unicode code points per reason).
- `by-campaign`: classic campaign comparison with name lookup and missing-campaign fallback. This is not Ads Manager campaign reporting.
- `calls`: persisted daily call outcomes, totals, connected count and rate. It does not place calls or ingest provider events.

`days` defaults to 30 and is bounded to 1–365. Message analytics accept normalized `channel=sms|email|all` (default SMS), with optional `campaign_id` on overview/timeseries/funnel. Campaign ID zero retains the original unfiltered truthiness behavior. By-campaign accepts `limit` 1–50 (default 10). Call analytics ignore irrelevant channel/campaign arguments as the source does.

## Source semantics preserved

- Shared `Analytics::counts()` counts actual stored messages. All outgoing records count as sent—including queued/failed ones—because that is the original metric definition, not proof of a successful provider delivery.
- Delivered means outgoing status delivered, a delivery timestamp, or an opened timestamp. Failure means outgoing status failed, not cancelled. An outgoing row can satisfy delivery evidence and failure simultaneously. Queued includes queued/sending/retrying. Replies are incoming rows. Open/click counters and distinct attempted contacts are available to future native reporting consumers.
- Message channel NULL is treated as SMS by SQL coalescing. Legacy-NULL schema/migration behavior is not certified by the current fixtures.
- Message windows start at midnight UTC `days - 1` days back, including today. Calls use the exact rolling instant `now - days`, matching the distinct source call-report behavior. Neither imposes an additional future-date upper bound that was absent in the source.
- Contact opt-outs, interested leads, follow-up creation and quarantine reasons intentionally remain global where the source leaves them global; they are not silently restricted by the message campaign/channel filter.
- Days without records are not synthesized into zero chart points. Ratios use the original max(sent, 1) denominator and one-decimal rounding. SQL tie ordering for equally frequent campaign/reason groups is not made into a new stable-order contract.
- The original `total_sms` overview field is retained for email/all scopes too; its legacy name does not change what the selected channel contains.

## Evidence and limits

`tools/generate_analytics_fixtures.py` executes actual selected original metrics and analytics AST on isolated fixed-clock SQLite. It generates 57 endpoint operations and 45 metric scopes, including both directions/channels, all common statuses, delivery/open/click evidence, exact day boundary, absent campaign names, long Unicode reasons, campaign zero and no-match filters. Native PHP comparisons plus empty/validation cases total 111 assertions per runtime.

Dispatcher tests additionally verify own-app authentication, real persisted aggregates, validation/query binding, channel/campaign isolation, chart/headline agreement and absence of mutation. Fixtures omit foreign keys/relationships and do not prove native MySQL collation, timezone storage, SQL date grouping, performance, real WordPress HTTP or browser integration. The native HTTP reference includes analytics smoke checks but has not executed.

This module does not restore the full dashboard/overview, provider sends, callback ingestion, campaign engines, call integration or all email/Ads reporting. Zero counts mean no matching stored rows, not an assertion that missing sending/provider workflows are operational. See `COMPLETION_CHECKLIST.md` for every remaining contract and release gate.
