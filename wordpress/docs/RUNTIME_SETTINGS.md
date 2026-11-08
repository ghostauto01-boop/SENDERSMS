# Native runtime settings — unreleased

This increment implements workspace preferences and encrypted notification credential storage. It does **not** implement notification delivery, provider connection tests, complete settings, frontend integration or a ready plugin. No interim ZIP was produced.

## Implemented routes

Under `/wp-json/sender-suite/v1/`, own-application authentication and the common same-origin, transaction, error-redaction and no-store boundary apply:

| Route | Behaviour |
|---|---|
| `GET settings/appearance` | Workspace mode, palette, density, radius and font, with source defaults |
| `PUT settings/appearance` | JSON partial update of validated preferences |
| `GET settings/compliance` | Stored consent, auto-opt-out, suppression and campaign-suppression preferences |
| `PUT settings/compliance` | Optional boolean query controls; source aliases `ec`, `ea`, `es`, `ek` and the long setting names are supported |
| `GET settings/notifications` | Provider metadata/event flags only; no credential plaintext or ciphertext |
| `PUT settings/notifications/pushover` | JSON credential update/clear and optional enabled flag; encrypted at rest |
| `GET settings/notifications/muted-senders` | Current sender list and MTN/AIRTEL defaults |
| `PUT settings/notifications/muted-senders` | Comma-separated `senders` query value, Unicode uppercase normalization and deduplication |

All writes require the authenticated application's **admin** role. A WordPress administrator cookie is not an application-session or role bypass. Authenticated non-admin accounts may read the non-secret settings. Unsupported methods/providers are not successful stubs.

## Source behaviour and intentional fixes

- Appearance choices/defaults are source-derived. Omitted/null values leave existing values alone; unrelated appearance DTO fields are ignored as in the source. The source's declared-but-unused `font_scale` is explicitly rejected when non-null rather than falsely reporting that it was applied. Malformed or out-of-enum stored appearance values fall back to known defaults rather than returning arbitrary style objects/strings.
- Compliance accepts both short source API aliases and the long keys used by the original frontend (which otherwise silently saved nothing). Conflicting aliases fail validation and the whole transaction rolls back. Known rows are repaired into their canonical category when saved. Reads allowlist the four keys instead of exposing arbitrary rows misclassified as compliance; invalid non-null stored boolean strings fail closed without echoing their value. Saving these preferences does **not** mean unported senders/workers enforce them. Delivery-time policy remains an open gate.
- Muted sender casing uses generated Unicode 15 full uppercase mappings, including expansions and non-ASCII scripts, rather than ASCII-only `strtoupper`. Empty string deliberately saves an empty list; absent/null stored value uses carrier defaults. The input limit is 64 KiB; deduplication uses a set.
- Notification secrets are accepted only in a JSON body. URL `user_key`/`app_token` is refused to avoid perpetuating the source's secret-in-query pattern. The original frontend still needs its native adapter changed to JSON; it is not represented as integrated. `is_enabled` may be supplied once in JSON or query. Unknown JSON provider fields are rejected rather than silently appearing saved. Only the original supported Pushover configuration is implemented; event flags are read, not writable through this source route.

## Credential storage and recovery

The complete two-field credential JSON is AES-256-GCM sealed through the existing vault. Associated data binds it to purpose, provider, row ID and the concrete database table prefix. Ciphertexts cannot be copied between fields/purposes/providers/rows/prefixes; protection also depends on the supplied site's key material. This is not source Fernet ciphertext compatibility or complete multisite qualification.

Omitted/null credentials preserve values; explicit empty strings clear them. A valid existing envelope supports one-field updates. A malformed, tampered, copied or legacy plaintext/Fernet value does **not** become a plaintext fallback. Partial edits then fail with a redacted 503. An admin can explicitly replace **both** fields (including two empty strings) to recover that row without decoding unknown legacy storage. Salt/key rotation, whole-database migration, backup/restore and operator recovery still require an explicit qualified migration plan; rotating WordPress salts also affects existing encrypted auth configuration.

Metadata GET never decrypts or returns secrets and never claims provider connectivity. `is_enabled` means a stored preference, not proof that keys are readable/valid or that a notification was sent. There is no REST plaintext-decryption endpoint. The credential getter is for trusted internal PHP consumers only. No `/test` route or notification sending worker is enabled by this module.

Writes use conditional prior-value comparisons to fail on detected conflicts; creation relies on the source unique keys. These checks, transaction rollback and first-save failure handling are offline-tested, **not native concurrency/locking/reconnection proof**.

## Evidence

- **36 actual-source settings operations** with response and selected persistent-state comparisons, covering appearance, compliance, muted senders and notification configuration.
- **11 source appearance DTO cases**.
- Source encryption is an explicitly synthetic oracle marker; comparisons inspect selected logical credentials, not ciphertext parity.
- **137 assertions per PHP runtime** in the settings suite, including native vault round trips, redaction, tamper/copy/prefix rejection, complete replacement of unreadable values, failed-first-save cleanup, write failures, simulated compare-and-swap conflicts, late-invalid-field rollback, category repair, enum/size guards and malformed stored state.
- **614 shared dispatcher assertions per runtime**, including settings login/origin/admin-role enforcement, query-secret refusal, persisted JSON secrets, private reads, long compliance aliases and rollback.

All execution is PHP-WASM with synthetic SQLite, a fixed source clock and isolated test keys. This does not certify real WordPress/PHP hosting, MySQL/MariaDB, HTTP/browser behaviour, role-management UI, provider credentials, concurrency, encryption rotation or actual notification delivery. No notifications or real provider messages were sent. Native HTTP smoke references include non-sending settings checks but remain unexecuted.

Global `Config` activates nine auth/scheduling source configuration names, including the validated `DEFAULT_TIMEZONE` used by follow-up day views (default `Africa/Lagos`). A dedicated timezone administration UI remains outstanding. The additional allowlisted runtime rows and provider envelope here do not make all original provider/environment settings active. Remaining settings, access/account management, OAuth, gateway/device/call configuration and transport integration remain in `COMPLETION_CHECKLIST.md`.
