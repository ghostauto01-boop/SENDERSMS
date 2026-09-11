# CallGate integration — phone calls from the app, via your SIM

This app places real GSM phone calls through **CallGate**
(`call-gate-app/android-app`), the companion Android app to SMS-Gate.
Install it on the **same phone** as SMS-Gate and the Contacts, Inbox and
Phone pages can call any contact using your phone's SIM.

CallGate does NOT handle audio — it tells the phone to start/end its own
normal call, exactly like tapping a number in the dialler.

---

## 1. Phone setup (2 minutes)

1. On the SMS-Gate phone, install **CallGate** from
   `github.com/call-gate-app/android-app` → Releases → latest APK.
2. Open CallGate and tap **Offline → Online** (server starts on port 8084;
   a status-bar icon appears).
3. In CallGate go to **Settings → Server**: note the username/password
   (change the random password to something secure) and find the phone's
   IP address (Android Wi-Fi settings → connected network → IP).
4. In this app go to **Settings → Calls** and enter:
   - **Phone address**: the IP, e.g. `192.168.1.5:8084` (just the IP is
     fine — `http://` and `/api/v1` are added automatically).
   - **Username / password** from step 3.
5. **Save**, then **Test connection**. You should see
   `Connected to CallGate (N webhook(s) registered)`.

## 2. Reachability — read this

CallGate is a **local** server (unlike SMS-Gate it has no cloud relay), so
this server must be able to open `http://<phone>:8084` directly:

| Setup | Works? |
|---|---|
| Server + phone on the same Wi-Fi/LAN | ✅ Yes |
| Phone on Tailscale/WireGuard VPN with the server | ✅ Yes (use the VPN IP) |
| Tunnel app on the phone (ngrok/Cloudflare) exposing 8084 | ✅ Yes (use the tunnel URL) |
| Server on Render, phone on mobile data, no VPN/tunnel | ❌ No — use **Direct dial** mode |

When CallGate is unreachable the app **automatically falls back**: tapping
Call opens a `tel:` link that dials from whatever device you are holding,
and the attempt is still recorded in call history. You can also force this
with **Settings → Calls → Direct dial (this device)**.

## 3. Call history (optional webhooks)

For live status (ringing → connected → ended + duration) in the Phone →
Recent tab and Analytics:

1. In CallGate: **Settings → Webhooks → Signing Key** — copy the key.
2. In this app: **Settings → Calls → Call history signing key** — paste it,
   **Save**.
3. **Settings → Calls → Call status webhooks → Register webhooks**.
   (The phone must be able to reach this server over HTTPS for deliveries.)

Without this, calls still work — only live status tracking is skipped.

## 4. Where calling lives in the app

| Place | What you get |
|---|---|
| **Phone page** (`/phone`) | All contacts + dial pad + Recent history + active-call banner with End call |
| **Contacts** | Call / SMS / WhatsApp chat / WhatsApp call / Website buttons per contact |
| **Inbox** | Call + WhatsApp + Website buttons in the chat header and Contact info panel |
| **Analytics** | Call volume + connect-rate chart |

## 5. WhatsApp + website buttons

- **WhatsApp chat** opens `https://wa.me/<number>` directly in WhatsApp.
- **WhatsApp call** opens the same chat — WhatsApp exposes no official
  voice-call URL, so the call button inside WhatsApp is one tap away.
- **Website** appears only when the contact has a website saved, and opens
  it in a new tab (`https://` added if missing).

## 6. API reference (all under `/api/v1/calls`, auth required)

| Method | Endpoint | Purpose |
|---|---|---|
| GET/PUT | `/calls/config` | Read/save phone address + credentials + dial mode |
| POST | `/calls/test` | Connectivity check with plain-language diagnosis |
| POST | `/calls/start` | `{contact_id}` or `{phone_number}` → place a call |
| POST | `/calls/end` | Hang up the active call |
| POST | `/calls/log-direct` | Record a `tel:` dial in history |
| GET | `/calls/logs` | Paginated call history (`search`, `page`) |
| GET | `/calls/active` | Current in-progress call, if any |
| GET/POST/DELETE | `/calls/webhooks…` | Manage `call:ringing/started/ended` webhooks |

The device posts events to `POST /api/v1/webhooks/callgate` using the same
envelope + HMAC-SHA256 signing as SMS-Gate (idempotent on the envelope id).

## 7. Verification

- `backend/tests/test_callgate.py` (13 tests): provider URL normalisation,
  config save/mask, direct-dial fallback, webhook signature rejection, the
  ringing → started → ended flow with duration, duplicate-delivery dedupe.
- Live E2E against a mock handset server: config save → test → webhook
  registration → real `POST /calls` on the mock → signed webhooks → history
  row `ended` with duration. Analytics, `/send/validate` and the ads quota
  preview were exercised on the same running server.
