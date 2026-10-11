# Gmail replies with Google sign-in (OAuth) — the whole setup

This is the written version of the walkthrough inside the app
(**Settings → Gmail replies**), for reading away from the screen or handing to
somebody else. It replaces the older "connect the replies inbox with an app
password" route, which still works but is now the fallback.

**What you end up with:** every Gmail inbox you connect is read by the app — Inbox
*and* Spam — so a prospect's reply shows up in **Email Inbox → Conversations**
within a minute, is threaded onto the campaign that caused it, and can be
answered from the same address it arrived on. You can connect as many Gmail
inboxes as you want; each one is polled on its own interval and has its own
counters.

---

## 1. Why Google sign-in instead of an app password

| | Google sign-in (Gmail API) | App password (IMAP) |
|---|---|---|
| Setup | One Google Cloud project, once | 2-Step Verification + a 16-character password, per inbox |
| Reads Inbox and Spam | yes | yes |
| Rescues a reply out of Spam | yes | yes |
| **"Never send it to Spam" filter** | **yes — creates it for you** | no (IMAP cannot create Gmail filters) |
| Sends your reply | through the Gmail API, in the same thread | through Gmail's SMTP |
| Revoking | myaccount.google.com/permissions | delete the app password |
| Google's direction of travel | supported | being withdrawn for most accounts |

The filter row is the one that matters: "never send it to Spam" is Gmail's own
remedy for a reply that keeps being filed away, and only the API can create it.

Credentials are Fernet-encrypted at rest with `CREDENTIAL_ENCRYPTION_KEY`. The
refresh token and the client secret are never returned by any endpoint and never
logged.

---

## 2. Before you start

You need:

1. **A public HTTPS address for this deployment.** Google redirects the browser
   back to it, so `http://localhost` will not do. Set `PUBLIC_BASE_URL`
   (on Render, `RENDER_EXTERNAL_URL` is used automatically when it is unset).
2. **A Google account to own the OAuth client.** Any account will do — it does
   *not* have to be one of the mailboxes you connect. A throwaway
   "mycompany-apps" account is a fine choice, because the client keeps working
   even if somebody leaves.
3. **The list of Gmail addresses you want to read.** Each one becomes a test user
   in step 4 (or the app gets published, which needs Google's verification).

The app shows you the values for 1 and 2 on the setup card, so you never have to
guess them:

- **Authorized redirect URI (callback URL)** — `https://YOUR-APP/api/v1/mailbox/google/callback`
- **Authorized JavaScript origin** — `https://YOUR-APP`
- **Scope** — `https://mail.google.com/`

Copy them from **Settings → Gmail replies**; each has a copy button.

---

## 3. Create the Google Cloud project and enable the Gmail API

1. Open <https://console.cloud.google.com> and sign in with the account from
   §2.2.
2. Project picker (top left) → **New Project** → name it something recognisable,
   e.g. `SENDERSMS replies` → **Create**. No billing account is needed; the Gmail
   API quota used here is free.
3. **APIs & Services → Library** → search **Gmail API** → **Enable**.
   Direct link: <https://console.cloud.google.com/apis/library/gmail.googleapis.com>

Skipping step 3 is what makes the consent screen fail with `access_denied` and
nothing else.

---

## 4. Configure the OAuth consent screen

**APIs & Services → OAuth consent screen**
(newer consoles: **Google Auth Platform → Branding** and **→ Audience**):

1. **User type: External.** "Internal" is only available to Google Workspace
   accounts and only for users in that same domain.
2. **App name**, **User support email**, **Developer contact email** — this is
   what the consent screen shows, so name it something you will recognise later
   ("SENDERSMS replies", not "Project 1").
3. **Scopes → Add or remove scopes** → paste `https://mail.google.com/` →
   **Update**. That single scope covers reading Inbox and Spam, sending a reply
   and creating the Spam filter. (You can add it later under
   **Data access → Scopes**; the consent screen just has to know about it before
   the first sign-in.)
4. **Audience → Test users → Add users.** Add **every Gmail address you intend to
   connect**, plus your own. This is the step people miss: while the app is in
   **Testing**, an account that is not a test user is refused with
   `access_blocked`, and — less obviously — a *test-user* grant keeps its refresh
   token, while an unlisted one would expire after seven days.
5. **Save.**

> **Publishing.** "Publish app" (moving to *In production*) lifts the 100-test-user
> limit and removes the "unverified app" warning, but a sensitive scope like
> `https://mail.google.com/` then requires Google's verification (a privacy
> policy URL, a video of the app in use, and an annual security assessment for
> restricted scopes). For reading your own inboxes, staying in **Testing** with
> your addresses as test users is the right answer.

---

## 5. Create the OAuth client ID

**APIs & Services → Credentials → Create Credentials → OAuth client ID**:

1. **Application type: Web application.**
   Not "Desktop app" — this app runs the callback on its own server, so it needs
   a redirect URI. (A Desktop client has no redirect-URI field at all, and the
   round-trip then fails with `redirect_uri_mismatch`.)
2. **Name**: anything, e.g. `SENDERSMS web`.
3. **Authorized JavaScript origins**: `https://YOUR-APP` — the origin only, no
   path, no trailing slash.
4. **Authorized redirect URIs → Add URI**: paste the callback URL from §2
   exactly:

   ```
   https://YOUR-APP/api/v1/mailbox/google/callback
   ```

   One character of difference is the whole failure: a trailing `/`, `http`
   instead of `https`, a missing `/api/v1`, or a different host (a custom domain
   on the app but the `*.onrender.com` address in the console). Google compares
   this string byte for byte.
5. **Create.** Google shows the **Client ID** and the **Client secret** — the
   secret once, behind an "eye" icon. Copy both.

---

## 6. Save them in the app

**Settings → Gmail replies → Your OAuth client**:

1. Paste the **Client ID**.
2. Paste the **Client secret**.
3. **Save OAuth client.**

The card flips to **ready**, and "Connect a Gmail inbox" switches on. The secret
is encrypted before it is stored and is never shown again — the field reads
"•••••••••••• saved". Saving a client ID on its own is refused, because Google
cannot exchange a code without both halves; resetting the secret in the console
and pasting the new one here replaces it.

Saved values take precedence over `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in
the environment, which keep working for deployments that prefer to configure the
server. The card says which one is in force ("from this app" / "from
environment").

---

## 7. Connect your Gmail inboxes

1. **Settings → Gmail replies → Connect a Gmail inbox.**
2. Google shows the **account chooser** — pick the inbox you want to read.
   The chooser is forced on purpose (`prompt=consent select_account`), so Google
   never silently reuses whichever account the browser is signed in to. That is
   what makes a second inbox possible.
3. Consent screen: because the app is not Google-verified you will see
   **"Google hasn't verified this app"** → **Advanced** → **Go to *SENDERSMS
   replies* (unsafe)** → **Allow** (tick "Don't show again" if you like). The
   warning is about the app not having paid for a Google audit — it is not a
   problem with your setup.
4. The app exchanges the code, reads the mailbox address from Google's profile,
   stores the refresh token, creates its label, and runs a first sync
   immediately. You land back on the page you started from with a summary: how
   many replies were imported, or "no replies were waiting right now".
5. **Add another:** press **Connect a Gmail inbox** (or **Add another**) again and
   pick the next account. There is no limit in this app — each mailbox is a
   separate row with its own poll interval, counters and controls. Every address
   must be a test user (§4.4).

Reconnecting an address that is already in the list replaces its credential
rather than creating a duplicate, so an expired grant is fixed by connecting
again.

---

## 8. Turn on the Spam filter and check it end to end

1. **Email Manager → Replies** → the mailbox card → **Never send it to Spam →
   Install**. This creates the filter Gmail's own editor writes for that
   checkbox (`removeLabelIds: ["SPAM"]`), so a prospect's reply is not filed away
   before the app can read it.
2. Leave **Send one-to-one replies from this mailbox** on: your answer then
   leaves from the same authenticated address the thread started on, which is
   what stops Gmail treating the conversation as spoofed.
3. **Prove it:** send a campaign to a second address you own, reply to it from
   that address (deliberately, and also once via a message you drag into Spam),
   and watch **Email Inbox → Conversations**. It should appear within
   `GMAIL_POLL_INTERVAL` seconds (60 by default), attributed to the campaign,
   with the reply rescued from Spam if you filed it there.
4. **Settings → Gmail replies** shows each inbox's last check, how many replies it
   imported, and the error text if the last sync failed. **Import now** forces a
   sync without waiting for the interval.

---

## 9. When it does not work

| Symptom | Cause | Fix |
|---|---|---|
| `redirect_uri_mismatch` | The URI in the console is not byte-identical to the app's | Re-copy it from **Settings → Gmail replies** (§5.4) |
| `access_denied` right away | The Gmail API is not enabled on the project | §3.3 |
| `access_blocked` / "app is not verified" with no **Advanced** button | That Gmail address is not a test user while the consent screen is in Testing | §4.4 |
| `Google did not return a refresh_token` | The account already granted access once, without `prompt=consent` | Revoke at <https://myaccount.google.com/permissions>, connect again |
| `invalid_client` | Client ID and secret are from different OAuth clients, or the secret was reset in the console | Re-copy both; re-save |
| `invalid_grant` on a mailbox card | The refresh token was revoked, or the Google project was deleted | Connect that inbox again |
| "PUBLIC_BASE_URL is not set" | The app cannot tell Google where to send you back | Set `PUBLIC_BASE_URL` to the public HTTPS address and restart |
| Replies arrive in Gmail but nothing is imported | The mailbox has never synced successfully | **Import now** on the mailbox card and read the error it reports |
| Signing in works but a second inbox never appears | Google reused the signed-in account | The app forces the chooser; if you are on an older build, sign out of the other account first |

---

## 10. Environment variables (the alternative to §6)

```dotenv
PUBLIC_BASE_URL=https://your-app.onrender.com
GOOGLE_CLIENT_ID=1234567890-abcdefghijklmnop.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxxxx
GMAIL_POLL_INTERVAL=60
GMAIL_RESCUE_FROM_SPAM=true
GMAIL_SEND_REPLIES=true
CREDENTIAL_ENCRYPTION_KEY=…   # encrypts the refresh tokens and the secret
```

Everything else — how many inboxes, which one sends replies, the Spam filters —
is configured in the app, not here.

**API surface** (all behind the operator login):

- `GET /api/v1/settings/google-oauth` — the client in use, its source
  (`db`/`env`/`none`), the redirect URI, the JavaScript origin, the scope, and
  the connected Gmail inboxes.
- `PUT /api/v1/settings/google-oauth` — save `client_id` / `client_secret`.
- `GET /api/v1/mailbox/google/start?return_to=/settings%3Ftab%3Dgmail` — the
  consent URL. `return_to` must be a same-origin path; anything else is dropped.
- `GET /api/v1/mailbox/google/callback` — where Google lands. The signed `state`
  carries the operator id and the return target, and expires after 10 minutes.
- `GET /api/v1/mailbox`, `POST /api/v1/mailbox/{id}/sync`, `…/check`,
  `…/filters`, `…/test-send`, `DELETE /api/v1/mailbox/{id}` — per-inbox controls.
