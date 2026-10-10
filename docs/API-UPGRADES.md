# Reliability and contact API additions

All routes below are mounted under `/api/v1` and use the app's existing authentication unless noted. FastAPI's generated OpenAPI document (`/docs`) includes the request/response schemas.

## Contact CSV preview and import jobs

### Preview a file

`POST /contacts/import/preview` accepts multipart form data with a `file` field. It returns the headers, detected mapping, up to 20 sample rows, total row count, and whole-file phone/email channel counts. The preview does not create contacts.

### Queue a file import

`POST /contacts/import/jobs` accepts the same multipart `file` and optional fields as the existing CSV endpoint: `list_id`, `new_list_name`, `skip_duplicates`, `column_mapping` (JSON object), `tags` (comma-separated), and `auto_tag_rules` (JSON array). It returns `202 Accepted` with an import-job `id` and initial progress. Work runs using FastAPI's existing in-process background-task mechanism—no broker or account setup is required.

Poll `GET /contacts/import/jobs/{job_id}` for `status`, `processed_rows`, `total_rows`, `progress_percent`, counts, and (on completion) the result. Only the job owner or an administrator can read it. `GET /contacts/import/jobs/{job_id}/errors.csv` downloads the row-level error report.

Example status values are `queued`, `running`, `completed`, and `failed`. Job CSV content is cleared after completion or failure. The legacy synchronous `POST /contacts/import/csv` remains available for existing clients.

### Import from a public URL

`POST /contacts/import/url` accepts JSON:

```json
{
  "url": "https://example.org/contacts.csv",
  "file_name": "contacts.csv",
  "list_id": 12,
  "skip_duplicates": true,
  "column_mapping": {"email": "email", "First Name": "first_name"},
  "tags": ["prospect"],
  "auto_tag_rules": [{"field": "industry", "contains": "food", "tag": "restaurant"}]
}
```

Only HTTP(S) URLs resolving to public IP addresses are accepted. Redirects are not followed; files are capped at 20 MiB. Authenticated URLs and URLs requiring custom request headers are intentionally not supported.

## Contact enrichment: JSON and legacy query parameters

`POST /contacts/enrich` accepts a typed JSON body, for example:

```json
{
  "contact_ids": [101, 102],
  "allow_inferred": false,
  "limit": 500
}
```

Or select by saved list and filters:

```json
{
  "scope": "list",
  "list_id": 12,
  "filters": {"search": "Lagos", "lead_status": "interested", "tag": "restaurant"},
  "limit": 250
}
```

Supported scopes are `ids`, `all`, `no_email`, `unverified`, and `list`. Empty/ambiguous requests default to explicit IDs and return an error rather than enriching the whole CRM. `contact_ids` and the legacy `ids` spelling are both accepted in JSON. Existing query-parameter clients remain supported (`contact_ids`, `scope`, `search`, `lead_status`, `tag`, `allow_inferred`, and `limit`); an explicit query value takes precedence if supplied alongside JSON.

## List contact output

`GET /lists/{list_id}/contacts` preserves the existing response envelope (`total`, `page`, `per_page`, `items`) and each item's established ID/name/business/phone/status fields. It also returns the typed contact fields, including email, location, consent/delivery state, and tags, matching `GET /contacts/`.

## Campaign preflight and launch

`POST /ads/campaigns/{campaign_id}/validate` is informational. It returns `ok: true`, an empty `errors` array, warnings, and a summary. `summary.projection` reports how long the audience will take under the sending rules in force (daily cap, mailboxes, sending days, projected finish date); see `docs/SENDING-RULES.md`. Missing content, audience, creative splits, or provider configuration can be shown as warnings; they do not gate launch. Provider/DNS checks are not performed as a launch prerequisite.

`POST /ads/campaigns/{campaign_id}/launch` locks the campaign row where supported, creates the audience assignments, updates state, and writes activity/event records in one transaction. Repeated launch requests for an already-active or scheduled campaign are idempotent.

## Structured errors and request IDs

Every response includes an `X-Request-ID` header. API errors use `code`, `message`, `field`, `hint`, and `request_id`; `detail` is retained for existing clients. Unhandled errors and database failures are logged with server-side traceback and best-effort persisted. An administrator can look up a single error with `GET /system/errors?request_id={id}` or list recent error summaries with `GET /system/errors?limit=50`. Stack traces are never included in the list response and are never exposed to non-admin users.

## Email reply routing compatibility

The Email Senders API accepts `reply_to` as the legacy single string, a comma-delimited string, or an array of up to 10 addresses. Responses preserve `reply_to` as the primary address and add `reply_to_addresses`. The existing Brevo sender, From address, primary Reply-To header, and send flow remain unchanged; all configured addresses are considered when matching inbound mailboxes.

## Migration

The additive SQL statements are in `scripts/migrate_existing_db.sql`. Existing deployments can run that script; startup schema repair also adds missing model columns/tables when the database role permits it. Neither path requires sender/domain authentication setup or a new third-party account.
