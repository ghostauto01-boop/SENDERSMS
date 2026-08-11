# SMS-Gate integration — how sending and receiving actually work

This document explains the rebuilt receive path: how an SMS that arrives on the
phone ends up in the chat, how outbound replies are tracked, and what you must
configure for it to work. Everything here is taken from the official docs at
<https://docs.sms-gate.app/features/webhooks/> and
<https://docs.sms-gate.app/features/reading-messages/>.

---

## 1. Why messages were not appearing in the chat

Five independent bugs each broke the path on their own. All are fixed and each
one now has a regression test in `backend/tests/test_inbound_receive.py`.

### 1.1 Webhook registration used the wrong request shape (the main cause)

The app registered a single webhook with an array of events:

```jsonc
{ "url": "https://…/api/v1/webhooks/smsgateway", "events": ["sms:received", …] }  // WRONG
```

The SMS-Gate API accepts **one event per webhook**, under a **singular `event`
key**. It does not reject the array — it silently records a webhook with an
empty event, which never fires. The phone was therefore never told to deliver
anything, and the inbox stayed empty no matter how many SMS arrived.

The app now registers one webhook per event and reconciles on every sync:

```jsonc
{ "url": "https://…/api/v1/webhooks/smsgateway", "event": "sms:received" }  // correct
```

### 1.2 Inbound senders were run through the Nigerian-number normaliser

Any sender that was not a valid Nigerian MSISDN was discarded. That silently
dropped bank/telco **short codes** (`32122`), **alphanumeric sender IDs**
(`MTN`, `GTBank`) and **foreign numbers** — exactly the messages users notice
are missing. A dedicated `normalize_inbound_sender()` now preserves all three.

### 1.3 Repeat replies were suppressed as duplicates

SMS-Gate derives an incoming message's `messageId` from its **content**, and the
docs state it is *not guaranteed unique*. So a second `YES` or `STOP` from the
same contact reused the previous id and was thrown away as a duplicate. The
idempotency key is now scoped by sender **and** `receivedAt`, while true
redelivery is caught by the top-level envelope `id` — which *is* unique.

### 1.4 A timezone crash aborted every message from a known contact

Comparing the incoming aware timestamp against the naive one stored in the
database raised `TypeError: can't compare offset-naive and offset-aware
datetimes`, aborting the handler. First messages worked; every follow-up in an
existing thread was lost. Only reproducible against a real database round-trip,
never in-memory — which is why it survived the earlier test suite.

### 1.5 Out-of-order replay clobbered the thread preview

`last_message_at` was guarded against going backwards but
`last_message_preview` was assigned unconditionally, so one back-dated message
from an inbox export made the conversation list show stale text. Both fields now
move together.

**Also fixed along the way:** `unread_count` never initialised on new
conversations; inbound rows stamped `created_at=now()` instead of `receivedAt`
(wrong chat ordering); `provider_message_id` never persisted (delivery receipts
could never match); a late `sms:sent` knocking a delivered message backwards;
per-part `sms:delivered` events bumping `delivered_at` forward; status events
matching *incoming* rows; the webhook returning 5xx and triggering ~2 days of
retries; MMS, data-SMS and `sms:cancelled` unhandled; both provider parsers
reading field names that do not exist in the real payload; `get_message_status`
sending no auth.

---

## 2. The receive path, end to end

```
   phone receives SMS
          │
          ▼
   SMS-Gate app fires  POST {PUBLIC_BASE_URL}/api/v1/webhooks/smsgateway
          │            headers: X-Signature (hex HMAC), X-Timestamp (unix seconds)
          ▼
   webhooks.py  ── verify HMAC over (raw_body + timestamp), reject 401 if bad
          │      ── dedupe on envelope `id`
          ▼
   sms_service.process_inbound_message()
          │      ── normalise sender (keeps short codes / alphanumeric IDs)
          │      ── find or create Contact
          │      ── find or create Conversation, status=unread, unread_count++
          │      ── store Message(direction=incoming, created_at=receivedAt)
          │      ── opt-out keyword → suppression list + stop sequences
          ▼
   GET /api/v1/inbox/conversations   ← InboxPage polls every 10s
```

Every event carries the same envelope, and the top-level `id` is what you
dedupe on:

```json
{
  "deviceId": "…", "event": "sms:received", "id": "unique-event-id",
  "webhookId": "…",
  "payload": { "messageId": "…", "message": "…", "sender": "+234…",
               "recipient": "…", "simNumber": 1, "receivedAt": "2026-08-11T09:15:00+01:00" }
}
```

Events handled: `sms:received`, `sms:data-received`, `mms:received`,
`mms:downloaded` (inbound); `sms:sent`, `sms:delivered`, `sms:failed`,
`sms:cancelled` (status); `system:ping`, `app:started` (acknowledged, no-op).

### Signature verification

`X-Signature` is a hex HMAC-SHA256 over `raw_body + X-Timestamp` (no separator),
keyed with the signing key from **Settings → Webhooks → Signing Key** in the
app. The check uses the raw bytes before JSON decoding, compares in constant
time, and rejects timestamps more than 5 minutes off. A forged signature gets
**401**; everything else returns **2xx**, because a non-2xx reply makes the
device retry with exponential backoff up to 14 times over roughly two days.

---

## 3. Automatic registration and sync

* **On startup**, `main.py` registers all webhooks if credentials and
  `PUBLIC_BASE_URL` are set, and records the result in `system_settings` so it
  is not re-registered on every boot.
* **Sync** (`POST /api/v1/inbox/poll-now`, the "Sync" button) reconciles the
  live webhook list against the required set — creating what is missing,
  keeping what matches, deleting stale entries that point at an old URL — then
  triggers an inbox export so anything received while the server was down is
  pushed through as normal `sms:received` webhooks.
* **Diagnostics** (`POST /api/v1/inbox/poll-debug`) reports exactly what is
  wrong in plain language, e.g. *"SMS-Gate credentials are missing"*, *"No
  device online — open SMS-Gate on the phone"*, *"No webhook registered for
  this URL — click Sync to register"*. Both panels are rendered in the Inbox UI.

Catch-up uses the cloud path, `POST /3rdparty/v1/messages/inbox/export`
`{deviceId, since, until}`; the device then re-pushes one `sms:received` per
message. The `GET /inbox` read endpoint is **Local Server Mode only** and is not
available on the cloud API, so it is not relied upon.

---

## 4. Outbound replies

The chat's reply box now reports the real outcome. Previously any accepted HTTP
call returned `success: true`, so a message the gateway had rejected still
toasted "Sent". Now a gateway rejection returns `success:false` with the
gateway's own reason and the row is stored as `failed`; an opted-out or
suppressed contact returns **409** with an explanation instead of a misleading
"Gateway not configured" 500.

Delivery state advances `queued → sending → sent → delivered/failed/cancelled`
and never moves backwards, which matters because `sms:delivered` fires **once
per part** of a multipart message and `sms:sent` can arrive after it.

---

## 5. What you must configure

| Setting | Where | Notes |
|---|---|---|
| `SMSGATE_USERNAME` / `SMSGATE_PASSWORD` | env | **Rotate these** — the old pair is in git history at commit `74c9cc2`. |
| `SMSGATE_WEBHOOK_SECRET` | env | Must equal Settings → Webhooks → Signing Key in the phone app. |
| `PUBLIC_BASE_URL` | env | Public HTTPS origin of this server. |

The webhook endpoint **must be HTTPS with a valid certificate**. Plain HTTP is
only accepted for `http://127.0.0.1`. For local development use an ADB reverse
forward, a Cloudflare Tunnel, or ngrok.

Existing Postgres databases also need the `allow_weekends` column added — see
`AUDIT.md`.

---

## 6. Verification

`backend/tests/test_inbound_receive.py` (21 tests) covers the whole path;
the full suite is **108 passing**. The receive tests were run against the
pre-fix commit as a control and 6 of them failed there, confirming they catch
the real regressions rather than merely describing current behaviour.

Live run against a real database and real signed webhooks: 8 messages stored
across 3 threads; an identical envelope deduped; a repeat `YES` with the same
`messageId` correctly stored as a second message; short-code and foreign senders
accepted; an MMS stored as `[Photo] look [attachment: img.jpg]`; a back-dated
message inserted in the right chronological position without disturbing the
preview; `system:ping` and unknown events acknowledged; a forged signature
rejected with 401; delivery receipts matched, with a late `sms:sent` and
repeated per-part `sms:delivered` leaving both status and timestamp intact.
