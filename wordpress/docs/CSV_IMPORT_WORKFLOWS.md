# Native CSV imports and background jobs

## Implemented APIs

Under `/wp-json/sender-suite/v1/`:

- `POST contacts/import/preview`: own-app-authenticated multipart upload; headers, mapping, sample rows and whole-file channel counts. It does not write contacts.
- `POST contacts/import/csv`: own-app-authenticated multipart upload; synchronous contact persistence and import results.

Both use a multipart file field named `file`, enforce the 20 MiB input cap, reject non-uploaded local file paths and never move input into the WordPress media library. Common own-application authentication and origin checks run **before reading uploaded bytes**. WordPress administrator login is not a bypass.

The persistence endpoint accepts multipart form fields (not JSON/query replacements): `list_id`, `new_list_name`, `skip_duplicates`, `column_mapping`, `tags`, `auto_tag_rules`. Mapping is a JSON object; tag rules are a JSON array. Empty/ignore mappings are respected. Missing destination lists fail rather than silently dropping membership. New lists are created within the import transaction.

## Contact behavior

- Automatic standard/custom-column mapping, UTF-8/BOM handling and source-compatible CSV parsing.
- A row needs a usable phone **or** email. Invalid phone cells can fall back to email with a warning.
- Multi-email cells retain the first usable primary and preserve additional addresses in custom fields.
- Re-import matching checks phone, primary email, stored email alias, then only an unambiguous qualifying full-name/business-name cross-channel match.
- Existing nonblank values are not cleared or overwritten. Additional primary-address candidates become aliases rather than replacing the current primary.
- Repeated file identities are counted as duplicates. Company/domain similarity produces suggestions, not automatic person merges.
- Additive global/row/conditional tags, deduplicated list membership, source channel totals and source-compatible error reporting (first 50 errors in the API response).
- New addresses retain CSV provenance. Weak country inference uses strings/TLD metadata only: no DNS/provider/network lookup.
- Imported custom fields are discovered by the variable registry. Registry refresh is isolated in a savepoint so its failure cannot undo an otherwise successful import.
- Import does not grant consent, clear existing SMS/email opt-outs, start campaigns, enqueue sends or invoke enrichment/providers.

`skip_duplicates=false` preserves the original behavior: email duplicates may be inserted, but a duplicate phone can violate the unique phone constraint. It is not a promise to bypass schema uniqueness.

## Integrity boundaries

The native synchronous API wraps the whole import in one transaction. Failed contact/tag/list writes roll back the file and any destination list created with it. Alias-lookup database errors fail closed rather than falling back to a potentially incorrect name match. Secondary-address insertion and registry refresh have explicit nested-transaction boundaries for best-effort bookkeeping.

The database adapter now rejects invalid UTF-8, NUL-containing text and overlong source VARCHAR values **before SQL**, so WordPress/MySQL non-strict modes cannot silently truncate these values. Length is measured in Unicode code points. This safety check does not certify native MySQL modes/collation or exact original internal-error envelopes: these failures currently return the common 503 envelope, not a fabricated successful import.

## Verified offline, not certified on a real host

`tools/generate_import_fixtures.py` extracts the original synchronous CSV API/service, identity/alias helpers and public-domain metadata without importing application/config/environment modules. The fixture has 34 helper cases and 23 sequential source imports, comparing both responses and persistent contacts/tags/lists/memberships/aliases after every operation (80 assertions per PHP runtime).

Mock dispatch checks additionally cover authentication-before-read, same-origin checks, preview read-only behavior, real persistence via the synthetic SQLite adapter, merges, list idempotency, opt-out preservation, rollback, registry failure, size caps, source column limits and refusal to read arbitrary local files. The positive upload reader is explicitly synthetic; **actual WordPress multipart handling has not been executed**. Neither synthetic SQLite nor PHP-WASM certifies native MySQL, real uploads, large-file memory/time behavior, collation, races, or browser flows.

## Background jobs (implemented, native hosting unverified)

- `POST contacts/import/jobs` takes the same multipart fields and returns HTTP **202**, a UUID, initial whole-file row count and queued state. A successful response is a persisted job, not a fake completed import. Destination lists use the original normalized duplicate-name rejection. Malformed mapping/rule JSON, missing lists, empty headers and list-name conflicts fail before committing a job or destination list.
- `GET contacts/import/jobs/{job_id}`: own-app session required. Only the owner or an application administrator can read a job; non-owners see the same 404 as a missing ID. It returns progress/counters, timestamps, sanitized failure text, final summary and live list counts. It never returns CSV content, saved mapping/options or internal JSON columns.
- `GET contacts/import/jobs/{job_id}/errors.csv`: same owner/admin guard and no-store policy, full error report with source CSV quoting, attachment headers and `nosniff`. A narrow REST serializer emits raw CSV bytes instead of JSON wrapping; it cannot replace other route bodies.
- The `sender_suite_import_queue` cron hook is **non-sending**. A five-minute recurring event is installed idempotently and removed on deactivation without deleting application data. The next queue tick picks up durable queued records; polling does not execute imports. At most five files are attempted per tick, with a 40-second between-file budget (not a guaranteed per-file time limit).
- The worker uses a MySQL connection advisory lock to serialize its queue, plus conditional status claims. It publishes running state, then commits contacts, memberships/tags and progress together every 100 rows, with a final checkpoint. This matches the source worker's partial-commit model, not the synchronous API's whole-file atomicity.
- A later error rolls back only the uncommitted batch. Earlier batches/counters remain durable and the job becomes failed. Raw CSV is cleared on terminal success/failure, and the final summary intentionally excludes imported IDs and limits inline errors to 50. Full errors remain in the private CSV report.
- Registry refresh is savepoint-isolated after the final contact checkpoint. Its failure still permits completion with zero newly discovered variables.
- If a previous worker was interrupted, the next lock-owning queue tick marks its running record failed and clears content. It **does not replay partially committed files**. Externally marked queued/retrying rows with nonzero processed counts are likewise refused. There is no new public retry/cancel endpoint; neither existed in this source route inventory. Re-upload with duplicate checking enabled starts a new job.

### Privacy and hosting requirements

As in the original source, queued/running CSV content is temporarily **plaintext in the application database**, not a WordPress media file. Terminal jobs clear this content, but retain progress/results/error reports (which can include CSV-cell warnings). A queued job can retain content indefinitely if cron never runs or the plugin remains inactive. Use appropriate database/backups access controls and retention procedures; do not mistake the authenticated DTO for at-rest CSV encryption.

WordPress pseudo-cron needs traffic or a correctly configured host cron runner. `DISABLE_WP_CRON` without an external runner will leave jobs queued. Native MySQL/MariaDB must support connection advisory locks and transactions. Unsupported lock/database operations leave work queued rather than silently running an unlocked worker. Sufficient PHP execution/memory and MySQL packet limits are required; the 40-second between-file budget cannot prevent a host killing a large single import. Hosts using database routers/proxies, connection reconnection, persistent connections or multiple database servers need special lock/transaction validation. Lock ownership, lost connections, concurrent requests and native cron are **not certified** by SQLite mocks.

The cached stored list count can lag after a partial failure, as in the source worker; job polling uses actual membership counts. Failure messages deliberately omit SQL/driver/CSV details instead of exposing original Python exception strings. Source response timestamp comparisons restore known fixture UTC metadata lost by SQLite refresh; that normalization is not native timezone proof.

### Additional offline evidence

`tools/generate_import_job_fixtures.py` executes 15 selected actual source create/read/error-report/worker operations, tracking four real SQLite source-worker commits, list/contact/tag/alias snapshots, privacy guards, list conflicts and full CSV quoting. Native comparison/safety checks total 57 assertions per PHP runtime. Dispatcher checks additionally exercise 202 vs synchronous behavior, scheduling failure, auth-before-read/schedule, owner/admin permissions, raw report output, reserved output boundaries and validation. Cron/advisory-lock implementations in these tests are explicitly simulated, not a WordPress server or MySQL lock service.

The isolated native reference harness now contains real multipart and raw-report HTTP checks plus an explicit native CLI worker; it has not run. It does not yet test actual WordPress cron dispatch or concurrent lock contenders.

## Public URL imports (implemented, real transport unverified)

`POST contacts/import/url` accepts the original strict JSON request: required `url` (8–2048 characters), optional `file_name`, `list_id`, `new_list_name`, `skip_duplicates`, `column_mapping`, `tags` (array or comma-separated string), and `auto_tag_rules`. Unknown properties, malformed field types, overlong tags and non-object rules fail validation. It fetches the CSV during the authenticated request, **outside the job SQL transaction**, then creates the same durable job and returns HTTP 202. The existing worker/polling/private-error-report workflow handles it thereafter. Download/validation/database failures do not leave new destination lists or pretend jobs behind.

### Transport and safety boundaries

- Only HTTP/HTTPS, ASCII public hostnames (including already-punycoded IDNs), or canonical public IPv4/IPv6 addresses. Credentials, fragments, raw whitespace/control characters, backslashes, ambiguous numeric IP spellings and local/reserved hostnames are rejected.
- All returned DNS addresses are checked. Private, loopback, link-local, shared, documentation, multicast, reserved/special/transition ranges and known metadata endpoints are denied. IPv6 acceptance is conservatively limited to ordinary global unicast. Empty/unusable DNS answers fail closed.
- The chosen validated address is pinned with PHP cURL `CURLOPT_RESOLVE`; literal addresses are canonicalized and need no DNS lookup. A fresh cURL handle keeps the original hostname for TLS hostname verification while preventing a second unpinned hostname resolution. Redirects are disabled, no cookies/credentials are supplied, netrc is ignored, and ambient proxies are explicitly disabled. TLS peer and hostname verification remain enabled. There is **no unpinned streams/HTTP fallback**.
- A 10-second connect and 20-second total cURL deadline, 64 KiB cumulative response-header cap and streaming 20 MiB body cap apply. Declared oversize bodies are rejected before reading the body; chunked/lying/missing-length bodies are independently bounded. DNS resolution is host-managed and not covered by the cURL transfer deadline.
- Host PHP **cURL support is required for URL downloads**. Missing support returns 503 rather than degrading address pinning. System DNS/CA configuration, IPv6 cURL support, hosting egress policy, memory and execution limits need native-host review.
- The WordPress `WP_HTTP_BLOCK_EXTERNAL` switch denies downloads before DNS, conservatively even if an accessible-host exception exists. Explicit `pre_http_request` short-circuit policy results are likewise treated as a denial, not trusted cached response bytes. This is a dedicated pinned PHP transport, **not the generic WordPress HTTP transport**: WordPress proxy settings, HTTP cache responses and arbitrary HTTP-argument rewrites are not used. Host/plugin network-policy compatibility must be reviewed before production use.
- For additional safety this implementation only permits ports 80/443 and rejects compressed responses instead of inflating them. These conservative restrictions (plus special-IP/ASCII-host/fragment restrictions and tighter transfer limits) are deliberate differences from the original httpx implementation. It is not universal URL-behavior parity.
- Fetched content is not executed or saved to media; queued content and terminal error/report privacy follow the same database boundaries as uploaded jobs.

### Evidence

`tools/generate_url_import_fixtures.py` executes 24 original Pydantic request cases and 14 actual original URL-handler operations with forcibly fake DNS/httpx and isolated SQLite. Additional native policy/streaming tests cover IP range boundaries, mixed private/public answers, metadata targets, URL ambiguities, redirect refusal, declared and streamed overflow, exact-size success, header overflow, encodings and WordPress network blocks (187 assertions per PHP runtime). Dispatcher checks prove auth/origin/DTO checks before DNS/HTTP, network fetch outside the SQL transaction, queue/worker integration, and SQL rollback. These are explicitly **not real DNS, TLS, cURL, WordPress, MySQL or SSRF-bypass certification**.

The isolated native HTTP reference now includes a negative URL-import check under the WordPress network kill switch. It has not run, and it does not establish positive native URL downloads.

## Still missing

Complete frontend import wizard integration remains missing. These APIs now cover the upload-job polling/report workflow, but the unchanged frontend is not yet a fully integrated WordPress application. Native cron, real uploads, MySQL locks/transactions, browser behavior and production-host resource limits still need actual execution and review.

PHP/web-server `upload_max_filesize`, `post_max_size`, memory and execution limits can be lower than the plugin's 20 MiB cap. They need isolated host testing/configuration; this plugin cannot override pre-request SAPI upload limits. Do not treat this development build as production ready or as the completed migration.
