# Sending rules, mailbox caps and the bounce watchdog

Outbound throttles are **on by default**. They exist because a mailbox (like a SIM)
has a reputation, and a burst of cold mail from a new mailbox destroys it faster
than anything can repair it.

## The defaults

| Rule | Default | Where to change it |
|---|---|---|
| Daily limit | on (SMS: 1000/day; email: see below) | Settings → Sending Rules, or `PUT /api/v1/settings/sending-rules?dl=` |
| Sending window | 09:00–17:00 in `DEFAULT_TIMEZONE` (Africa/Lagos) | `ss`, `se` (clear both to remove the window) |
| Weekends | off | `aw=true` |
| Pacing | 30 s between messages | `pc`, `md` |
| Emails per mailbox per day | 30 (`EMAIL_DEFAULT_DAILY_LIMIT`) | `email_daily_per_mailbox` (1–500; 20–50 is the usual guidance) |
| Bounce watchdog | on: **notify** above 2% bounces or 0.10% complaints, after at least 20 sends, over 7 days (one alert per campaign per 6 hours) | `bounce_alert_enabled`, `bounce_alert_bounce_pct`, `bounce_alert_complaint_pct`, `bounce_alert_min_sample`, `bounce_alert_window_hours`, `bounce_alert_cooldown_hours` |

Every rule can still be switched off. When a send is held, the reason is always
spelled out ("outside sending hours", "sending paused on weekends", "daily limit
(30) reached") together with the time sending resumes.

## The email cap follows the mailboxes

The daily cap for email is **the per-mailbox default × the number of usable
mailboxes** (active, with an API key and a From address). An account's own
`daily_limit` can only *lower* its share: Brevo's quota says what the provider will
accept, not what a mailbox with no reputation can safely send. The daily-limit
switch controls the per-mailbox default too, so there is one switch for "daily
caps", not two.

Email and SMS volume are counted separately, so one never eats the other's allowance.

The cap applies to **outreach** (campaigns, Ads Manager, follow-ups). A one-to-one
reply to someone who wrote to you is never held by the window or the cap.

## How a cap is enforced

1. **Producers stop at the cap.** The classic campaign engine and the Ads Manager ask
   the gate how many messages may go out *now* (`SendingGate.room()`, which subtracts
   mail already queued) and create only that many. A 925-contact audience on one
   mailbox is queued 30 at a time, not all at once.
2. **Every message is checked again at send time.** Past the cap or the window it is
   *deferred* (stays queued, retry budget untouched), never failed.
3. **Paused campaigns send nothing.** Mail created before a pause is held, not
   dropped; resuming releases it. Stopping a campaign cancels its queued mail.

## `validate` tells you how long it will take

`POST /api/v1/campaigns/{id}/validate` (and the Ads Manager's `…/validate`) report a
`projection`: how many contacts are actually sendable, the daily cap and where it
comes from, the sending days needed, calendar days and the projected finish date.
A 925-contact email audience on one mailbox at 30/day is **31 sending days**.

## The bounce watchdog (report only — it never stops a campaign)

For every email campaign, in both campaign systems, the watchdog looks at the same
rolling window and **notifies you** when

* more than `bounce_alert_bounce_pct` of recent sends bounced or were refused by the
  provider, or
* spam complaints exceed `bounce_alert_complaint_pct`.

A minimum sample (`bounce_alert_min_sample`, default 20) stops one bounce in five
sends from reading as "20%". It checks as each bounce webhook arrives, after a
provider refusal, and every five minutes as a backstop.

The alert is an ordinary in-app notification **and** a browser push
(`⚠️ High bounce rate: <campaign>` — see `NOTIFICATIONS.md`), so it reaches the
person who can fix the list instead of waiting for them to open the campaign page.
One alert per campaign per `bounce_alert_cooldown_hours` (6 by default): a bad list
bounces on every send, and a notification per bounce would bury everything else.

**Nothing is paused, stopped or held.** A campaign with a high bounce rate keeps
sending until a person decides otherwise; `/resume`, `/start` and the Ads Manager's
`/launch` are never refused because of it, and `paused_reason` is only ever what an
operator wrote.

> This replaced the *bounce circuit breaker*, which paused an email campaign above
> the same thresholds and demanded `acknowledge_breaker=true` to resume it. A
> campaign that stops itself halfway down its list is worse than one that keeps
> going with a dozen bad addresses — every good contact queued behind them simply
> never hears from you. The old `breaker_*` settings are still honoured, so an
> install that tuned them keeps its thresholds.

## Existing deployments: the one-time safe-defaults step

Changing a default only helps installs that never saved the Sending Rules page; once
saved, every throttle is stored explicitly (as *off*). So at startup the app runs a
**one-time** step:

* every stored throttle exactly equals the old all-open values → nobody ever tuned
  them → the safe values are written (daily limit on, 09:00–17:00, weekends off);
* any throttle set to anything else → an operator made a choice → **left alone**;
* either way a marker (`safe_defaults_applied`) records the decision, so an operator
  who later switches a rule off is never overridden again.

The outcome is logged at startup (`Sending rules safe-defaults step: …`).
