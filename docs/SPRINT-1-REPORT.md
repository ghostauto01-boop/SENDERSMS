# Sprint 1 report (QA sweep of 10 Oct 2026, §G → §J)

Branch `arena/191f89ff-sendersms`, five commits on top of `45c0937`
(`d9002cf` P0-1, `b9eedb5` P0-2, `38d3b87` P0-3, `60c0f4f` P0-4, `abeb293` P0-5, `04f946b` C10 + P0-5 follow-up).
Evidence (raw before/after output) is in [`docs/evidence/sprint-1/`](evidence/sprint-1/).

**Everything below was verified in a sandbox with synthetic data. I had no production URL or credentials, so
nothing was run against production, and none of the brief's production numbers (1,297 contacts, 184 bounced,
525 pending, 45% failure rate…) were re-measured by me. Where my environment differs from the brief I say so.**

---

## 0. P0-0 — NOT EXECUTED. This one is yours.

> `POST /api/v1/ads/campaigns/2/pause` on **production**.

Campaign 2, "Trybe UGC Image Ads - Cold Outreach", is described as active and continuous, 525 pending, 45% failure
rate, resuming at 09:00 Africa/Lagos on the next weekday. `POST …/campaigns/2/resume` reverses the pause.

I cannot do this (no production access) and I did not. Until you do it, the code in this branch changes nothing about
campaign 2: it only takes effect after deploy. After deploy, the verification gate (P0-5) refuses unverified
addresses, and the brief says none of the 1,297 contacts is verified — so email outreach to that list would stop at
validation. That last sentence is an inference from the brief's numbers, not something I observed.

---

## Results at a glance

| Check | Command | Result |
|---|---|---|
| Backend suite | `python -m pytest` | **1130 passed, 1 skipped** (the skip: `test_postgres_compat.py`, no `pgserver` module here) |
| New backend tests | 7 files | 231 (P0-1 46 · P0-2 23 · P0-3 59 · P0-4 34 · P0-5 50 · C10 19) |
| Frontend | `npx tsc --noEmit -p tsconfig.json` · `npx vitest run` | tsc exit 0 · **24 files / 164 tests passed** |
| Email-channel simulator (real server + fake Brevo) | `python tools/simulate_email_flow.py` | gate off: **61/61 PASS**; gate on: **35/35 PASS** (stops at the gate by design) |
| MCP smoke (real JSON-RPC) | `python tools/mcp_smoke.py` | **92/92** |
| Every parameterless GET | `python tools/smoke_all_endpoints.py` | 95 endpoints, all clean |

Each fix has a test that **failed on the pre-change code and passes now** (the failing runs are in the evidence folder).

---

## P0-1 — campaign lifecycle (commit `d9002cf`)

* **Changed:** `backend/app/services/campaign_service.py`, `api/v1/campaigns.py`, `models/campaign.py`, `schemas/campaign.py`,
  `tasks/campaign_tasks.py`, `mcp/registry.py`, `frontend/src/pages/CampaignsPage.tsx`, `scripts/migrate_existing_db.sql`;
  tests `backend/tests/test_p0_1_campaign_lifecycle.py` (46).
* **Verify:** `python -m pytest backend/tests/test_p0_1_campaign_lifecycle.py`
* **Before → after:** pre-fix code **27 of 46 failed** (`p0_1_before.log`); now 46 pass (`p0_1_after.log`).
  Reproduced: a draft with a past `scheduled_start_at` was launched by the scheduler right after a `validate`.
  Now `validate` is a report (`changed: false`, any number of calls leaves the status alone); `scheduled` has exits —
  pause (records `paused_from`, resume restores `scheduled`), delete when `messages_sent == 0`, stop, edit —
  `schedule(null)` returns it to `draft`; a missing id is 404 (was 400); `TRANSITIONS` is a tested state machine in which
  every status has at least one outgoing transition **except `completed`/`stopped`, which are terminal by design**.
* **Differs from the brief:** `POST /schedule` already existed, and `scheduled` could already be stopped, started and
  edited — pause and delete were the missing exits.
* **Not verified:** the UI in a browser (only vitest), production data.

## P0-2 — one campaign directory (commit `b9eedb5`)

* **Changed:** `api/v1/overview.py`, `api/v1/dashboard.py`, `mcp/registry.py`, `mcp/server.py`, `main.py`, schemas;
  tests `test_p0_2_unified_campaigns.py` (23).
* **Verify:** `python -m pytest backend/tests/test_p0_2_unified_campaigns.py`
* **Before → after:** dashboard `active_campaigns` **1 → 3** on a fixture with overlapping ids; `get_campaign(2, kind=ads)`
  returned the classic "Legacy email draft" (now the ads campaign); MCP `pause_campaign(1)` guessed a system (now an id in
  both systems is a 409 `AMBIGUOUS_CAMPAIGN_ID` listing the candidates). Test: every id surfaced by `list_campaigns`,
  the dashboard and activity resolves through `get_campaign`.
* **Differs from the brief:** `kind` is `campaign | ads` (the classic payloads already say "campaign"); `system` carries
  `legacy | ads`, and `kind=legacy` is accepted as an input alias. The MCP registry has no ads tools; the generic
  `api_request` tool reaches them.
* **Not verified:** the real campaign ids 1/2/3 (my overlap is a constructed fixture).

## P0-3 — throttles on by default, bounce circuit breaker (commit `38d3b87`)

* **Changed:** `services/sending_limits.py` (rewritten), `services/circuit_breaker.py` (new), `services/campaign_service.py`,
  `services/ads_service.py`, `tasks/campaign_tasks.py`, `tasks/sms_tasks.py`, `api/v1/settings.py`, `config.py`, UI
  `SettingsPage.tsx`/`CampaignsPage.tsx`/`ads/CampaignDetail.tsx`; docs `docs/SENDING-RULES.md`;
  tests `test_p0_3_send_throttles.py` (37) + `test_p0_3_circuit_breaker.py` (22).
* **Verify:** `python -m pytest backend/tests/test_p0_3_send_throttles.py backend/tests/test_p0_3_circuit_breaker.py`
* **Before → after** (`p0_3_before.log` / `p0_3_after.log`): Saturday 23:30 Lagos, 200-contact email campaign, one mailbox —
  **200 of 200** messages created and handed to the provider → **0** created (200 stay pending). Wednesday noon: **30**
  created (cap = 30 per mailbox × 1 mailbox) and still 30 on a second pass, 170 pending. (The log's "handed to the provider: 1"
  is the existing 30-second pacing in a harness that does not wait.) Defaults: daily cap on, 09:00–17:00 Africa/Lagos,
  weekends off. `validate` reports a projection (sendable, daily cap, sending days, finish date). The breaker pauses an email
  campaign above 2% bounces/refusals or 0.10% complaints (rolling 7 days, ≥20 sends), records why in `paused_reason`;
  a synthetic 5% bounce run is the test. Resume/start/launch need `acknowledge_breaker=true`.
* **Also fixed:** an email campaign carrying its own message and no template raised `UnboundLocalError` on its first contact, so it
  could never send. Fixed together with the throttles on purpose — fixing it alone would have released an unthrottled send.
* **Not verified:** real Brevo bounce/complaint webhooks; behaviour over a real week; the Celery beat sweep (the inline sweep is tested).

## P0-4 — nothing silently truncated (commit `60c0f4f`)

* **Changed:** `mcp/response.py` (new), `mcp/server.py`, `mcp/registry.py`, `api/v1/contacts.py` (new `GET /contacts/export`),
  `api/v1/inbox.py`, `api/v1/webhooks.py`, `api/v1/mcp.py`, `schemas/contact.py`, `tools/mcp_smoke.py`; docs `docs/MCP-RESPONSES.md`;
  tests `test_p0_4_no_silent_truncation.py` (34).
* **Verify:** `python -m pytest backend/tests/test_p0_4_no_silent_truncation.py`
* **Before → after** (5,000-contact fixture; `p0_4_before.log`, `p0_4_after.log`, `p0_4_tests_before.log`): `export_contacts_csv` returned
  **4,000 characters = 26 complete rows** with no flag; `search_contacts per_page=100` returned 6,034 characters of **unparseable JSON**.
  Now every tool result is one valid JSON envelope (`truncated`, `returned`, `total`, `next_cursor`, `cut`, `data`); the 1,297-contact export
  yields **1,297 rows over 10 pages** (default 20 KB page budget), 5,000 → 5,000 over 39 pages, every page valid JSON, last page
  `truncated:false`; `per_page=101` is a **422**. 25 of the 34 tests fail on the old code.
* **Not verified:** how real MCP clients (Claude/ChatGPT/Arena) follow `next_cursor` — only through this server's JSON-RPC (`mcp_smoke` 92/92).

## P0-5 — unknown is not a yes (commits `abeb293`, `04f946b`)

* **Changed:** `models/contact.py` (+`email_verdict`), `services/email_service.py` (one gate for screening and sending),
  `services/email_validator.py`, `services/verification_jobs.py` + `models/verification_job.py` (new), `api/v1/validator.py`,
  `services/campaign_service.py` (set-based population, risky last), `services/ads_service.py`, `tasks/*`, `config.py`
  (`EMAIL_REQUIRE_VERIFIED`), `mcp/registry.py` (+3 tools), `frontend/src/pages/ValidatorPage.tsx`; docs `docs/EMAIL-VERIFICATION.md`;
  tests `test_p0_5_verification_gate.py` (50).
* **Verify:** `python -m pytest backend/tests/test_p0_5_verification_gate.py`; repro `p0_5_before.log` / `p0_5_after.log`.
* **Before → after** (5 contacts, 3 verified + 2 never checked, email campaign): `validate` **valid, 5 of 5 sendable** → **invalid, "2 of 5
  contacts have an unknown email verification status"**; `start` **200, 5 messages created, 2 to unverified addresses** → **400, 0 messages**;
  validator `batch_size` 5 → 25 (`/batch` with 6 rows: 422 → 200); `/validator/status`: `smtp_enabled: true, reason: none` →
  `smtp_enabled: false` with a reason.
* **What it does:** `unknown` (never checked, or the verifier could not decide) is not sendable by campaigns, the Ads Manager, follow-ups or list
  sends; `risky` (catch-all/role/disposable) is sent last; one-to-one mail and replies are not gated. Verdicts persist to `email_verified`,
  `email_verified_at`, `email_confidence` and a new `email_verdict`; an `unknown` result never undoes an earlier proof. Verification is a durable,
  resumable job (`POST /validator/jobs`), not a browser loop.
* **Found by verifying, not by reading:** (1) the port-25 probe first reported SMTP as working, because in this sandbox the TCP connection to a
  mail server is *accepted and then closed with no banner*; the probe now requires the `220` greeting (test included). (2) The simulator showed a
  one-to-one send with a marketing template (unsubscribe headers) was being refused as "bulk"; the gate now keys on who chose the recipient.
* **Differs from the brief:** the validator already had a Reacher client, an MX check, catch-all → `risky` and an SMTP probe. The real gaps were
  that unknown stayed sendable, nothing was persisted unless a caller passed `save=true`, and nothing said why mailboxes could not be confirmed.
* **Not verified — important:** **no real verifier was exercised.** This environment has no `reacher_*` configuration, and DNS/SMTP are mocked in
  every test. Whether your Reacher service returns what the client expects, and how long 1,297 real checks take, is untested. The job runner is tested
  with stubbed verdicts and a simulated mid-run interruption.
* **Operational consequence:** imported contacts are `unknown` until verified, so **with no Reacher service and no open port 25, nothing becomes
  sendable.** That is the intended behaviour, but it means a verifier must be configured before the first campaign.

## C10 — secrets out of reads, token rotation (commit `04f946b`)

* **Changed:** `services/email_service.py`, `api/v1/email.py` (reveal/rotate), `api/v1/webhooks.py`, `api/v1/inbox.py`, `mcp/server.py`
  (`X-Sendsms-Via: mcp`), `services/setup_guide.py`, UI `EmailManagerPage.tsx`, `SetupPage.tsx`, `api/email.ts`, `api/guide.ts`; docs `docs/WEBHOOK-SECURITY.md`;
  tests `test_c10_secrets.py` (19) + one `SetupPage` UI test.
* **Verify:** `python -m pytest backend/tests/test_c10_secrets.py backend/tests/test_setup_guide.py`; `npx vitest run frontend/src/pages/SetupPage.test.tsx`
* **Before → after** (`c10_before.log` / `c10_after.log`): `GET /email/accounts` contained the **full webhook token: True → False** (now `token=****a1b2`).
  13 of 19 tests fail on the old code; the new UI test fails on the old page. Rotation replaces the token and the old one gets a 403 immediately; reveal and
  rotate are refused to the MCP bridge, including through the generic `api_request` tool.
* **Not done:** HMAC-signing of webhooks (Brevo does not sign its transactional webhooks, so there is nothing to verify; the SMS gateway webhook already has a
  signature check). The device id is masked in the two diagnostic endpoints that returned it; other secrets-in-reads were not audited. Because Brevo can only be
  given a URL, the token still appears in web-server access logs for Brevo's calls (the header form exists for senders that can set one). How Brevo reacts to
  deliveries refused after a rotation (retry or drop) was not verified.

---

## Behaviour changes you will notice

1. `POST /campaigns/{id}/validate` no longer arms the campaign; use `/start` or `/schedule`. Missing campaign → 404, state conflict → 409.
2. Sending rules are on by default (09:00–17:00 Lagos, weekends off, daily cap); a campaign started out of hours waits, and the `start` response says so.
3. A bounce rate above 2% (or complaints above 0.10%) pauses an email campaign; resuming needs `acknowledge_breaker=true`.
4. MCP tool text is now a JSON envelope (the endpoint payload is `data`); list sizes above the documented maximum are 422, not silently clamped.
5. Contacts must be verified before campaigns send to them (see P0-5).
6. `webhook_url`/`webhook_path` in account reads are masked; call `…/webhook/reveal` for the real URL.

## Extra findings

* **Live SMS-Gate credentials are printed in plaintext in `AUDIT.md`, `START_HERE.md` and `backend/debug_poll.py`, and are in git history** (`AUDIT.md` cites commit
  `74c9cc2`). Rotate them. I did not copy them anywhere and did not edit those files: scrubbing does not remove history, and `debug_poll.py` is a working script.
* `UnboundLocalError` in `_send_template_message` (fixed, P0-3).
* `GET /mcp/activity` silently clamped `limit` with `min(limit, 200)`; it is now a 422 like the other lists.
* `tools/mcp_smoke.py` defaults `ADMIN_PASSWORD` to `admin`, while the app's shipped default is `12345678`; I passed the variable and did not change the default.
* The simulator's helper scripts must share the server's `CREDENTIAL_ENCRYPTION_KEY` (a shared `.env`); with mismatched keys four steps fail with `api_key_unreadable`.
  I proved that was the cause by re-running with matching keys (61/61).

## Not done

* **P0-6** (Sprint 3) and **C1–C9** (Sprint 2) were not started. **C11:** only the missing-campaign 404 is done (as part of P0-1); PATCH 405 and the invalid-mark 422 are not.
* **§E data work** (tagging `pattern_generated`, consent model, RFC 8058 headers, …): none; no data was touched.
* Production: P0-0, the deploy, and lifting the "freeze outbound sends" are your calls. New schema is created at startup (`contacts.email_verdict` by
  `schema_repair`, `email_verification_jobs` by `create_all`) and also listed in `scripts/migrate_existing_db.sql`.
