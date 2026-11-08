# Sender Suite WordPress-native migration — INCOMPLETE

This is a **repository-backed WordPress-native migration** of the original application. WordPress replaces the Python backend at runtime; a separate Python service is not the proposed solution. The full SMS/email application is not finished. The repository source, not any unavailable archive or historical chat claim, is the behavior specification.

All recreated runtime/tests/tools are inside `wordpress/` on the session branch. `tools/native-wordpress-workflow.yml` is a proposed isolated native CI workflow, not an installed or executed workflow. The current GitHub App connection rejected workflow creation because it lacks `workflows` permission. Original `backend/`, `frontend/`, deployment files and root dependency manifests are unchanged. No original application/config/environment module was imported, no environment file was read, no production database/provider was contacted, and no messages were sent.

## What has actually been recreated

- Source AST inventory:73 tables,1,120 columns,235 indexes,61 foreign keys,511 HTTP declarations/508 distinct contracts,71 settings declarations,165 unchanged frontend file hashes.
- A source-derived MySQL/MariaDB schema planner, explicit SQL alternatives, checked/prepared database adapter with nested transactions, guarded installer and data-retaining lifecycle.
- Site/field-bound AES-256-GCM encrypted-value utility with tamper checks and key serialization/debug protections. **Eight initial auth settings now persist encrypted in a custom table**, with a capability/nonce-protected operator setup screen. This is not a claim that all 71 source settings or provider credentials are wired.
- Pinned/scoped phone8.13.52, Locale2.7.0 and mbstring1.31.0, upstream licenses, modified-file hashes and Unicode15 lower/decimal/NFKD data.
- Original-source-compatible UTF-8 replacement and CSV parsing, header mapping, phone/email classification and read-only preview service.
- Own-app-authenticated multipart CSV preview and synchronous import APIs: real contact persistence, additive phone/email/name/alias merges, custom fields/variables, global/row/conditional tags, destination lists, duplicate/company suggestions and error reporting. Imports never grant consent or clear existing opt-outs. Durable background jobs, batch progress polling and private CSV error downloads are also implemented; URL imports and full frontend/native-host integration are still missing.
- A separately labeled WordPress-administrator-only CSV diagnostic with capability/nonce/upload/size checks. This is **not** the original own-app-authenticated import API, and performs no contact writes.
- One public REST health handler and five initial own-application auth handlers (access/admin/login/logout/me), at `/wp-json/sender-suite/v1/`. These are not yet mapped to the original `/api/v1/` URLs or certified against real WordPress HTTP. Other application APIs are deliberately not registered as fake successful placeholders.
- Own-app-authenticated contact creation/read/edit, duplicate checks, phone/email normalization, tag replacement/addition/removal, channel/status/list filtering, allowlisted sorting and stable contact pagination.
- Own-app-authenticated list creation/edit/listing, live membership counts, member browsing, deduplicated addition, server-scoped add-all, non-destructive removal and status statistics. Contact/list deletion remains missing; this is not yet complete CRUD.
- Variable-registry creation/editing/deletion, built-in protection, discovery from custom fields, aliases, fallbacks, source/search filters and authenticated message analysis. This alone is **not** a sending workflow.
- Source-derived text rendering (double/single braces, greeting fallbacks, unknown-variable marking, cleanup and scalar formatting) and source-compatible SMS segment estimates. No HTML sanitization or carrier billing is certified.
- SMS/email template creation/editing/filtering/categories, active flags, duplicate/SMS-to-email, source metrics, structured unknown-variable errors/suggestions and query personalization preview. Attachment size/count/storage limits preserve source behavior; responses omit base64 payloads. No sending, file fetching or providers are invoked.
- Auto-reply rule and If/Then automation create/read/edit/delete/list, priority ordering, channel filtering, sender existence checks (including unified negative mailbox IDs), condition validation and non-sending rule tests. The built-in lexicon classifier is translated to PHP; it requires no AI API. Rule actions are stored, **not executed**: inbound triggers, per-contact cooldowns, destructive actions, real sends and counters are not wired.
- In-app notification centre listing, unread/latest counts, mark-read and mark-all-read, isolated to browser-provider records. Push subscriptions, VAPID setup, browser/provider delivery and notification-emitting business jobs remain missing.
- Explicit source-model defaults for these business tables; no assumption that Python application defaults become native SQL defaults.
- Argon2-only production password codec, HS256 cookie sessions, current-account activation/role checks, same-origin guards, scoped JSONP/envelope hardening and persistent login throttling. Production crypto, real cookies and concurrent native throttling remain unverified. WordPress administrator login is not an application-session bypass.
-132 freshly compiled unchanged frontend assets, with available production dependency notices retained. They are **not served at the site root** while original API URL integration and business workflows are missing.
- Reproducible generation, test and recovery-packaging tools. Caches/node_modules/environment files are excluded from recovery packaging.

## Freshly executed evidence

See `tests/checks-summary.json` and `tests/frontend-evidence.json`.

-35,614 functional assertions across PHP WASM7.4.34-dev and8.3.36, including offline account/DTO and auth-dispatch tests.
-62 first-party PHP syntax checks.
-3,075 vendor parser checks, **reported separately from functional coverage**. The PHP8-only guarded mbstring bootstrap is intentionally not parsed with PHP7.4. Parser memory is raised to512M only inside that test.
-1,547 vendor file hashes and165 unchanged frontend source hashes verified.
-Recorded unchanged frontend run (not rerun in this iteration):269 tests/43 files pass; production build passes and generates132 assets. A jsdom navigation warning is not native browser workflow proof.

Template comparisons add 40 source operations, 42 DTO cases, 6 attachment coercion cases, 6 file/message size boundaries and 136 typo-suggestion cases (288 assertions per runtime). Helpers are extracted with AST, not original application/config imports. These use a fixed clock, synthetic SQLite with monotonic committed IDs, and omitted relationships. Reply/notification comparisons add 68 source/fixture operations, 108 DTO cases, 273 classifier cases, 200 individual rule-match cases and 504 condition cases (1,289 assertions per runtime). Original DTO checks model FastAPI v2 from-attributes adaptation, not actual HTTP. Synchronous import comparisons add 34 pure helpers and 23 original API/service imports with full contacts/tags/lists/memberships/aliases state snapshots (80 assertions per runtime). Background-job comparisons add 15 original queue/create/read/error-report/worker operations, four durable commit snapshots and failure/recovery safety checks (57 assertions per runtime). SQLite discards timezone metadata on refresh, so the known UTC fixture timestamps are explicitly restored in that oracle; native timezone behavior remains unverified. Mock upload readers and advisory locks are synthetic, not actual SAPI uploads or MySQL locks. The adapter also checks UTF-8/NUL/source VARCHAR bounds before SQL to prevent silent truncation. No native MySQL/MariaDB, real WordPress activation, real login, HTTP upload, scheduler or browser workflow is certified by these offline tests. The separate native CI runner is not evidence until its structured results pass. Current WordPress tests are hook/request/response mocks, not a booted WordPress server. Contact/list comparisons additionally cover 48 original-source AST operations and 38 DTO cases against five synthetic SQLite tables, with a fixed clock and omitted ORM relationships. Each runtime adds 286 mock data-dispatch assertions, including fault-injected rollback, authentication, path-ID protection, query binding and membership retry tests. They do not establish FK cleanup, native collation, concurrent idempotency, real uploads, or browser integration. The variable suite adds 420 pure-source render cases (render/context/unknown checks), 27 source-registry operations, 32 DTO cases, 50 source segment-estimate cases and 17 scalar-conversion cases. Registry comparison uses synthetic SQLite with an explicitly monotonic committed-ID adapter setting; it is not native identity allocation or concurrency proof. Non-standard JSON NaN/Infinity, all float representations, native collation, HTML safety and carrier billing are not certified. Auth comparisons cover 21 original-source AST business scenarios and 24 DTO cases with an explicitly synthetic password codec and two-table SQLite account adapter. The 57 dispatch assertions per runtime additionally simulate rate counters without expiry; they do not prove MySQL SQL, expiry, atomicity, HTTP, Argon2 or browser behavior. Previous38,646-assertion/363-HTTP-case claims describe the missing build and are **not reused as current evidence**.

## Download and operator setup

`artifacts/sender-suite-development.zip` is a WordPress-layout development plugin ZIP (its top-level folder is `sender-suite/`), not a complete application. `sender-suite-recreated-INCOMPLETE.zip` separately contains the repository-backed recovery material and test tools; it is not the WordPress-upload layout.

For **isolated development only**, back up the test WordPress database, use an HTTPS test site, upload the development ZIP under Plugins > Add New > Upload Plugin, and activate it only on a compatible test host. Actual native activation is still unverified. Open Settings > Sender Suite and choose an operator username/password. There is no shared default password. Setup does not send messages. Failed activation can leave retained partial tables; do not use a production database.

Only APP_NAME, APP_ENV, ADMIN_USERNAME, ADMIN_PASSWORD, LOGIN_PASSWORD, SECRET_KEY, ACCESS_TOKEN_EXPIRE_MINUTES and RATE_LIMIT_LOGIN are currently active encrypted settings. The UI edits the operator credentials only. Encryption depends on the site's WordPress salts; salt rotation needs an explicit recovery plan. Never export plaintext settings.

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
python3 wordpress/tools/run_checks.py
python3 wordpress/tools/checkpoint.py
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

`docs/contracts.json` and `docs/REMAINING_WORK.md` retain every source method/path. Current status: **445 missing handlers,63 partial health/auth/contact/list/variable/template/reply/classification/notification/import handlers,0 fully certified contracts**. The five actual preview/import/job/status/error-report API handlers are included in the partial count; the separate administrator preview diagnostic is not counted.

Native auth/account/session/rate-limit certification and concurrency recovery, full settings/provider wiring, contact/list deletion and related-record cleanup, remaining bulk actions, URL imports, native queue/lock certification, email accounts/verification, campaigns/follow-ups, inbox/SMS/calls, scheduler, webhooks, invoices/calendar, remaining profile workflows, AI/MCP, analytics/notifications, complete migration, unchanged frontend integration and native release tests still need recreation/implementation. Do not expose the unchanged frontend as a working application until those routes and permissions exist.

This is a development checkpoint, not completion of the full migration request.
