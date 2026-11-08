# Sender Suite WordPress-native migration — INCOMPLETE

This is a **repository-backed WordPress-native migration** of the original application. WordPress replaces the Python backend at runtime; a separate Python service is not the proposed solution. The full SMS/email application is not finished. The repository source, not any unavailable archive or historical chat claim, is the behavior specification.

All recreated runtime/tests/tools are inside `wordpress/` on the session branch. `tools/native-wordpress-workflow.yml` is a proposed isolated native CI workflow, not an installed or executed workflow. The current GitHub App connection rejected workflow creation because it lacks `workflows` permission. Original `backend/`, `frontend/`, deployment files and root dependency manifests are unchanged. No original application/config/environment module was imported, no environment file was read, no production database/provider was contacted, and no messages were sent.

The complete current task list, module counts and every outstanding method/path are in `docs/COMPLETION_CHECKLIST.md`. This checkout includes that checklist; it is not the completed migration. No new development ZIP is being handed off.

## What has actually been recreated

- Source AST inventory:73 tables,1,120 columns,235 indexes,61 foreign keys,511 HTTP declarations/508 distinct contracts,71 settings declarations,165 unchanged frontend file hashes.
- A source-derived MySQL/MariaDB schema planner, explicit SQL alternatives, checked/prepared database adapter with nested transactions, guarded installer and data-retaining lifecycle.
- Site/field-bound AES-256-GCM encrypted-value utility with tamper checks and key serialization/debug protections. **Nine active auth/scheduling settings now persist encrypted in a custom table (including the follow-up day-view timezone)**, with a capability/nonce-protected operator setup screen. This is not a claim that all 71 source settings or provider credentials are wired.
- Pinned/scoped phone8.13.52, Locale2.7.0 and mbstring1.31.0, upstream licenses, modified-file hashes and Unicode15 lower/upper/decimal/NFKD data.
- Original-source-compatible UTF-8 replacement and CSV parsing, header mapping, phone/email classification and read-only preview service.
- Own-app-authenticated multipart CSV preview and synchronous import APIs: real contact persistence, additive phone/email/name/alias merges, custom fields/variables, global/row/conditional tags, destination lists, duplicate/company suggestions and error reporting. Imports never grant consent or clear existing opt-outs. Durable background jobs, batch progress polling and private CSV error downloads are also implemented. Public URL imports also feed the same job queue through a bounded pinned-address fetcher; full frontend/native-host integration is still missing.
- Read-only analytics overview, daily series, funnel/failure/quarantine reasons, classic-campaign comparisons and call outcome statistics. Shared stored-message metrics preserve source channel/window/status definitions. These APIs do not implement sending, provider reporting ingestion, campaign engines or the complete dashboard.
- Sequence-builder list/create/read/edit/delete with immutable step revisions/snapshots, template/channel checks, forward-only condition branches, live-campaign channel protection and referenced-sequence deletion guards. This is data/validation, not sequence execution or sending. A channel-only edit is additionally rejected if it would mismatch existing sends, a deliberate safety hardening over the source.
- Calendar-only booking CRUD, attendees, SMS conversation links, protected pipeline transitions, date/filter/tag/upcoming/day reads and personalized previews now persist natively. Sending/reminder flags must be disabled: requests for unimplemented delivery fail instead of claiming success. Immediate attendee responses and non-multiplying contact filters deliberately fix source inconsistencies; see `docs/CALENDAR.md`. Complete calendar UI/delivery/native qualification remains outstanding.
- Private draft invoice creation/read/edit/delete, line items, server totals, numbering, audit history, duplication, draft voiding, filtered listing/currency summaries and read-only existing payment metadata are implemented. Money/payment-attempt records are protected from edits/deletion. Checkout URLs are explicitly unavailable; no invoice email/Paystack/settlement calls or PDF/browser workflows are implemented. See `docs/INVOICES.md` for storage/rounding bounds, deliberate source differences and native qualification gaps.
- Status-only SMS-Gate callbacks now authenticate raw-body HMAC, deduplicate receipts and transactionally reconcile stored SMS/message/campaign/contact state, with encrypted admin-only signing-key configuration. No provider connection, sends, registration, inbound ingestion or native qualification is implied. See `docs/SMS_RECEIPTS.md`.
- Standalone follow-up preparation, local-timezone/DST day views, live overdue flags and transactional skip/delete/reschedule controls are implemented, with attempt/history/pause/consent/link guards. This does not execute follow-ups, resolve configured email senders, notify due events or queue sends. See `docs/FOLLOWUPS.md`.
- Stored inbox conversations/messages, channel/status/campaign/replied/search filters, first/last campaign attribution repair, campaign filter counts, transactional read/unread/lead labels, active-template preview and source-scoped sequence-pause/cancellation controls are implemented. These are data/control APIs, not replies, ingestion, provider sync or sequence execution; raw provider errors are redacted and large threads fail without silently clearing unread state. See `docs/INBOX.md`.
- A separately labeled WordPress-administrator-only CSV diagnostic with capability/nonce/upload/size checks. This is **not** the original own-app-authenticated import API, and performs no contact writes.
- One public REST health handler and five initial own-application auth handlers (access/admin/login/logout/me), at `/wp-json/sender-suite/v1/`. These are not yet mapped to the original `/api/v1/` URLs or certified against real WordPress HTTP. Other application APIs are deliberately not registered as fake successful placeholders.
- Own-app-authenticated contact creation/read/edit, duplicate checks, phone/email normalization, tag replacement/addition/removal, channel/status/list filtering, allowlisted sorting and stable contact pagination.
- Own-app-authenticated list creation/edit/listing, live membership counts, member browsing, deduplicated addition, server-scoped add-all, non-destructive removal and status statistics. Contact/list deletion, bounded permanent member deletion, bulk actions, SMS number hygiene, generated-address tagging and channel-specific consent/suppression operations are now implemented with transactional cleanup and shared-calendar safeguards. See `docs/CONTACT_LIFECYCLE.md` for deliberate source safety fixes and test boundaries. Authenticated cursor/JSON exports and spreadsheet-safe CSV downloads are also implemented; see `docs/CONTACT_EXPORTS.md` for source differences, byte/read/cursor limits and incomplete native qualification. Enrichment/live email verification and native lifecycle certification remain missing.
- Variable-registry creation/editing/deletion, built-in protection, discovery from custom fields, aliases, fallbacks, source/search filters and authenticated message analysis. This alone is **not** a sending workflow.
- Source-derived text rendering (double/single braces, greeting fallbacks, unknown-variable marking, cleanup and scalar formatting) and source-compatible SMS segment estimates. No HTML sanitization or carrier billing is certified.
- SMS/email template creation/editing/filtering/categories, active flags, duplicate/SMS-to-email, source metrics, structured unknown-variable errors/suggestions and query personalization preview. Attachment size/count/storage limits preserve source behavior; responses omit base64 payloads. No sending, file fetching or providers are invoked.
- Auto-reply rule and If/Then automation create/read/edit/delete/list, priority ordering, channel filtering, sender existence checks (including unified negative mailbox IDs), condition validation and non-sending rule tests. The built-in lexicon classifier is translated to PHP; it requires no AI API. Rule actions are stored, **not executed**: inbound triggers, per-contact cooldowns, destructive actions, real sends and counters are not wired.
- Workspace appearance, allowlisted compliance flags, Unicode-normalized muted senders and encrypted Pushover credentials are implemented with application-admin write protection and redacted reads. Credential writes require JSON rather than query-string secrets. This is storage/preferences, not provider connectivity or notification delivery; see `docs/RUNTIME_SETTINGS.md`.
- In-app notification centre listing, unread/latest counts, mark-read and mark-all-read, isolated to browser-provider records. Push subscriptions, VAPID setup, browser/provider delivery and notification-emitting business jobs remain missing.
- Explicit source-model defaults for these business tables; no assumption that Python application defaults become native SQL defaults.
- Argon2-only production password codec, HS256 cookie sessions, current-account activation/role checks, same-origin guards, scoped JSONP/envelope hardening and persistent login throttling. Production crypto, real cookies and concurrent native throttling remain unverified. WordPress administrator login is not an application-session bypass.
-132 freshly compiled unchanged frontend assets, with available production dependency notices retained. They are **not served at the site root** while original API URL integration and business workflows are missing.
- Reproducible generation, test and recovery-packaging tools. Caches/node_modules/environment files are excluded from recovery packaging.

## Freshly executed evidence

See `tests/checks-summary.json` and `tests/frontend-evidence.json`.

-42,122 functional assertions across PHP WASM7.4.34-dev and8.3.36, including offline account/DTO and auth-dispatch tests.
-86 first-party PHP syntax checks.
-3,075 vendor parser checks, **reported separately from functional coverage**. The PHP8-only guarded mbstring bootstrap is intentionally not parsed with PHP7.4. Parser memory is raised to512M only inside that test.
-1,547 vendor file hashes and165 unchanged frontend source hashes verified.
-Recorded unchanged frontend run (not rerun in this iteration):269 tests/43 files pass; production build passes and generates132 assets. A jsdom navigation warning is not native browser workflow proof.

Template comparisons add 40 source operations, 42 DTO cases, 6 attachment coercion cases, 6 file/message size boundaries and 136 typo-suggestion cases (288 assertions per runtime). Helpers are extracted with AST, not original application/config imports. These use a fixed clock, synthetic SQLite with monotonic committed IDs, and omitted relationships. Reply/notification comparisons add 68 source/fixture operations, 108 DTO cases, 273 classifier cases, 200 individual rule-match cases and 504 condition cases (1,289 assertions per runtime). Original DTO checks model FastAPI v2 from-attributes adaptation, not actual HTTP. Synchronous import comparisons add 34 pure helpers and 23 original API/service imports with full contacts/tags/lists/memberships/aliases state snapshots (80 assertions per runtime). Background-job comparisons add 15 original queue/create/read/error-report/worker operations, four durable commit snapshots and failure/recovery safety checks (57 assertions per runtime). SQLite discards timezone metadata on refresh, so the known UTC fixture timestamps are explicitly restored in that oracle; native timezone behavior remains unverified. URL comparisons add 24 original DTO cases and 14 actual source endpoint operations with fake DNS/httpx; conservative address, policy and streaming-boundary tests bring that suite to 187 assertions per runtime. They do not certify real DNS, TLS, cURL, SSRF resistance or host proxy behavior. Mock upload readers and advisory locks are synthetic, not actual SAPI uploads or MySQL locks. The adapter also checks UTF-8/NUL/source VARCHAR bounds before SQL to prevent silent truncation. Analytics adds 57 actual source endpoint operations and 45 source metric scopes (111 assertions per runtime), with fixed UTC clock, synthetic SQLite and omitted foreign keys/relationships. Sequence comparisons add 16 body helpers, 42 DTO-adapter cases and 40 actual source operations with persistent version/step state snapshots (159 assertions per runtime including failure/safety checks). Fixtures reconstruct only the two source delete-orphan relationships, omit other FKs and normalize known UTC timestamps after SQLite refresh. Native version races and cascade/FK behavior remain unverified. Lifecycle comparisons add 269 pure source number cases and 31 actual source lifecycle/hygiene operations with persistent-state snapshots, plus synthetic full-schema SQLite-FK cleanup/fault/suppression/calendar/batch tests (687 assertions per runtime). These intentionally do not certify native constraints, concurrent sends/deletes or real migration. Export comparisons add 34 selected original export operations with separately labeled CSV formula protection and cursor/resource/read-boundary tests (68 assertions per runtime). Settings comparisons add 36 actual source operations and 11 appearance DTO cases plus credential/role/repair/failure/conflict guards (137 assertions per runtime). Source ciphertext is a synthetic marker, not Fernet/vault parity. Calendar comparisons add 38 source operations, 30 DTO acceptance cases and 6 meeting-placeholder cases (148 assertions per runtime); the oracle explicitly restores SQLite UTC results, corrects the known stale attendee response against a fresh source read, and excludes monotonic updated_at differences. Invoice comparisons add 32 source operations, 34 DTO acceptance cases, 400 actual source total cases and 333 Python rounding cases (866 assertions per runtime). Comparisons explicitly exclude disabled checkout URLs/random tokens/monotonic updated_at and normalize event ordering. Inbox comparisons add 38 source operations with conversation/contact/follow-up/campaign-contact snapshots (97 assertions per runtime), explicit raw-error redaction and UTC timestamp normalization. No native MySQL/MariaDB, real WordPress activation, real login, HTTP upload, scheduler or browser workflow is certified by these offline tests. The separate native CI runner is not evidence until its structured results pass. Current WordPress tests are hook/request/response mocks, not a booted WordPress server. Contact/list comparisons additionally cover 48 original-source AST operations and 38 DTO cases against five synthetic SQLite tables, with a fixed clock and omitted ORM relationships. Each runtime adds 614 mock data-dispatch assertions, including fault-injected rollback, authentication, path-ID protection, query binding and membership retry tests. They do not establish native FK cleanup, native collation, concurrent idempotency, real uploads, or browser integration. The variable suite adds 420 pure-source render cases (render/context/unknown checks), 27 source-registry operations, 32 DTO cases, 50 source segment-estimate cases and 17 scalar-conversion cases. Registry comparison uses synthetic SQLite with an explicitly monotonic committed-ID adapter setting; it is not native identity allocation or concurrency proof. Non-standard JSON NaN/Infinity, all float representations, native collation, HTML safety and carrier billing are not certified. Auth comparisons cover 21 original-source AST business scenarios and 24 DTO cases with an explicitly synthetic password codec and two-table SQLite account adapter. The 57 dispatch assertions per runtime additionally simulate rate counters without expiry; they do not prove MySQL SQL, expiry, atomicity, HTTP, Argon2 or browser behavior. Previous38,646-assertion/363-HTTP-case claims describe the missing build and are **not reused as current evidence**.

## Download and operator setup

**No new interim ZIP is being produced while the requested full migration is unfinished. Existing archives are older incomplete snapshots, not the current working tree or a ready release.**

`artifacts/sender-suite-development.zip` is a WordPress-layout development plugin ZIP (its top-level folder is `sender-suite/`), not a complete application. `sender-suite-recreated-INCOMPLETE.zip` separately contains the repository-backed recovery material and test tools; it is not the WordPress-upload layout.

For **isolated development only**, back up the test WordPress database, use an HTTPS test site, upload the development ZIP under Plugins > Add New > Upload Plugin, and activate it only on a compatible test host. Actual native activation is still unverified. Open Settings > Sender Suite and choose an operator username/password. There is no shared default password. Setup does not send messages. Failed activation can leave retained partial tables; do not use a production database.

Additional allowlisted workspace preferences/compliance/muted-sender rows and the table-prefix/purpose/row-bound encrypted notification credential envelope are now active (see the settings document). The active global encrypted Config names remain APP_NAME, APP_ENV, ADMIN_USERNAME, ADMIN_PASSWORD, LOGIN_PASSWORD, SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES and RATE_LIMIT_LOGIN. The UI edits the operator credentials only. Encryption depends on the site's WordPress salts; salt rotation needs an explicit recovery plan. Never export plaintext settings.

Intentional safety differences from the original development defaults: production environment by default, operator setup required before public auth, no default password, stricter malformed-JWT rejection, and login throttling keyed by server REMOTE_ADDR rather than trusting arbitrary X-Forwarded-For. Configure trusted reverse-proxy real-IP handling at the server; do not simply trust a client header. Session signing keys remain unchanged on password rotation, so existing sessions are not automatically revoked. Changing the operator username does not delete or deactivate previous named accounts; account-management UI and an explicit revocation/recovery workflow remain outstanding. Rate counter expiry and concurrent behavior are not native-tested; stale-row housekeeping is not implemented.

## Installation boundary

This development plugin is not ready for production installation. Native activation requires MySQL/MariaDB with InnoDB, Argon2id and AES-256-GCM. The installer refuses SQLite before issuing SQL. Only the non-sending contact import cron worker is installed; no provider registration or sending callback is enabled. The queue requires working WordPress cron and native MySQL advisory locks.

DDL changes are not transactionally reversible on MySQL. The installer takes a database advisory lock, creates tables idempotently, checks column presence/types/nullability/auto-increment and engine, and adds deferred foreign keys. It does **not yet certify index/foreign-key drift or historical upgrade migrations**. A changed schema fingerprint fails rather than attempting destructive repair. Failed installations may leave retained partial tables for diagnosis.

Source PostgreSQL full-TEXT/oversized unique indexes use explicitly listed full-value SHA-256 generated-column alternatives; these have a theoretical collision difference and require native engine tests. Nonunique oversized indexes use prefixes, never silently truncated unique keys. Collation, JSON, timezone, unsigned/signed ranges, FK names, SQL modes, concurrency and native constraint behavior still require host testing. Source VARCHAR bounds and valid UTF-8/NUL-free String/Text are now checked before SQL, independently of MySQL strict mode; the actual native engines have not been exercised.

Deactivation/uninstall does not delete application tables. An explicit, tested data-removal policy is still needed before a release.

## Reproduce from this repository

Build-only dependencies are isolated from the plugin runtime:

```
npm ci --prefix wordpress/tooling --ignore-scripts
python3 -m pip install --target wordpress/.cache/python unicodedata2==15.0.0 phonenumbers==8.13.52
python3 wordpress/tools/rebuild_metadata.py
python3 wordpress/tools/vendor_phone.py
node wordpress/tools/compile_unicode.cjs
python3 wordpress/tools/generate_csv_fixtures.py
python3 -m pip install --target wordpress/.cache/python sqlalchemy==2.0.36 aiosqlite==0.20.0 pydantic==2.10.3
python3 wordpress/tools/generate_auth_fixtures.py
python3 wordpress/tools/generate_contact_fixtures.py
python3 wordpress/tools/generate_variable_fixtures.py
python3 wordpress/tools/generate_template_fixtures.py
python3 wordpress/tools/generate_reply_fixtures.py
python3 wordpress/tools/generate_import_fixtures.py
python3 wordpress/tools/generate_import_job_fixtures.py
python3 wordpress/tools/generate_url_import_fixtures.py
python3 wordpress/tools/generate_analytics_fixtures.py
python3 wordpress/tools/generate_sequence_fixtures.py
python3 wordpress/tools/generate_number_filter_fixtures.py
python3 wordpress/tools/generate_contact_action_fixtures.py
python3 wordpress/tools/generate_export_fixtures.py
python3 wordpress/tools/generate_settings_fixtures.py
python3 wordpress/tools/generate_calendar_fixtures.py
python3 wordpress/tools/generate_invoice_fixtures.py
python3 wordpress/tools/generate_inbox_fixtures.py
python3 wordpress/tools/generate_sms_receipts_fixtures.py
python3 wordpress/tools/generate_followup_fixtures.py
python3 wordpress/tools/run_checks.py
python3 wordpress/tools/checkpoint.py
python3 wordpress/tools/write_remaining_checklist.py
python3 wordpress/tools/package_plugin.py
python3 wordpress/tools/package_recovery.py
```

The pinned PHP tooling declares Node>=24.18/npm>=11.16; this sandbox currently ran successfully on Node22.22.3/npm10.9.8 but emitted engine warnings. Supported toolchain provisioning remains open.

Frontend reproduction (run npm commands from `frontend/`; does not edit components):

```
npm ci --ignore-scripts
npm run build -- --outDir ../wordpress/sender-suite/app
npm test -- --reporter=dot
python3 wordpress/tools/licenses.py
```

Three frontend production dependencies lack standalone notices in the installed package and remain flagged for review in `sender-suite/licenses/frontend-dependencies.json`. Do not describe license review as complete.

## Initial data API shapes

All paths below are relative to `/wp-json/sender-suite/v1/`, require an own-application session cookie, and are not yet original-URL/browser certified:

- `POST contacts/import/preview` and `POST contacts/import/csv` take multipart `file`; the latter also takes form `list_id`, `new_list_name`, `skip_duplicates`, `column_mapping`, `tags`, `auto_tag_rules`. See `docs/CSV_IMPORT_WORKFLOWS.md` for transaction/privacy boundaries.
- `POST contacts/import/jobs` takes the same multipart options and returns a durable queued job with HTTP 202. `GET contacts/import/jobs/{job_id}` returns owner/admin-only progress, completion summary and live list counts. `GET contacts/import/jobs/{job_id}/errors.csv` serves the full private CSV report, not JSON-wrapped bytes. The periodic non-sending WordPress cron worker commits contacts and counters every 100 rows, clears raw CSV on completion/failure, and refuses to replay interrupted partial imports. This does not yet certify the full browser import wizard or native scheduler.
- `POST contacts/import/url` accepts the original strict JSON options, fetches a public CSV outside the job SQL transaction, then returns the same HTTP 202 durable job. PHP cURL is required for validated-address pinning; there is no unpinned fallback. Redirects, credentials, private/special addresses, compressed responses and nonstandard ports are rejected. See `docs/CSV_IMPORT_WORKFLOWS.md` for deliberate safety differences and WordPress network-policy integration.
- `GET analytics/{overview,timeseries,funnel,by-campaign,calls}` reads real stored data under own-app auth and no-store responses. Days/channel/campaign/limit validation is source-derived. Source global contact/follow-up figures are intentionally not scoped by message channel/campaign; calls use a rolling instant rather than the message midnight-UTC window. These are not real send/call workflows.
- `GET/POST sequences/`, `GET/PUT/DELETE sequences/{sequence_id}` implement the sequence builder/version library, not a sequence worker. See `docs/SEQUENCE_WORKFLOWS.md` for validation, deletion, snapshot and concurrency boundaries.
- `GET/POST contacts/`; `GET/PUT contacts/{id}`; `GET contacts/tags/`.
- `GET/POST lists/`; `PUT lists/{id}`. As in the original API, list creation/editing uses **query** fields `name` and `description`, not JSON.
- `GET/POST lists/{id}/contacts` (POST body is a JSON integer array).
- `POST lists/{id}/contacts/add-all` (JSON filters); `POST lists/{id}/contacts/remove` (JSON `contact_ids`, or `scope: "all"` with optional `search`). Removal changes memberships only, not contact records.
- `GET lists/{id}/stats`.
- `GET/POST variables/`; `PUT/DELETE variables/{id}`; `POST variables/sync`.
- `GET/POST templates/`; `GET templates/categories`; `GET/PUT/DELETE templates/{id}`; `POST templates/{id}/duplicate`; `POST templates/{id}/to-email`.
- `POST templates/preview` uses query body/subject/html_body/channel/contact_id plus optional sample overrides, not a JSON DTO.
- `GET/POST autoreply/` and `automations/`; `GET/PUT/DELETE autoreply/{id}` and `automations/{id}`; `POST autoreply/test` (JSON `body`) and `automations/test` (JSON `text`/optional `tags`). These tests do not execute actions.
- `POST ai/classify` (JSON `text`) returns the original built-in offline classification, not a cloud AI/agent response.
- `GET notifications/`, `GET notifications/unread-count`, `POST notifications/{id}/read`, `POST notifications/read-all`. Browser push delivery is not implemented.
- `POST variables/preview` uses query `body` and optional `contact_id`, as in the source API. It reports resolved/empty/unknown placeholders without sending.

These handlers have actual persistence logic, not placeholder successes. They remain development-only: most other source APIs, all sending workers, native-engine behavior and the full application interface are still outstanding. List-member tie ordering follows the original created-at-only query; it has not acquired the stable tie-breaking used by the main contact listing. Concurrent duplicate-name/email checks and membership races still need native review. Variable sync preserves the original helper’s behavior, including potentially stale counters for keys absent from a later scan. Deleting an imported registry entry leaves contact data intact, so a later sync can rediscover it. Explicit-null writes to non-null model columns and internal-error envelope parity remain uncertified.

## Remaining work

`docs/contracts.json` and `docs/REMAINING_WORK.md` retain every source method/path. Current status: **369 missing handlers,139 partial health/auth/contact/list/variable/template/reply/classification/notification/import/analytics/sequence/settings/calendar/invoice/inbox/status-webhook/follow-up-control handlers,0 fully certified contracts**. The six actual preview/import/URL/job/status/error-report API handlers are included in the partial count; the separate administrator preview diagnostic is not counted.

Native auth/account/session/rate-limit certification and concurrency recovery, full settings/provider wiring, contact/list deletion and related-record cleanup, remaining bulk actions, native URL transport and queue/lock certification, email accounts/verification, campaigns/follow-ups, inbox/SMS/calls, scheduler, webhooks, invoices/calendar, remaining profile workflows, AI/MCP, analytics/notifications, complete migration, unchanged frontend integration and native release tests still need recreation/implementation. Do not expose the unchanged frontend as a working application until those routes and permissions exist.

This is a development checkpoint, not completion of the full migration request.
