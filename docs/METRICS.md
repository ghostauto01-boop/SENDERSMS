# One set of message numbers

`dashboard/stats`, `analytics/overview` (and `funnel`, `timeseries`, `by-campaign`), `email/stats`
and `email/overview` all count messages through one module, `services/message_metrics.py`. Before
this, one database produced several sets of "sent / delivered / failed", for four independent reasons:

| | before | now |
|---|---|---|
| **scope** | the "SMS" analytics had no channel filter (email rows were in it); the dashboard was all-time, analytics 30 days, email 29 | analytics, its charts and `send/history` are SMS unless you pass `channel`; every response says its `scope` |
| **window** | a rolling "now minus N days" in two places, "now minus N−1" in a third | one instant: midnight UTC, N−1 days back (today counts as one of the N) |
| **delivered** | the dashboard: `status = delivered`; email also: a delivery time, or an open | everywhere: status `delivered`, **or** a delivery time, **or** an open |
| **failed** | `overview`: status `failed`; `funnel` / `timeseries`: also `cancelled` | everywhere: status `failed`. A cancelled message (nobody tried to send it) is reported on its own as `cancelled` |

## Definitions (also returned in every response as `definitions`)

* **sent** — outgoing messages *created* in the scope, including ones still queued or that failed. It is the
  number of attempts, not the number that left; use `queued` and `failed` to split it.
* **delivered** — reached the recipient: status delivered, a delivery time, or an open.
* **failed** — status failed.
* **queued** — waiting to go (queued, sending, retrying).
* **replies** — incoming messages in the scope.

## Asking for a scope

`GET /dashboard/stats?channel=sms|email|all&days=N` (all time when `days` is omitted),
`GET /analytics/overview?channel=…&days=N`, `GET /email/stats?days=N`. For the same channel and `days`
they return the same numbers; the response's `scope` echoes what was counted. The MCP tools
`dashboard_stats`, `analytics_overview` and `send_history` take the same parameters.

Rates (`delivery_rate`, `reply_rate`, …) are percentages with one decimal everywhere (the email ones used two).
