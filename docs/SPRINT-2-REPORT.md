# Sprint 2 report (QA findings C1–C11)

Branch `arena/191f89ff-sendersms`: `000d4c0` (C1, C2, C8) and `5ec15d2` (C3–C7, C9, C11, and a P0-3 bug found while verifying).
Raw before/after output: [`docs/evidence/sprint-1/`](evidence/sprint-1/) — `tests_s2_*.txt` per item, `repro_c6.txt`. Regenerate with
`SPRINT=2 docs/evidence/sprint-1/collect.sh` (each item's tests against the code at the end of Sprint 1, then against today) and
`docs/evidence/sprint-1/integration.sh` (real server).

**Same caveat as Sprint 1: everything was verified in a sandbox with synthetic data; nothing ran against production.**
"Before" below means the code at the end of Sprint 1 (`981320d`) running today's tests.

## Results at a glance

| Check | Result |
|---|---|
| Backend suite (`python -m pytest`) | **1240 passed, 1 skipped** (the skip: `test_postgres_compat.py`, no `pgserver` here). +110 tests this sprint |
| Frontend (`npx tsc --noEmit -p tsconfig.json`, `npx vitest run`) | tsc exit 0; 24 files / 164 tests |
| Real server: email simulator, gate off | **61/61** on six consecutive runs (after the P0-3 fix below; it failed about 3 runs in 8 before) |
| Real server: gate on · MCP smoke · every GET | 35/35 · **92/92** · 95 endpoints clean |

## Per item

Each row: what was reproduced, what changed, the tests (`backend/tests/`), and the count of that item's tests that **fail on the
Sprint 1 code → pass now**.

| | Reproduced | Fix | Tests: fail before → pass now |
|---|---|---|---|
| **C1** | `render_text_preview` ignored `contact_id` (the endpoint did not even have the parameter) and rendered through its own sample-value path, not the registry-aware one senders use | `POST /templates/preview` takes `contact_id`; one renderer, `variable_service.render_preview`, behind the Templates page, `render_text_preview` and `preview_template` (which now passes the contact by id). The tool lost the hidden `first_name=John…` defaults that would have beaten the contact's values | `test_sprint2_templates.py` (C1+C2+C8 templates): 22 of 25 → 25 |
| **C2** | `{{frist_name}}` was deleted without a word at save, preview and send | Save is a 422 (`UNKNOWN_TEMPLATE_VARIABLES`, with "did you mean"); a preview shows `[UNKNOWN VARIABLE: x]` and lists it; campaign `validate` errors. A column imported since the registry's last scan counts as known | (same file) |
| **C3** | `reply_to_conversation` always 422'd "Message body or template is required": the endpoint read `body` from the query, the tool sent JSON. It also texted the contact for an *email* thread, and attached an SMS to whichever conversation the contact had first | Either form accepted; routed by the conversation's channel; SMS reply/direct send/scheduled send/inbound text use the contact's **text** thread | `test_sprint2_reply.py`: 8 of 9 → 9 |
| **C4** | `list_followups` had no parameters, so the endpoint's `due_today` + `sms` defaults applied (a follow-up for next week, or an email one, never showed); no DELETE | `view=pending`; tool defaults to pending + both channels (the page's defaults unchanged); `page/next_page`; 422 on a bad `view`/`channel`; `DELETE /followups/{id}` + `delete_followup` | `test_sprint2_followups.py`: 10 of 11 → 11 |
| **C5** | Emailing a typed address, scheduling an email to one, and inbound mail from a stranger created a contact with an invented `phone_number` (`email:bola@acme-lead`) and `country: Nigeria`; `update_contact` returned and kept it | `new_email_only_contact` (no phone, no country); startup repair + SQL clear old placeholders; the `country` default removed from the contact tools | `test_sprint2_contacts.py`: 5 of 6 → 6 |
| **C6** | One database, four answers (below) | `services/message_metrics` is the single definition; every response carries `scope` and `definitions` (`docs/METRICS.md`) | `test_sprint2_metrics.py`: 11 of 11 → 11 |
| **C7** | Tool parameters drifted from their endpoints | `test_sprint2_tool_contracts.py` walks every tool against the OpenAPI document (58+ tools, 200+ parameters). On its first run it flagged 9 things; fixed or declared intentional. The dispatcher now **applies** a declared default and refuses an out-of-enum value (`docs/TOOL-CONTRACTS.md`) | 5 of 7 → 7 |
| **C8** | A bogus template `channel` silently became `sms`; an email template needed no subject; three lists named "Leads" were all created (201); inbox filters answered 200 to `channel=whatsapp` | 422 / 422 / 409 (naming the existing list; also on rename and on an import that creates a list) / 422 | `test_sprint2_lists_and_filters.py`: 10 of 15 → 15, plus the template cases above |
| **C9** | `{"sent":0,"skipped":1}` and nothing else; an SMS list send that hit the rate limit dropped its unreached recipients from the response | Every send response: `skipped`, `skipped_reason`, `skipped_reasons`, `skipped_details`, `message` | `test_sprint2_skipped_reasons.py`: 6 of 6 → 6 |
| **C10** | Done in Sprint 1 (masking, reveal/rotate, constant-time compare, device id). HMAC-signing is **not done** (Brevo does not sign its webhooks) | — | — |
| **C11** | `PATCH /campaigns/{id}` was a 405; an invalid conversation mark was a 405 (and the tool forwarded it) | `PATCH` is the same operation as `PUT`; a 422 listing the valid marks, and the tool refuses it before calling the app. Missing campaign → 404 was P0-1 | `test_sprint2_http_semantics.py`: 10 of 16 → 16 |

### C6 in numbers (`repro_c6.txt`, one database)

Truth for SMS in the last 30 days: **sent 11, delivered 6, failed 1, replies 3** (12 / 7 all-time because of one old message).

| endpoint | before | after |
|---|---|---|
| `analytics/overview` ("SMS", 30 days) | sent **19**, delivered 9, failed **2**, replies **5** (email rows included) | 11 / 6 / 1 / 3 |
| `analytics/funnel`, `analytics/timeseries` (same page) | failed **3** (a cancelled message counted) | 1 |
| `dashboard/stats?channel=sms` | all-time 12 / delivered **6** | all-time 12 / delivered 7 (a delivery receipt counts) |
| `send/history` | **21** rows (email included) | 12 |
| `email/stats`, `email/overview`, dashboard email | 8 / 5 / 1 / 2 (but a different "delivered" and window from the dashboard's) | identical definitions and window |

## A bug in my own P0-3, found while verifying this sprint

Running the email simulator repeatedly against a real server, it failed about 3 runs in 8 ("campaign mail reached Brevo: 2 → 2"). I first
blamed the app's inline poller; **that was wrong**. The real cause: the app serves requests while its background boot task is still
running, and the one-time *safe sending defaults* step from P0-3 read "every stored throttle is at the old all-open values" as "nobody ever
configured this" — overriding the rules the operator had **just saved** (the simulator saves exactly those values on purpose). On a Sunday
the 09:00–17:00 / weekends-off defaults then held the campaign. My P0-3 text said an operator's own configuration is never touched; that was
not true for a save that landed during boot. Now every save records that a person decided (`record_operator_choice`), and the step re-checks
the marker right before it writes. Tests: a save before the step is never overridden; an untouched install still gets the safe defaults;
a no-op save is not a decision. After the fix: six consecutive 61/61 simulator runs.

## Differences from the brief, and decisions that are yours

* **C2:** a *real send* still removes an unknown variable rather than print `[UNKNOWN VARIABLE…]` to a customer. You can no longer reach that
  state through the API (save is a 422, `validate` fails the campaign), so the visible marker is where a person looks: previews, and the
  422. If you want sends to fail instead, that is a one-line policy change in the renderer.
* **C5:** I could not reproduce "`update_contact` invents `phone_number`" on contacts created through the API or CSV import (those have
  `phone_number: null`). I reproduced it on contacts created by `/email/send`, scheduled sends and inbound mail, which fabricated the
  number — that is what was fixed.
* **C7:** the brief asked for schemas *generated* from the endpoint models. They are still hand-written; a test now enforces the contract
  (`docs/TOOL-CONTRACTS.md`). The classic engine **stores but never reads** a campaign's `daily_limit`, `hourly_limit`, `min_delay` and
  `max_delay` (only Settings → Sending Rules apply). I removed the two the create tool offered; the API fields still exist (the UI may send
  them). Honouring or removing them is a product decision.
* **C6 is a behaviour change:** the analytics endpoints and `send/history` are SMS-only unless `channel` says otherwise; the funnel and
  charts no longer count a cancelled message as failed; email rates have one decimal like the rest; "last N days" starts at midnight UTC,
  N−1 days back.
* **C8:** campaign and Ads Manager creation already answered 422 to a bogus `channel`.

## Not verified

* The dashboard in a browser (the new Templates preview warnings are covered by `tsc` only; there is no UI test for them).
* How real assistants (Claude, ChatGPT, Arena) behave with the now-applied defaults; verified through this server's JSON-RPC (MCP smoke 92/92).
* PostgreSQL: the new queries are standard SQL (`COALESCE`, `SUM(CASE…)`, `LOWER(TRIM())`, `LIKE`) and run on SQLite here; no Postgres was available.
* The placeholder-phone repair against production data (it only touches rows whose phone starts `email:` and that have an address).
