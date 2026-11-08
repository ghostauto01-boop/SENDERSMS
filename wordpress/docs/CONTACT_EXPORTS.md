# Native contact exports — unreleased

Implemented PHP-native read workflows, not a ready application or release. No new ZIP accompanies this work.

## APIs

Own-application login and the common origin/transaction/error/no-store boundary apply under `/wp-json/sender-suite/v1/`:

- `GET contacts/export`: CSV-in-JSON or raw-value JSON rows, `limit=1..500` (default 200), `max_bytes=1000..200000` (default 20000), `format=csv|json`, and optional cursor. Shared search, lead_status, tag, email_state, channel and list_id filters match the original export selection.
- `GET contacts/export/csv`: an actual CSV attachment (`contacts.csv`), not a JSON-encoded marker or filesystem path. The explicit successful GET route is the only route served by its raw-response serializer. Error responses and unrelated methods/routes cannot trigger it. Content type, attachment disposition, no-store and nosniff headers are set.

Paged exports retain source `format`, `columns`, `returned`, `total`, `truncated`, `next_cursor` and `csv`/`rows` fields. First-page CSV contains the header; continuation pages do not. Keyset IDs avoid offset-based skipped/repeated rows when contacts are removed between requests. New contacts may appear later; this is not a cross-request frozen database snapshot. Custom columns remain those discovered on the first page. Filter fingerprints and ordinary opaque cursor serialization are source-compatible; a cursor cannot silently change its contact filters.

CSV downloads use newest-first order with an explicit ID tie breaker. Both paths include tags and discovered custom fields. Reserved standard/tag columns are not duplicated by colliding custom keys. JSON preserves raw custom scalar/array/object types. Legacy invalid/non-object custom JSON is treated as an empty custom dictionary, as in the source. Valid data that is too deep/large is explicitly rejected rather than silently truncated.

## Deliberate safety differences

- CSV string cells and column labels beginning with formula operators (including leading whitespace), or leading tabs/newlines/carriage returns, receive an apostrophe prefix. Quoting alone does not stop spreadsheet formula execution. Phone strings starting `+234` are protected too. Actual numeric values remain numeric; CSV escaping still preserves commas, quotes and multiline cells.
- Spreadsheet-safe CSV is intentionally not a byte-exact/lossless raw-value interchange for affected strings. Use JSON export to preserve those values exactly. No claim is made that every spreadsheet/importer interprets escaping identically.
- `max_bytes` counts UTF-8 output bytes rather than the original Python code-point count. One row may exceed the soft CSV page budget, as in the source; hard limits still apply. JSON rows include actual serialized object/key size in the hard response budget rather than just value-array size.
- Cursor shape/size/type, duplicate/reserved custom keys, positive/nonnegative integer representation and custom-column resources are checked. Malformed/tampered cursors return 422 instead of becoming arbitrary unbounded structures. Cursors are not authorization tokens or signed snapshot handles: the caller must authenticate on every request.
- Hard output budget: 20 MiB. Projected source-field bytes are checked with MySQL `OCTET_LENGTH` before large payload hydration; a single projected row above 8 MiB is refused. Read groups target 4 MiB (one larger permitted row may exceed that group target). Custom JSON parsing is bounded at 1 MiB per contact; at most 512 custom columns with keys no longer than 255 code points; cursor maximum 32 KiB. More than 20,000 tag links in a hydration batch is refused. Resource-limit failures return 413, not partial successful downloads. Narrow filters/page sizes or reduce oversized data to proceed.

These admission limits reduce obvious allocation hazards but are **not native memory, database-packet, latency, concurrency or large-data performance certification**. Whole-download/custom-column discovery still scans matching metadata; host query/memory/time limits, isolation levels, large custom structures and long-running reads need native testing. There is no asynchronous export worker or downloadable persisted export job in this increment.

## Evidence

`generate_export_fixtures.py` executes the selected original export functions with fixed-clock synthetic SQLite. It records **34 source export operations**, raw source outputs and a separately labeled spreadsheet-safety adjustment, covering JSON/CSV, custom values and column unions, tags, list/channel/search filters, first/continuation pages, empty selections, byte-limited continuation and filter-bound cursor errors.

The export suite has **68 assertions per PHP runtime**, additionally checking strict cursor/resource guards, formula/control-character handling, numeric/quote/newline formatting, removed/new contacts between requests, frozen columns, real UTF-8 byte budgets and projected-payload admission spies. SQLite's `OCTET_LENGTH` is a test-adapter function implemented with byte length, not native MySQL evidence.

The shared dispatcher suite has **455 assertions per runtime**, including export login checks, validation, pagination, private headers, raw CSV attachment output, trailing-slash handling and serializer refusal of unrelated/error responses. Neither suite is actual WordPress HTTP, MySQL or browser coverage. Native HTTP smoke references include exports but remain unexecuted. No provider calls or real messages were sent.

The rest of the requested migration and release qualification remain incomplete; see `COMPLETION_CHECKLIST.md`.
