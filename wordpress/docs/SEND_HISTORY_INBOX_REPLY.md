# Stored outgoing history and queued inbox SMS replies

Development migration, not a ready plugin or full inbox/send parity. No real SMS was sent. Original backend/frontend remain untouched.

## Stored outgoing history

Authenticated `GET /send/history` reads native stored outgoing rows regardless of whether experimental sending is enabled. It never invokes the transport, starts cron, or mutates data. The existing own-application authentication, origin and no-store response boundaries apply; a WordPress administrator cookie is not a bypass. As in the source, authenticated application viewers can read shared history, not just messages they personally enqueued.

Implemented source filters: default SMS channel; normalized sms/email/all channel validation; arbitrary status filters; failed includes cancelled; body/error wildcard search; total/page/per_page/items; newest creation time first. Ties use ID ascending for deterministic pagination. Contact enrichment is batch SQL, including source name/business/phone fallbacks (text `0` is preserved). Response carries actual stored provider IDs, status/times, body and Unicode code-point preview, campaign and conversation IDs. Native unknown/queued states are reported truthfully, not disguised as sent.

Deliberate privacy hardening: raw provider errors are replaced with `Message delivery reported an error.`; raw response metadata, credential envelopes, email headers and attachments are not selected. Search retains source SQL wildcard semantics, including matching stored errors, so this remains an authenticated shared-data read service, not a public diagnostic. Page/per-page/search/status/channel bounds are enforced, including NUL and invalid UTF-8 rejection for text filters. A 4 MiB stored-body page budget is checked before loading source TEXT, plus an 8 MiB encoded response budget. Oversized results return 413, not silent truncation or invented empty history.

Evidence: `send-history-test.php` has 137 assertions per runtime including 42 selected actual Python source AST history/channel comparisons, redaction, Unicode/SQL-wildcard/search/channel/status pagination, source text `0`, orphan-contact projection, no writes and oversized JSON/page tests. The fake DB omits FK enforcement for source orphan vectors. It does not prove native collation, isolation, HTTP, browser or migrated-database behavior.

## Guarded queued inbox replies

`POST /inbox/conversations/{conversation_id}/reply` is a restricted migration subset behind boolean `SENDER_SUITE_ENABLE_SMS_OUTBOX===true` (default false). Requires an own-application administrator session, same-origin request, an Idempotency-Key and JSON `{ "gateway_setting_id": 1, "body": "Final SMS reply text" }`. Gateway selection is explicit; it never guesses a sender. Text is stripped as in the source and must meet the outbox's final-text, consent, phone and segment restrictions. URLs containing reply data are rejected rather than logging private message text in query strings.

Reply lookup uses the path conversation ID, not query/body overrides. It verifies the thread is SMS; email threads return an explicit unavailable response and **never fall back to SMS**. The shared outbox locates the unique SMS conversation, then the handler checks that the resulting stored message is attached to the requested thread, all within the route transaction. A failed check rolls back. Worker scheduling is attempted before enqueue; scheduling failure does not create a message.

HTTP 202 and status queued acknowledge durable admission only. Provider ID remains null until an actual provider acknowledgement is committed. Key replay returns the existing message; content changes under a used key conflict. Reply and direct-send paths share a user/key namespace deliberately, so one operation cannot silently duplicate the same key. No inline provider call, automatic POST retry or unknown-outcome resubmission is added. Existing shared worker consent/config/body/claim/reconciliation protections still apply. Cancellation uses the existing preclaim native message cancellation endpoint.

Not implemented here: source query-body compatibility, automatic sender routing, template-only replies/use counters, email reply transport/attachments/subjects, scheduled inbox replies, provider-qualified live delivery, full frontend. Unsupported fields fail explicitly, rather than return successful placeholders. These differences keep the source contract partial.

Evidence: `inbox-reply-test.php` has 50 assertions per runtime using synthetic WordPress request/session/cron, SQLite and fake transport. It covers auth/origin/permission/channel/body/key guards, scheduler/commit failures, shared worker delivery claim, no inline transport, idempotency, opt-out admission and history projection. Additional dispatcher tests prove default-off replies neither queue nor schedule and stored history remains readable. This is not actual HTTP/MySQL/WordPress cron/concurrency or browser qualification.

## Native qualification attempt

The existing isolated native WordPress/MySQL workflow definition was attempted in `.github/workflows` on this branch, but GitHub rejected the push: the connected GitHub App lacks `workflows` permission. No workflow started and no native evidence was obtained. The rejected workflow-only local commit was removed; the original proposed definition remains in `wordpress/tools/native-wordpress-workflow.yml`, with native probes under `wordpress/tools`. Workflow-write permission on the GitHub connection (or execution by an authorized runner) is still required. No credentials were requested or stored in chat. All native qualification flags remain false.
