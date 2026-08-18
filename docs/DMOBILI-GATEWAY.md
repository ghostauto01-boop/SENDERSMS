# Dmobili.com as a second SMS gateway

SENDERSMS can send through **two interchangeable SMS gateways**:

| Gateway | What it is | Role |
|---|---|---|
| `smsgate` — SMS-Gate.app | Android phone bridge (your own phone + SIM) | Default gateway |
| `dmobili` — Dmobili.com | Hosted Nigerian/global bulk SMS provider (Pace Bulk SMS platform) | Optional second gateway |

Switching between them is a single toggle in **Settings → SMS Gateway**.
Everything — direct sends, campaigns, sequences, follow-ups, scheduled sends,
auto-replies, delivery reports and inbound replies — follows the active
gateway. Nothing breaks when only one of them is configured: the app starts
on `smsgate` and stays there until you actively switch.

---

## What the Dmobili API supports (research summary)

Dmobili's [Developers page](https://dmobili.com/developers.php) states their
HTTP API can:

1. **Send SMS** from your website or application.
2. **Receive SMS** into your application (two-way).
3. **Get delivery reports** (DLRs).

Key facts about the platform (it runs the **PACE Bulk SMS** product from
Defture Nigeria Ltd — their signup flow is literally
`dmobili.com/csupport/pacebulksms-signup`):

- REST-style HTTP API, GET/POST, JSON and XML content types.
- Auth: **Basic login (username + password) and/or an API token**.
- Phone numbers must carry the **234** national prefix (the provider module
  formats numbers automatically).
- DLR vocabulary: `PENDING`, `DELIVERED`, `EXPIRED`, `REJECTED`,
  `UNDELIVERABLE`, `HANDSET_ERRORS`, `USER_ERRORS`, `OPERATOR_ERRORS`.
- Routes: Alpha (registered Sender ID required, ~24–48 h approval), Alpha 2,
  Premium/Premium2 (DND), OTP (CAC documents required). DND delivery is
  blocked by the networks on marketing routes.

### ⚠️ The spec is private — you must request it

There is **no public API documentation**. The Developers page ends with
*"Contact us today for your HTTP API need."* — credentials, endpoint URLs and
the exact request/response format are issued by their support team per
account (phone 08168919910, or via the contact page).

Because of that, every endpoint on our side is **configurable via
environment variables** (see below). The defaults match the common Pace
platform layout; when their docs arrive, point the paths at whatever they
give you — no code change should be needed.

### ⚠️ Two-way SMS needs a dedicated number

Dmobili's bulk routes send **from an alphanumeric Sender ID**, which cannot
receive replies. To get inbound SMS (replies) you need their **Long Code** or
**Short Code** product — ask support to enable two-way on a dedicated number
and to push inbound messages to your callback URL (below). Until then,
sending + delivery reports work, but replies won't arrive.

---

## Configuration

Set these environment variables (Render/Docker/`.env`):

```bash
# --- Dmobili gateway (optional second gateway) ---
DMOBILI_BASE_URL=https://dmobili.com        # gateway host
DMOBILI_USERNAME=your_username              # portal username
DMOBILI_PASSWORD=your_password              # portal password
# ... or, for token-authenticated accounts:
# DMOBILI_API_TOKEN=your_api_token

DMOBILI_SENDER_ID=MyBrand                   # registered Sender Name
DMOBILI_SEND_PATH=api/sms/index.php         # send endpoint (confirm with their docs)
# DMOBILI_BALANCE_PATH=api/sms/balance.php  # optional: enables credential verification
# DMOBILI_REPORT_PATH=api/sms/report.php    # optional: enables DLR polling
# DMOBILI_ROUTE=                            # optional: Alpha / Premium / OTP ...

# Protects the inbound callback endpoint (REQUIRED in production):
DMOBILI_WEBHOOK_SECRET=a-long-random-string
# DMOBILI_WEBHOOK_ALLOW_UNSIGNED=0          # never enable in production
```

### Enabling it

1. Deploy with the variables above.
2. Open **Settings → SMS Gateway**.
3. Both gateways appear as cards. Click **Test Connection** on the Dmobili
   card. With a balance endpoint configured this verifies credentials;
   without one it verifies reachability (the response says which).
4. Click **Use this gateway** on the Dmobili card. Done — all outbound SMS
   now goes through Dmobili. Click the same button on SMS-Gate to switch back.

The toggle refuses to switch to a gateway that has no credentials, and if
the active gateway is ever selected but unconfigured, sends **fail loudly**
instead of silently routing through the other provider (that would change
sender IDs, costs and compliance posture).

### Callback URL for inbound SMS + DLRs

Give Dmobili support this URL (shown with a copy button in Settings):

```
https://YOUR-APP-DOMAIN/api/v1/webhooks/dmobili
```

(`PUBLIC_BASE_URL` must be set for it to resolve.) The endpoint:

- accepts JSON or form-encoded bodies and tolerates the field spellings this
  platform family uses (`from`/`sender`/`msisdn`, `message`/`text`/`msg`,
  `status`/`state`/`dlr`);
- requires the shared secret (`DMOBILI_WEBHOOK_SECRET`) as `?secret=…`,
  an `X-Dmobili-Secret` header, or a `secret`/`token` field in the body;
- stores inbound SMS in the same conversations/inbox as SMS-Gate replies
  (with `provider="dmobili"` attribution), runs opt-out keywords,
  auto-replies and Pushover notifications exactly as before;
- applies DLRs (`DELIVERED`/`REJECTED`/…) to the matching outgoing message.

If their callback format turns out to use unusual field names, extend the
`_DMOBILI_*_KEYS` tuples at the top of
`backend/app/api/v1/webhooks.py` — one line per alias.

---

## How it differs from SMS-Gate.app

| Capability | SMS-Gate.app | Dmobili |
|---|---|---|
| Sending | via your Android phone's SIM | via provider's routes (needs credit) |
| Sender ID | your phone number | registered alphanumeric Sender ID |
| Inbound SMS | device pushes webhooks | provider pushes callbacks (needs Long/Short Code) |
| Delivery reports | per-SIM device events | DLR callbacks (or report endpoint if configured) |
| SIM slot setting | yes (SIM 1/2) | n/a — routing is per account |
| Auth | username/password | username/password or API token |

## Developer notes

- Provider adapter: `backend/app/providers/dmobili.py`
  (module-level `send_sms_direct` / `test_connection_direct` /
  `poll_status_for_ids` mirror the smsgate module shape).
- Dispatch: `backend/app/services/gateway_dispatch.py` — the only place that
  decides which gateway is live. All send paths (direct send, campaigns,
  sequences, scheduled sends, auto-replies, Celery tasks, inline poller) go
  through `send_sms_dispatch()`.
- Active gateway is stored in `system_settings` under `gateway.active_provider`.
- Delivery-status polling is per-provider: each gateway is polled only for
  the messages it sent, so a mid-flight switch never strands receipts.
- Tests: `backend/tests/test_dmobili_provider.py`,
  `test_gateway_dispatch.py`, `test_dmobili_webhook.py`,
  `test_gateway_settings_api.py`.
