# Access settings, contact-variable profiles and stored dashboards

These are partial native PHP implementations, not a finished application, native installation certification, or provider connectivity evidence. Original Python/frontend files are unchanged.

## Optional shared operator password

`GET/PUT /wp-json/sender-suite/v1/settings/access` requires an application session. Reads expose only `password_required` and `password_set`; mutations require the application's `admin` role. A WordPress administrator cookie does not bypass that role. `PUT` accepts a bounded JSON object; query parameters other than WordPress's `rest_route` are rejected, including on reads. Passwords are never accepted in the URL. The normalized password is 4–128 Unicode characters; the request is limited to 8 KiB. Production uses the existing Argon2id codec; offline tests explicitly use a synthetic SHA codec.

**This is an alternate shared administrator credential, not a second factor, a general user password, or passwordless mode.** The operator password remains mandatory. The source's `password_required` field name is retained but must not be represented to users as turning off the application login wall. Enabling issues a renewed HttpOnly, SameSite=Lax, Path=/ session cookie, Secure in production. The cookie is attached only after the database transaction commits. Failure responses never issue the prepared renewal token. Existing valid JWTs are not revoked by a password change or disabling this alternate credential.

Two setting snapshots are read once, then written hash-first with conditional updates and strictly increasing timestamps, including repeated saves. Failure rolls back both settings. Missing-row insert races may produce a retryable 503 instead of a version conflict 409; native concurrency has not been proven.

Intentional hardening beyond source behavior:

- Disabling clears the optional hash atomically. The source left the hash usable after disabling.
- A stored explicit false flag blocks optional authentication even if an old hash remains.
- A legacy hash with no flag remains active and metadata honestly shows it enabled.
- An unrecognized flag blocks optional authentication and returns a configuration-repair error on metadata reads.

## Contact-variable profile

`GET /wp-json/sender-suite/v1/variables/contact/{contact_id}/profile` returns the source profile's 12 standard entries, every custom imported entry, labels, shortcodes, active flags, display name and contact metadata. Inactive aliases are retained. Nested values use the shared source-style Python rendering helper. The path ID cannot be replaced by query parameters. Responses are private/no-store; a missing contact is 404.

The original read invokes variable discovery on **the first contact**, not necessarily the requested contact. That surprising behavior is preserved; the native first-contact selection is explicitly ascending ID. The dispatcher transaction commits registry discovery and the response together; commit errors return 503 and roll back the registry, rather than returning a successful profile with lost discovery.

Safety admission is intentionally stricter than the source: requested/first-contact raw notes plus custom JSON are limited to 1 MiB; at most 1,000 top-level custom fields, depth 32 and 10,000 recursive values; registry at most 10,000 entries/2 MiB; encoded profile at most 8 MiB. Oversized/out-of-native-range integers and nonfinite numbers are rejected rather than silently rounded. Malformed ordinary JSON or a non-object custom value becomes an empty map like the source. Boundaries are not native host memory/load certification.

## Stored dashboard

`GET /wp-json/sender-suite/v1/dashboard/stats` and `/dashboard/charts` use the own-application session, no-store response policy, validated `channel=sms|email|all` and `days=1..365`. Stats without days use all-time shared stored-message metrics; charts default to 30 elapsed days. Both classic `running` and Ads `active` campaigns contribute to active totals. Follow-ups use the configured local-day UTC bounds; they intentionally remain channel-independent as in the source. Recent conversations use projected fields with joined contact names, ten-item limits and deterministic tie-breaks. Recent classic campaign activity uses a five-item limit. Lead statuses merge empty/null into `new`, preserving object encoding even for an empty distribution. Chart data is sparse and counts stored outgoing/incoming rows, not verified delivery.

**`gateway_status` is `not_verified`, intentionally different from the original source's unconditional `configured`.** No gateway health check has happened. Reads never send, start a campaign/worker, call providers, or synthesize messages. Stored preview bytes are checked before projection (1 MiB); lead distributions and date groups are capped at 1,000; the encoded stats response is capped at 1 MiB. The native dashboard frontend, unified overview, provider health and complete reporting remain outstanding.

## Evidence and limits

- `generate_access_profile_fixtures.py`: selected actual source functions/DTOs and fixed-clock SQLite, 8 access cases, 7 DTO cases, 7 profile reads with registry effects.
- `access-profile-test.php`: 91 assertions per runtime including deliberately hardened disable/legacy behavior, fault/conditional-write/rollback and resource checks. Synthetic SHA, not crypto evidence.
- `generate_dashboard_fixtures.py`: actual selected source dashboard/metrics AST, 24 stats/chart operations over four channel selections and several windows, seeded classic/Ads/conversation/follow-up boundary data. No application configuration or environment module is imported.
- `dashboard-test.php`: 66 assertions per runtime with complete stored-table read-only snapshots, source comparisons, resource guards and explicit false-health prevention.
- `data-routes-test.php`: 716 dispatcher assertions per runtime overall; new coverage includes application-session/role/origin/body/query bounds, no-store, post-commit cookies, commit-failure credential/registry rollback, path-ID protection and dashboard query validation.
- Proposed isolated native HTTP probes were extended, **not executed**. They cannot be counted as native proof.

Offline PHP-WASM/SQLite is not real WordPress/MySQL/MariaDB/HTTP/Argon2id/concurrent races/browser proof. Do not package these features as a completed application or production-ready plugin.
