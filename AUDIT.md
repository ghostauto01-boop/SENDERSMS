# SMS SENDER — App Audit

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
