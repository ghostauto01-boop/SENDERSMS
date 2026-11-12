# Native WordPress interface integration (0.12.0-dev)

This is a development interface, not the completed application or a release-certified plugin. The source ledger is still 349 missing / 159 partial / 0 native-certified contracts out of 508. These counts are not a completion percentage.

## Application entry

WordPress Settings → Sender Suite now has **Open Sender Suite application**. The document is served only on the explicit `?sender_suite_app=1` query. No rewrites, WordPress homepage takeover, public payment-link claims, or full-application shortcode are introduced. Only exact string `1` is accepted; normal site requests and other query values remain untouched. GET/HEAD are supported; document POST returns 405. Entry does not grant a WordPress administrator an application session: the native application's own operator cookie is still required by every protected API.

The shell resolves assets under the plugin's `app/` directory and API roots through WordPress `rest_url`, including site subdirectories and plain `rest_route` URLs. A per-document nonce covers every script, with no-store/private caching, same-origin frames/referrers, no-sniff, no objects/workers/base-tag injection, and same-origin connections. The frontend rejects foreign-origin or credential-bearing runtime URLs rather than falling back to Python or a different service. Hosts serving plugin assets from a separate CDN origin need a reviewed configuration; they are not silently permitted.

Hash routing (`#/contacts`, `#/send`, etc.) preserves the WordPress document URL on navigation/reload. Hardcoded original API downloads/streams are transformed to the native root; page queries use the hash query rather than WordPress's outer query. The original root-scoped service-worker registration/manifest are removed from the build. Existing website service workers are not unregistered or modified. Push and unimplemented MCP transport discovery fail closed. All ordinary API requests use a guarded same-origin Axios client with no automatic request retries. Multipart uploads allow the browser to generate the content boundary. Unimplemented WordPress routes surface an explicit migration-unavailable error instead of calling the original backend or returning successful placeholders.

## Authentication and interface

The original source UI is copied into an ignored build workspace, with reviewed overlays/transformations. Original `frontend/`, `backend/`, dependency manifests and deployment files are unchanged. The copied UI keeps the original pages/layout/navigation, but their existence does **not** mean all backing workflows are implemented. A persistent development warning states the incomplete state and SMS enablement flag.

The login overlay uses `/auth/admin` with the configured operator password, not a hardcoded `admin` username. It stores no password/token in session/local storage; only the existing HttpOnly application cookie authenticates requests. It removes the old frontend's `sendsms.lastPassword` session item if present. Missing/failed access metadata defaults to password-required. Application setup still uses the WordPress Settings credential form.

## Truthful native SMS composer

The copied Send page is replaced with an explicit restricted single-contact queue composer plus stored outgoing history. It does not adapt the old source's synchronous send response into fictional sent counts.

- Explicit enabled/configured gateway selection; final text; contact choice; optional local-time schedule converted to UTC. The backend rechecks consent, suppression, canonical phone, sender credentials, text/segment bounds and native outbox admission.
- A new random key is generated once per deliberate operation. The exact payload stays frozen in memory across an explicit retry; no automatic POST retry is added.
- Before enqueue, session storage must accept the key/path recovery record. It never stores the SMS body or provider credentials. On acknowledged enqueue it saves the actual server message ID. 202 displays queued, not sent/delivered.
- Ambiguous acknowledgement retains the same key and payload for explicit retry. After reload without a recoverable payload, a previous pending record blocks blind new submission and asks the operator to review status/history. Known-ID refresh is a read; preclaim cancellation uses the existing guarded endpoint. Starting a separate intentional request requires confirmation and never pretends to cancel or safely resend the previous operation.
- SMS inbox reply sends direct operators to this composer, with only conversation/contact IDs in the hash. It reloads the authoritative contact ID from the native thread and locks contact editing; final text must be re-entered, not leaked into the URL. Email threads are rejected by the server with no SMS fallback. Template/email/full-source reply parity remains incomplete.
- Default-off hosts disable the sending controls while leaving stored history available. The runtime flag reflects the existing server constant, not a new operator-facing enable switch.

Session storage is per-tab recovery convenience, not a durable cross-device outbox registry. Loss/clearing of browser storage or deliberately generating a new key does not establish that an earlier submission was absent. Unknown outcomes still need manual provider review; no recovery UI or guarantee against intentional repeated distinct requests is claimed.

## Reproduction and provenance

From repository root:

```
npm ci --ignore-scripts --no-audit --no-fund
python3 wordpress/tools/build_native_ui.py
python3 wordpress/tools/verify_native_ui.py
node_modules/.bin/vitest run --config wordpress/ui/vitest.config.mjs
node wordpress/tools/test_native_ui.mjs
python3 wordpress/tools/run_checks.py
```

`build_native_ui.py` verifies the 165 original frontend hashes, copies source/public assets into `wordpress/.cache/ui`, applies fail-on-source-drift transformations and overlays, typechecks with TypeScript, and builds to the plugin app directory with Vite. The manifest records build-script/config/root-lock/overlay/generated-asset hashes. `verify_native_ui.py` checks exact asset membership, hashes and removal of original PWA artifacts; the full offline runner and development packager require this check. Build dependencies remain outside the plugin ZIP. Existing production dependency notices are retained, but the previously flagged license review is not declared complete.

## Exact evidence boundaries

- Full PHP-WASM suite: **44,190 assertions, 108 first-party syntax checks**, PHP 7.4/8.3. Shell suite: **61 assertions per runtime**, validating the explicit launch gate, rendered runtime/asset paths, no original API/SW, nonce coverage and restrictive headers.
- Native-runtime Vitest/jsdom: **38 tests** for same-origin roots, pretty/plain/subdirectory request construction, reserved query/path traversal rejection, assets and hash navigation.
- Actual Chromium 149 built-UI fixture journeys: **36 assertions** across pretty/plain/default-off modes. These use actual PHP-rendered HTML/CSP and bundled React, but a clearly synthetic local REST fixture, NOT WordPress/MySQL. They check custom-operator login, no password storage, native contact read, stable-key/frozen-payload retry after a simulated committed-but-lost acknowledgement, truthful queue state, cancellation, reload recovery and default-off controls.
- **141 build provenance checks** at this checkpoint. No live SMS, production DB or original backend was used.

All native WordPress/MySQL/full-browser qualification flags remain false. The isolated native HTTP runner now includes document GET/HEAD/405/header probes but has not executed; the GitHub App still lacks workflow-write permission. Synthetic HTTP/browser integration must not be relabeled real WordPress activation, actual cron, native concurrency or full end-to-end certification.

## Still outstanding

Most source backend workflows, all complete providers/accounts/mailbox/call/campaign/Ads/sequence/follow-up/AI/MCP executions, full attachments/download and public tracking/payment workflows, operator recovery, native install/migration/FK/race/security/hosting tests, complete mobile/accessibility/journey acceptance and release packaging. Unsupported existing pages may show explicit errors; full control-level hide/disable coverage has not been reviewed. Full source parity, not the presence of copied pages or passing synthetic browser tests, remains the acceptance criterion. No new ready ZIP or Drive replacement is produced by this checkpoint.
