# Campaign Scheduling + Auto-Reply

Two new features, both live on branch `arena/019fedaf-sendersms` (commit `b2cbbab`).

---

## 1. Campaign scheduling

**What you can now do:** pick a future date and time on a campaign, and it sends itself. No recurrence — one launch, as you asked.

### How to use it

1. Campaigns → create your campaign as usual (message, list, limits).
2. Click **Schedule** on the campaign card.
3. Pick the date and time. The picker uses *your* local time; the app converts to UTC behind the scenes.
4. The card then shows *"Scheduled for …"*. You can change the time or cancel it any time before it fires.

At the chosen moment the campaign flips to **running** on its own and starts sending. Nothing else for you to press.

### What makes it reliable

| Concern | How it's handled |
| --- | --- |
| Does it fire if the worker is asleep? | Two independent launchers poll for due campaigns — the Celery beat schedule (every 60s) *and* the API server's inline poller. Either one alone is enough. |
| Could it send twice? | No. A launcher must win an atomic `UPDATE … WHERE status='scheduled'` to claim the campaign. The loser sees `rowcount == 0` and walks away. |
| Can I schedule something in the past? | Rejected, with a readable message. |
| What if the campaign is invalid (no message body, etc.)? | Scheduling runs the same validation as the Validate button. A campaign that fails validation at launch time drops back to **draft** rather than blasting garbage. |
| Free Render instance sleeps after 15 min idle | ⚠️ **This still applies.** See "One thing to know" below. |

### ⚠️ One thing to know about timing on Render's free tier

Your API service sleeps after 15 minutes of no traffic, and the worker is the thing that actually fires the timer. If both are idle when a campaign comes due, the launch waits until something wakes them up. In practice a campaign scheduled for 09:00 might go out a few minutes late.

If exact-to-the-minute timing matters to you, the fix is a paid Starter web service ($7/mo) which never sleeps. Otherwise, treat the schedule as "around this time" and you'll be fine.

---

## 2. Auto-reply

**Every rule is yours to write.** There is no canned content and nothing AI-generated in the replies — the app only sends text you typed.

### How to use it

Go to the new **Auto-Reply** page in the sidebar. Each rule has:

- **Name** — for your reference only.
- **Keywords** — comma-separated, e.g. `price, pricing, how much`.
- **Match type**:
  - `contains` — the message includes any keyword (most useful)
  - `exact` — the whole message is the keyword (ignores case and stray spaces)
  - `starts` — the message begins with a keyword
  - `any` — catch-all, replies to *everything* that no higher-priority rule caught
- **Reply** — what gets sent. Supports the same variables as campaigns: `{{first_name}}`, `{{business_name}}`, and `{{first_name|there}}` for a fallback.
- **Priority** — **lower number wins.** Put your specific rules at 1, 2, 3 and a catch-all at 99.
- **Cooldown** — minutes before the same contact can get another auto-reply. Default 240 (4 hours), `0` turns it off.
- **Enabled** — toggle a rule off without deleting it.

There's a **Test** box on the page: type a message, and it tells you which rule would answer and shows the exact reply. Use it before you go live.

### Safety rails already built in

- **Never replies to someone who opted out.** A contact who sent STOP gets silence.
- **Never replies to a STOP message itself** — that would be a terrible look.
- **Cooldown is per contact**, so one chatty person can't trigger a loop, but a *different* contact still gets their reply immediately.
- **A keyword rule with no keywords never matches** (so a half-finished rule can't accidentally become a catch-all).
- Auto-replies are marked in the database and show up in the conversation thread, so you can always see what the system said on your behalf.

### Suggested starting setup

| Priority | Match | Keywords | Reply |
| --- | --- | --- | --- |
| 1 | contains | `price, pricing, how much, cost` | your pricing answer |
| 2 | contains | `hours, open, closed` | your opening hours |
| 3 | contains | `where, location, address` | your address |
| 99 | any | *(none)* | "Thanks {{first_name\|there}}, we got your message and will reply shortly." |

---

## Bug found and fixed along the way

**Timestamps were being sent to the browser without a timezone.** The database hands back a bare `2026-08-11T14:33:25` with no offset, and JavaScript reads a bare timestamp as *local* time. So a campaign scheduled for 14:33 UTC was being **drawn on screen as 14:33 WAT** — an hour off for you in Abuja. The campaign would have fired at the correct moment; you'd just have been told the wrong time. Now every timestamp goes out stamped `+00:00` and the display matches reality.

Also fixed: when the server rejected a bad schedule time, the error box showed `[object Object]` instead of the reason. FastAPI returns validation errors as a list of objects; the API client now flattens that into readable text everywhere in the app, not just here.

---

## Before you deploy

**Run the migration.** There is no Alembic in this project, so schema changes live in SQL:

```
scripts/migrate_existing_db.sql   # sections 5 and 6 are new
```

It's idempotent — safe to run more than once. It adds the `auto_reply_rules` table, `campaigns.scheduled_start_at`, and the `messages.is_auto_reply` flag. **Run it against the live Postgres before deploying this code**, or the app will error on startup looking for columns that aren't there.

---

## How this was verified

Not just unit tests — the whole thing ran against a live stack (real API server, real Celery worker with beat, a mock SMS gateway that signs webhooks exactly like SMS-Gate does).

- **238 automated tests pass** (was 210 before this task, 236 mid-task; +2 for the timezone regression).
- **`tsc` and the production build are clean.**
- **Scheduling/auto-reply simulation: ALL CHECKS PASSED** — campaign auto-launched ~30s after its scheduled time, both recipients got exactly one SMS each, no duplicates, the past-time rejection was readable, the higher-priority rule beat the catch-all, a real inbound SMS produced `"Hi Ada, our plans start at 5000 NGN/month."` at the gateway, the cooldown blocked the repeat, a second contact was *not* blocked, STOP produced no reply, disabling a rule stopped it matching, and deletion stuck.
- **The earlier duplicate/edit/delivery simulation still passes**, so nothing that worked before is broken.

The two timezone tests were mutation-verified: I reverted the fix and confirmed the test fails, then restored it.

## Still open (unchanged from before, minor)

- `poll_status_for_ids` substring-matches `state` — works, but fragile.
- `gateway_settings.password_encrypted` is written but never read.
- The Gateway tab has no credential form (credentials are env-only).
- Responsive layout has never been visually rendered — verified by class audit, not by eye.
- Upstash Redis password rotation is still recommended eventually. **Not touched, per your instruction.**
