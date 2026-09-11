# 📲 Inbuilt Notifications (no Pushover, no limits)

SendSMS has its own notification sender. New SMS replies, campaign results and
missed calls land on your phone as **browser notifications** — free forever,
no Pushover, no OneSignal, no quotas to exhaust.

## How it works

1. Every event is saved in the app (the 🔔 bell in the header + the
   **Notifications** page), so nothing is ever lost.
2. The same event is pushed to every subscribed browser via **Web Push
   (VAPID)** — the open standard built into Chrome, Edge, Firefox and Samsung
   Internet. On Android it buzzes/vibrates like a native app notification,
   even when the browser is closed (best with the app installed: menu →
   *Install app* / *Add to Home screen*).
3. Tapping a notification opens the right screen: a reply opens that
   conversation, a campaign result opens Campaigns, a missed call opens Phone.

Events covered today:

| Event | Trigger |
|---|---|
| 📱 New SMS reply | inbound message arrives (muted carrier senders excluded) |
| ✅ Campaign finished | campaign completes, with sent/delivered counts |
| ❌ Campaign failed | failure hook ready (see API below) |
| 📞 Missed call | inbound CallGate call ends unanswered |
| ⏰ Follow-up due | hook ready for follow-up engine |
| 🔔 Test | "Send test" button |

## Setup (30 seconds per phone)

1. Open the app on your phone's browser and log in.
2. Tap the **🔔 bell** in the top header → **Enable notifications**.
3. The browser asks *"Allow sendsms … to send notifications?"* → tap **Allow**.
4. Done. To verify: bell → *View all* → **Send test** — your phone should
   buzz within seconds.

Manage devices any time in **Settings → Notifications**: per-browser status,
**Send test**, and the list of subscribed devices.

> ℹ️ iPhone: push needs iOS 16.4+, the app installed to the Home Screen
> (*Share → Add to Home Screen*), and opening it from that icon. Events still
> appear in the in-app centre on any browser regardless.

> ⚠️ If you tapped **Block** by mistake: tap the 🔒/ⓘ icon in the browser's
> address bar → Permissions/Site settings → set Notifications to **Allow**,
> then reload and enable again.

## For developers

- Backend: `app/services/push_service.py` — `notify()` records the
  `NotificationEvent` (provider `"browser"`) and fans out to
  `push_subscriptions` via `pywebpush`. Failures are counted per
  subscription; 404/410 or 5 consecutive failures prunes the endpoint.
  Sending is best-effort and never blocks SMS ingest or webhooks.
- VAPID keys are generated once on first use and stored in `system_settings`
  (`push.vapid_public/private`); or pin them with `VAPID_PUBLIC_KEY` /
  `VAPID_PRIVATE_KEY` env vars to survive fresh databases.
  `VAPID_CLAIM_EMAIL` sets the `mailto:` contact in push claims.
- Frontend: `utils/push.ts` (subscribe/unsubscribe), `NotificationBell`
  (header badge + dropdown), `pages/NotificationsPage.tsx`,
  Settings → Notifications card, and push/click handlers in `public/sw.js`.
- API: `GET /notifications/` · `GET /notifications/unread-count` ·
  `POST /notifications/{id}/read` · `POST /notifications/read-all` ·
  `GET /notifications/push/vapid-key` · `POST /notifications/push/subscribe|unsubscribe` ·
  `GET /notifications/push/subscriptions` · `POST /notifications/push/test`.
- Emit a custom event from anywhere with a DB session:
  ```python
  from app.services.push_service import notify
  await notify(db, "followup_due", "⏰ Follow-up due: Ada",
               "Quote sent 3 days ago.", url="/follow-ups")
  ```
- Tests: `backend/tests/test_notifications.py` (8) and
  `frontend/src/utils/push.test.ts` (6).
