# SMS SENDER — App Audit

> **STATUS: ALL FINDINGS FIXED** — see commit `649ce93`. Each item below is
> annotated with how it was resolved and how the fix was verified against a
> running server. One action remains for you: **rotate the SMS-Gate credentials**
> (see finding 3) — that cannot be done from inside this environment.

I installed both stacks, booted the FastAPI backend, built the React frontend, logged in, and
exercised every endpoint the UI calls. Below is what I found, ordered by severity.

**Baseline that DOES work:** login/session/logout, contacts CRUD + CSV import + bulk actions,
lists CRUD + membership, templates CRUD + preview + duplicate, sequences create, dashboard/analytics
stats, inbox listing, direct send (fails gracefully when the phone is offline), SPA + PWA serving.
`tsc -b` compiles clean, `vite build` succeeds, all 60 backend tests pass.

---

## 1. BLOCKER — Campaigns can never be launched

A campaign can never leave `draft`. The UI flow is **draft → Validate → Start**, but validation
always fails:

```
POST /api/v1/campaigns/3/validate
{"detail":"No SMS gateway selected"}   HTTP 400
```

Tested with a fully-populated campaign (list with 3 contacts + template attached). The blocker is
in `backend/app/services/campaign_service.py:80`:

```python
if not campaign.gateway_setting_id:
    errors.append("No SMS gateway selected")
```

**Why it can never pass:** the `gateway_settings` table is never written to. Searching the whole
codebase, `GatewaySetting(...)` is never instantiated anywhere — the model is declared and never
used. There is also no gateway picker in the create-campaign form
(`CreateCampaignModal` only collects name, description, list, template, sequence), and
`gateway_setting_id` appears nowhere in the frontend except as a type field.

So: no row can exist, no UI can set it, and validation hard-requires it. The entire Campaigns
feature is dead-ended. Note this is inconsistent with the rest of the app, which sends via
credentials read straight from `settings` (`send_sms_direct`) and ignores `gateway_settings`
entirely.

**Fix options:** (a) drop the `gateway_setting_id` check and validate against the configured
SMSGATE credentials like the send path does, or (b) seed/expose a real GatewaySetting row and add
a selector to the campaign form. Option (a) matches how the app actually sends today.

## 2. BLOCKER — Starting a campaign 500s when Redis is down

`POST /campaigns/{id}/start` returns **HTTP 500 Internal Server Error**:

```
kombu.exceptions.OperationalError: Error 111 connecting to localhost:6379. Connection refused.
```

`campaigns.py:125` calls `process_campaign.delay(campaign_id)` with no try/except, so an unreachable
broker becomes an unhandled 500 and the user gets a raw error. Worse, `start_campaign()` has already
flipped status to `running` and committed before the enqueue throws — the campaign is left
**permanently stuck in `running` with nothing processing it**, and the UI then only offers
Pause/Stop.

Same unguarded `.delay()` pattern to check elsewhere in the API layer.

**Fix:** wrap the enqueue in try/except; on broker failure roll the status back (or mark `failed`)
and return a clear 503 instead of a 500.

## 3. SECURITY — Live gateway credentials committed to the repo

`backend/app/config.py:44-45` hardcodes real production SMS-Gate.app credentials as defaults:

```python
SMSGATE_USERNAME: Optional[str] = "_O48UB"
SMSGATE_PASSWORD: Optional[str] = "nw_e7wyhwjwubp"
```

These are in git history (commit `74c9cc2`) and readable by anyone with repo access — they can send
SMS on your account. `.env.example` correctly leaves these blank, so the defaults are the problem.

**Fix:** rotate these credentials now, then default both to `None` and load only from env.

## 4. SECURITY — Webhook endpoint is unauthenticated

`POST /api/v1/webhooks/smsgateway` accepts anything, with no signature check. I injected a fake
inbound SMS from an unauthenticated request and it landed in the inbox as a real conversation:

```
curl -X POST .../webhooks/smsgateway -d '{"event":"sms:received","payload":{"message":"INJECTED",...}}'
→ {"ok":true}
→ inbox now shows conversation "INJECTED" from +2348000000001, contact auto-created, lead_status "replied"
```

`SMSGATE_WEBHOOK_SECRET` exists in config and `validate_webhook_signature` exists on the provider —
but it's `lambda self,*a,**kw: True` (always passes) and the webhook route never calls it. Anyone
who finds the URL can forge replies, create contacts, and corrupt lead data.

**Fix:** verify the SMS-Gate signature header against `SMSGATE_WEBHOOK_SECRET` and reject mismatches.

## 5. SECURITY — Login rate limiting is configured but not wired up

`RATE_LIMIT_LOGIN = "5/minute"` is defined and `slowapi` is in `requirements.txt`, but neither is
ever imported or applied. 8 rapid bad logins all returned plain 401 with no throttling:

```
attempt 1..8: 401 401 401 401 401 401 401 401
```

The login endpoint is an unthrottled brute-force target. **Fix:** install the slowapi limiter on the
app and decorate `/auth/login`.

## 6. BUG — Runtime state stored in local files, lost on every deploy

The SIM selection, poll timestamp, and webhook-registered flag are written to files next to the
source (`.sim_number`, `.last_poll`, `.webhook_done`) rather than the database:

- `backend/app/api/v1/settings.py:18` / `send.py:20` — SIM number
- `backend/app/main.py:13-14` — poll cursor and webhook flag

On Render/Docker the filesystem is ephemeral, so the user's SIM choice silently resets to 1 on every
restart, and the webhook re-registers. With more than one instance, each has its own copy, so
settings appear to randomly flip. `.sim_number` is even committed to git.

**Fix:** persist these in the database (a settings/system table).

## 7. BUG — Production webhook URL is hardcoded

`main.py:20` and `settings.py:42` both hardcode:

```python
register_webhook_direct("https://sendsms-api.onrender.com/api/v1/webhooks/smsgateway")
```

Any other deployment (staging, custom domain, local) registers its gateway webhook to point at that
one Render host, so inbound SMS goes to the wrong server. **Fix:** derive from a
`PUBLIC_BASE_URL` env var.

## 8. Lower priority

- **Insecure fallback secrets.** `SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` default to
  `"change-me-..."`, and `ADMIN_PASSWORD` defaults to `"admin"`. If an env var is missed in
  production the app boots anyway with a guessable admin and forgeable JWTs. Better to fail fast
  when `APP_ENV=production` and the defaults are still in place.
- **`allow_weekends` typed wrong.** `campaign.py:39` declares a boolean column as
  `Mapped[bool] = mapped_column(Integer, ...)`.
- **`GET /api/v1/health` triggers work.** The health check kicks off `_poll()` as a background task
  (status polling + scheduled sends). Render pings this constantly, so uptime checks drive real
  sending work; it also means polling stops whenever health checks stop.
- **Bare `except:` everywhere** in `main.py`, `send.py`, `smsgate.py` swallows errors silently and
  makes failures hard to diagnose.
- **Bundle size** — single 741 kB JS chunk (211 kB gzipped); worth code-splitting.
- **Unused deps.** `fastapi-cors`, `csvkit`, `passlib`, `aiohttp` are installed but never imported.

---

## How to reproduce

```bash
pip install -r requirements.txt && npm install && npm run build
cd backend && uvicorn app.main:app --port 8000
# login, then:
curl -X POST localhost:8000/api/v1/campaigns/1/validate   # → 400 "No SMS gateway selected"
```

Note: I verified against SQLite locally since Postgres/Redis aren't available in this sandbox
(`create_async_engine` in `database.py` passes `pool_size`/`max_overflow`, which SQLite rejects —
that only affects local SQLite runs, not your Postgres deployment). Findings 1, 3, 4, 5, 6, 7 are
static/logic issues that are DB-independent; finding 2 reproduces whenever Redis is unreachable.


---

# Resolution summary

| # | Finding | Status | Verified by |
|---|---|---|---|
| 1 | Campaigns can never be launched | **Fixed** | `POST /campaigns/1/validate` → `200 {"status":"scheduled"}` |
| 2 | Redis outage → 500, campaign stuck `running` | **Fixed** | Returns `503` in 0.68s (was 19s); status rolls back to `scheduled` |
| 3 | Hardcoded gateway credentials | **Fixed in code — rotation still required** | Defaults removed; env-only |
| 4 | Unauthenticated webhook / inbox injection | **Fixed** | Forged payload → `401`; signed → `200`; stale timestamp → `401` |
| 5 | Rate limiting configured but absent | **Fixed** | 6th login attempt → `429` |
| 6 | State in files lost on redeploy | **Fixed** | SIM/last-poll/webhook flag now in DB |
| 7 | Hardcoded onrender.com webhook URL | **Fixed** | `PUBLIC_BASE_URL`, warns when unset |
| 8 | Lower-priority cleanup | **Fixed** | prod guard aborts on weak secrets; bundle 741 kB → 246 kB; 14 bare `except:` removed; 4 unused deps dropped |

**Tests: 83 passing** (up from 60). The 23 new tests pin the two blockers and the
webhook signature logic, which previously had no coverage at all.

## The one thing you still have to do

The credentials `SMSGATE_USERNAME=_O48UB` / `SMSGATE_PASSWORD=nw_e7wyhwjwubp` are
in commit `74c9cc2` in git history. Removing them from the code does **not**
remove them from history. Change the username/password on the SMS-Gate device,
then set the new values as environment variables (`SMSGATE_USERNAME`,
`SMSGATE_PASSWORD`) on your host rather than in the repo.

While you are there, also set `SMSGATE_WEBHOOK_SECRET` (device →
Settings → Webhooks → Signing Key) and `PUBLIC_BASE_URL`, or inbound replies
will not arrive.

---

# Final checkup — full-workflow simulation (11 Aug 2026)

The earlier audit reviewed the code. This round *ran* the app: a mock SMS-Gate
device, a mock Pushover endpoint, Redis and a real Celery worker, driven through
the same HTTP API the UI uses — create contacts, build a list, write a template,
launch a campaign, receive replies, opt out, pause/resume/stop.

Six defects surfaced. None were visible to unit tests, because each needs two
processes, or a real template and contact, to reproduce.

| # | Defect | Impact | Status |
|---|--------|--------|--------|
| 1 | Three different placeholder substitution implementations | `{{first_name}}` shipped raw to real phones on some paths | **Fixed** — one shared renderer |
| 2 | Campaign sends left empty inbox threads | Contact appeared in chat with no preview, unsortable | **Fixed** |
| 3 | `messages_delivered` / `messages_failed` / `replies` never written | Every campaign reported 0% forever | **Fixed** |
| 4 | Nothing prevented duplicate conversations | Second thread wedged that contact permanently (`MultipleResultsFound`) | **Fixed** |
| 5 | **Campaign start race — campaigns sent nothing at all** | Task dispatched before the transaction committed; worker saw no contacts and exited "successfully" | **Fixed** |
| 6 | Searching by phone number matched nothing | Stored `+234…`, users type `0803…` | **Fixed** |

Defect 5 is the one worth reading twice. `POST /campaigns/{id}/start` returned
`200`, the campaign showed **running**, the worker logged **succeeded** — and not
one message was sent. Every surface reported success. It reproduced on the first
clean run and disappeared on a retry, which is exactly what makes this class of
bug so hard to catch in production.

**Tests: 133 → 178.** The race test deliberately uses a file-backed sqlite
database; an in-memory one shares a single connection and would happily pass a
broken commit order. It was verified to fail when the fix is reverted.

## Contact names in Pushover notifications

Notification titles now show the same name the chat shows, falling back to the
number when there is no contact:

| Contact record | Notification title |
|---|---|
| Ada Obi | `📱 New SMS from Ada Obi` |
| business name only | `📱 New SMS from Zenith Motors` |
| first name only | `📱 New SMS from Chidi` |
| no contact | `📱 New SMS from +2349099998888` |

One shared helper (`utils/naming.py`) backs the inbox list, the notification and
follow-ups, so these can no longer drift apart.

## Still on you

Besides rotating the exposed credentials above:

- **Existing databases need a manual migration.** `init_db` uses `create_all`,
  so the new unique constraint only applies to freshly created tables. Deduplicate
  `conversations` by `contact_id`, then add the unique index. Same for the
  `allow_weekends` column (`ALTER TABLE`).
- Configure and enable Pushover in Settings — the keys come from the database,
  not environment variables.
- Send one real SMS from a named contact as a final confirmation.
