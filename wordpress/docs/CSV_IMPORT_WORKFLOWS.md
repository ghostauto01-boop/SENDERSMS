# Native synchronous CSV import

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

## Still missing

Background import jobs, progress/retry/status/error-download APIs, URL fetching and the full frontend import wizard integration are not implemented by this synchronous path. The original frontend uses background jobs; adding the synchronous APIs alone does not make that wizard complete.

PHP/web-server `upload_max_filesize`, `post_max_size`, memory and execution limits can be lower than the plugin's 20 MiB cap. They need isolated host testing/configuration; this plugin cannot override pre-request SAPI upload limits. Do not treat this development build as production ready or as the completed migration.
