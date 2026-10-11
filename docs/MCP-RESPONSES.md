# MCP tool responses: bounded, honest, always JSON

An assistant cannot tell that a result is partial unless the result says so. Before
P0-4 the MCP layer returned `text[:6000]` of an endpoint's JSON (cut mid-string, so no
longer JSON) and reduced a non-JSON body such as the CSV export to its first 4,000
characters. With 5,000 contacts the export tool delivered 26 complete rows and nothing
said it had stopped.

## The envelope

Every tool result's text is **one valid JSON object**:

```json
{
  "ok": true,
  "http_status": 200,
  "request": "GET /api/v1/contacts/",
  "truncated": true,
  "has_more": true,
  "returned": 20,
  "total": 5000,
  "next_cursor": {"page": 2, "per_page": 20},
  "cut": {"requested": 100, "kept": 20, "unit": "items", "reason": "response size limit (30000 characters)"},
  "note": "This page was too large for one response, so only the first 20 of 100 items…",
  "data": { "...the endpoint's own payload...": [] }
}
```

* `returned` / `total`: items in this response, and how many match.
* `truncated`: **more results exist beyond this response.** True on page 1 of 3, false on
  page 3 (where `returned < total` only because earlier pages delivered the rest).
* `next_cursor`: the request that continues exactly where this response stops (merge it
  into the same call), or `null`. For page-numbered lists it is `{page, per_page}`; for the
  contact export it is an opaque string.
* `cut`: present **only** when the tool layer shortened a page you did not ask to have
  shortened (it was over 30,000 characters). It records what was requested and kept. A
  list is always cut by whole items; a file body by whole lines; anything with no list to
  shorten becomes `data.preview` plus `data.chars`.
* Errors are `{"ok": false, "http_status": 422, "request": "…", "error": "…", "message": "…"}`
  and set `isError`. Plain messages (a refusal, the guide) are `{"ok": …, "message": "…"}`.
* `structuredContent` is the payload bounded the same way, with a `_truncated` marker
  when anything was cut.

## Reading everything

* **Contacts as CSV:** call `export_contacts_csv`, then repeat it with `cursor` set to
  `next_cursor` (and the same filters) until it is `null`; concatenate each page's `csv`.
  Only the first page has the header row. `limit` is 1–500 rows, `max_bytes` bounds a page's
  text (1,000–200,000); a page is shortened by the byte budget and the cursor makes up the
  difference, so nothing is skipped or repeated even if contacts change mid-export. A cursor
  from other filters, or an altered one, is a 422.
* **Any list:** use `page` / `per_page` and follow `next_cursor`.

## Page-size ceilings

A `per_page` / `limit` above the endpoint's maximum is a **422**, not a silent clamp. The
maximums are in the OpenAPI schema and in each MCP tool's JSON schema (`maximum`), and a
test fails if the two disagree.

| Endpoint | per_page / limit max |
|---|---|
| `GET /contacts/` | 100 |
| `GET /overview/campaigns` | 200 |
| `GET /inbox/conversations` | 500 |
| `GET /email/history` | 200 |
| `GET /email/inbox/conversations` | 100 |
| `GET /send/history` | 100 |
| `GET /webhooks/logs` | 200 |
| `GET /mcp/activity` (`limit`) | 200 |
| `GET /contacts/export` (`limit`) | 500 |
