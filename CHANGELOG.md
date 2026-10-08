# Changelog

## Unreleased — reliability, imports, contact hygiene, and compliance

- Made campaign launch preflight advisory: audience, creatives, send windows, and missing configuration are reported as warnings; provider checks do not gate launch. Launch now locks the campaign row and commits audience assignments, state, events, and activity together, with rollback on failure and idempotent retries.
- Added request IDs and structured API errors while retaining the legacy `detail` key. Unexpected server failures are logged with traceback and stored privately for administrator lookup at `GET /api/v1/system/errors`.
- Added contact CSV preview, a pollable import-job API with persisted progress and downloadable error CSV, safe public-URL import, bounded file sizes, progress in the import dialog, country inference only from explicit/domain clues, company duplicate suggestions, and import tag rules. The existing synchronous CSV endpoint remains available.
- Made contact country nullable so an unknown country remains unknown instead of defaulting to Nigeria. Existing country values are not overwritten.
- Added contact tags to create/update and made list-member responses consistent with the full contact response while preserving their pagination envelope.
- Added typed JSON input for contact enrichment; existing query-parameter clients remain supported.
- Added multiple account reply-to addresses for mailbox address matching/routing. The legacy `reply_to` field remains the primary address and the existing Brevo sender/from/reply-to sending flow is unchanged.
- Added privacy-first suppression of clearly identified inbound OTP/PIN, bank-account, and payment-card content before it is copied into webhook records, contacts, messages, or logs. Ordinary inbound replies continue through the existing inbox path.
- Added additive schema migration statements for the new fields and support tables.
