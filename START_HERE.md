# START HERE — Getting SendSMS Live, Step by Step

This is the plain-English guide. No prior experience assumed. Follow it top to bottom.

**Time needed:** about 60–90 minutes.
**Money needed:** ₦0. Every service below has a free tier that does not ask for a card.

**What you are building:** a website (your control panel) that lives on the internet, which
sends and receives real text messages through an Android phone you own.

```
   You  ──►  SendSMS website  ──►  SMS-Gate cloud  ──►  Your Android phone  ──►  SMS
                    ▲                                                              │
                    └──────────── reply comes back the same way ◄──────────────────┘
```

Your Android phone is the thing that actually sends the SMS. The website is the brain that
decides what to send and keeps the records.

---

## The 9 steps

| # | Step | Time |
|---|------|------|
| 1 | Create the three free accounts | 15 min |
| 2 | Get your database address | 5 min |
| 3 | Get your Redis address | 5 min |
| 4 | Make your secret passwords | 2 min |
| 5 | Set up the Android phone | 10 min |
| 6 | Put the app on the internet (Render) | 20 min |
| 7 | Connect the phone to the app | 5 min |
| 8 | Fix an older database (**most people skip this**) | 5 min |
| 9 | Turn on phone alerts + final test | 10 min |
| 10 | (Optional) Let ChatGPT or Claude run the app | 5 min |

---

## Before you start: two words explained

- **Environment variable** — a setting you type into a website's control panel instead of
  into the code. Things like passwords go here so they never end up on the internet.
  It's just a name and a value, like `ADMIN_PASSWORD` = `MySecret123`.
- **Deploy** — to copy your app onto a computer on the internet so it runs 24/7 and
  doesn't stop when you close your laptop.

Keep a blank notes file open. You will collect **8 values** along the way. Keep it safe —
these are effectively passwords.

---

## Step 1 — Create three free accounts

Sign up for these. Use the same email for all three.

1. **GitHub** — https://github.com — stores your code. You likely already have this.
2. **Neon** — https://neon.tech — your database (the filing cabinet: contacts, messages).
   Click "Continue with GitHub".
3. **Upstash** — https://upstash.com — your Redis (the to-do list your app works through).
   Click "Continue with GitHub".

None of these ask for a card on the free plan.

---

## Step 2 — Get your database address

1. In **Neon**, click **Create a project**. Name it `sendsms`. Pick the region closest to
   you (Frankfurt is a good default for Nigeria). Click **Create**.
2. You'll land on a page showing a **Connection string**. Click the copy button.
   It looks like:
   ```
   postgresql://neondb_owner:AbC123xyz@ep-cool-name.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```
3. **You must edit it: delete the `?` and everything after it.** That's the only change
   needed. Result:
   ```
   postgresql://neondb_owner:AbC123xyz@ep-cool-name.eu-central-1.aws.neon.tech/neondb
   ```

> **Why this one edit is not optional.** Neon's copy button adds `?sslmode=require`
> (and often `&channel_binding=require`). The app's database driver does not understand
> those two words and **crashes on startup** with
> `TypeError: connect() got an unexpected keyword argument 'sslmode'`.
> I tested this directly against this project's driver — with the `?...` left on it fails
> every time; with it removed it starts fine. Your connection is still encrypted: Neon
> requires TLS on its side regardless.
>
> **You do *not* need to add `+asyncpg`.** The app rewrites the prefix for you
> automatically (`backend/app/database.py`). Adding it yourself is harmless if you prefer,
> but it is not required — an earlier version of this guide said it was, which was wrong.

**Save in notes:** `DATABASE_URL` = that cleaned-up URL. You'll use the same one again in
Step 8.

---

## Step 3 — Get your Redis address

1. In **Upstash**, click **Create Database**. Name it `sendsms`. Choose the same region.
   Choose the **free** plan. Click **Create**.
2. Scroll to the connection details and find the **`redis://...`** URL
   (not the "REST" one). Copy it. It looks like:
   ```
   rediss://default:AbCdEf123@eu2-fine-hound-12345.upstash.io:6379
   ```
   > `rediss` with two s's is correct — it means encrypted.

**Save in notes:** `REDIS_URL`

---

## Step 4 — Make your secret passwords

The app needs two long random secrets. Don't invent them by hand — random is safer.

Open https://www.random.org/strings/ and generate, **or** if you have a Mac/Linux terminal:

```bash
# SECRET_KEY — signs your login session
openssl rand -hex 32

# CREDENTIAL_ENCRYPTION_KEY — encrypts saved gateway passwords
openssl rand -base64 32
```

**Save in notes:** `SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`

Also decide now:
- `ADMIN_USERNAME` — the operator account name, e.g. `admin`
- `ADMIN_PASSWORD` — **the password the login screen asks for.** The app ships
  with `12345678` so you can get in on the first run; change it here before the
  address is shared with anyone. The stored admin account follows this value, so
  rotating it takes effect at the next sign-in and never locks you out.

> **The login screen is password-protected.** A wrong password is refused and an
> empty one is never accepted, so only someone with `ADMIN_PASSWORD` gets in. If
> `ADMIN_PASSWORD` is left empty, add `APP_PASSWORD` instead — that is the site
> password, and the app will use whichever one is set.

---

## Step 5 — Set up the Android phone

This phone must stay on, charged, and with airtime/an SMS bundle. It does the actual sending.

1. On the Android phone, install **SMS Gate** from the Play Store
   (https://play.google.com/store/apps/details?id=me.capcom.smsgateway).
2. Open it. Grant the **SMS** and **Contacts** permissions it asks for. Say yes to everything —
   without SMS permission it cannot send.
3. Turn on **Cloud Server** mode (sometimes labelled "Cloud gateway" / "Private server").
   > **Why cloud mode:** your phone dials out to SMS-Gate's servers. That means you do
   > **not** need ngrok, a tunnel, or a fixed IP address — those are only for "Local" mode
   > and are much harder. Ignore any tunnel instructions you read elsewhere.
4. The app now shows a **username** and **password**. These are new, generated for you.

   **Save in notes:** `SMSGATE_USERNAME`, `SMSGATE_PASSWORD`

   > ⚠️ **Important — do not reuse the old ones.** A previous username/password
   > (`_O48UB` / `nw_e7wyhwjwubp`) got saved into this project's history and must be
   > considered public. If your phone still shows those, tap the option to
   > **regenerate/reset credentials** and use the fresh pair.

5. In the app go to **Settings → Webhooks** and find the **Signing Key**
   (may be called "secret"). If it's blank, type a long random word of your own.

   **Save in notes:** `SMSGATE_WEBHOOK_SECRET`

   > **What this does:** when your phone reports "a new SMS arrived", this shared secret
   > proves the message really came from your phone and not from a stranger who found
   > your web address. Your app rejects unsigned reports.

6. **Turn off battery optimisation for SMS Gate**: Android Settings → Apps → SMS Gate →
   Battery → **Unrestricted**. Otherwise Android silently kills it after a few hours and
   your messages stop with no error.

---

## Step 6 — Put the app on the internet

1. Go to https://render.com and **Sign up with GitHub**.
2. Click **New +** → **Blueprint**.
3. Choose your `SENDERSMS` repository. **Select the branch `main`.**
4. Render reads the `render.yaml` file and offers to create **two services**:
   - `sendsms-api` — the website and control panel
   - `sendsms-worker` — the background helper that sends campaigns on schedule
5. Render will ask you to fill in the blanks. Render prompts you once per service, so you
   will type most of these **twice** — the values must match exactly.

   | Setting | Value | Which service |
   |---|---|---|
   | `DATABASE_URL` | the cleaned-up URL from Step 2 (no `?sslmode=...` on the end) | both |
   | `REDIS_URL` | from Step 3 | both |
   | `SECRET_KEY` | from Step 4 | both — **same value** |
   | `CREDENTIAL_ENCRYPTION_KEY` | from Step 4 | both — **same value** |
   | `ADMIN_USERNAME` | from Step 4 | both |
   | `ADMIN_PASSWORD` | from Step 4 | both |
   | `SMSGATE_USERNAME` | from Step 5 | both |
   | `SMSGATE_PASSWORD` | from Step 5 | both |
   | `SMSGATE_WEBHOOK_SECRET` | from Step 5 | both |
   | `PUBLIC_BASE_URL` | leave blank for now — Step 7 | both |
   | `CORS_ORIGINS` | leave blank for now — Step 7 | api only |

   > ⚠️ **`SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` must be *identical* on both
   > services.** Render offers to auto-generate them on the api service — don't accept
   > that. Paste your own, the same value in both places. If they differ, the worker
   > cannot read the gateway password and campaigns fail silently.

6. Click **Apply** / **Create**. Wait 5–10 minutes for the first build.
7. When `sendsms-api` goes green, copy its web address from the top of the page, e.g.
   `https://sendsms-api.onrender.com`

   **Save in notes:** `PUBLIC_BASE_URL`

**If the build fails**, click the **Logs** tab and read the last red lines:
- `unexpected keyword argument 'sslmode'` → you left `?sslmode=require` on your
  `DATABASE_URL`. Delete the `?` and everything after it. Step 2.
- `password authentication failed` → the database password is wrong; re-copy from Neon.
- Build ran out of memory → retry; the free tier is occasionally short on RAM.

---

## Step 7 — Connect the phone to the app

Now the app knows its own address, so tell it.

1. In Render, open **sendsms-api** → **Environment**. Set:
   - `PUBLIC_BASE_URL` = the address from Step 6, no trailing slash
     (`https://sendsms-api.onrender.com`)
   - `CORS_ORIGINS` = the same address
2. On **sendsms-worker**, set `PUBLIC_BASE_URL` to the same address. **Save changes** —
   both services restart.

> **Why this matters:** on startup the app automatically tells SMS-Gate *"send new messages
> to `<your address>/api/v1/webhooks/smsgateway`"*. If that address is wrong, the
> registration points nowhere and **incoming SMS never arrive** — with no error shown.
> The address must be `https://` with a valid certificate; your Render address already is.
>
> On Render specifically, leaving `PUBLIC_BASE_URL` blank is survivable — the code falls
> back to the `RENDER_EXTERNAL_URL` that Render injects for you
> (`backend/app/utils/urls.py`). Set it anyway: it costs nothing, and it's the difference
> between "works by luck" and "works on purpose" — especially if you ever add a custom
> domain, where the fallback would point at the wrong host.

**`CORS_ORIGINS` note:** the worker has no `CORS_ORIGINS` field in the blueprint, so set it
on **sendsms-api** only. `PUBLIC_BASE_URL` goes on both.

3. Wait for both to go green, then visit your address and log in with your
   `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
4. Go to **Settings → Webhooks** and confirm you see registered webhooks. If the list is
   empty, click **Register webhook**.

> **Heads-up about the free plan:** Render free services go to sleep after ~15 minutes idle
> and take ~50 seconds to wake. A text arriving during sleep is not lost — SMS-Gate keeps
> retrying for about 2 days — but it may land a minute late. Upgrade to the $7/month plan
> to remove this.

---

## Step 8 — Fix an older database

**Skip this entirely if your Neon database is brand new** (created in Step 2). It's already
correct. This is only for a database that already had contacts and messages in it.

> **Why it's needed:** the app creates missing *tables* on its own, but it never alters
> tables that already exist. Two recent improvements — a new campaign setting, and the rule
> that stops one contact from having several duplicate chat threads — need to be applied by
> hand, once.

I've written the script for you: **`scripts/migrate_existing_db.sql`**.

Using the `DATABASE_URL` you saved in Step 2:

```bash
# 1. Back up first — always
pg_dump "PASTE_YOUR_DATABASE_URL_HERE" > backup-before-migration.sql

# 2. Run the migration
psql "PASTE_YOUR_DATABASE_URL_HERE" -f scripts/migrate_existing_db.sql
```

> No `psql` on your computer? In Neon, open your project → **SQL Editor**, then copy the
> whole contents of `scripts/migrate_existing_db.sql`, paste it in, and click **Run**.

**Check it worked** — run this; it should return **no rows**:

```sql
SELECT contact_id, COUNT(*) FROM conversations
GROUP BY contact_id HAVING COUNT(*) > 1;
```

The script merges duplicate threads into the oldest one and keeps every message. It is
wrapped in a transaction: if anything goes wrong it undoes itself and changes nothing.
Running it twice is harmless.

It also adds the email columns (thread ids, attachments, bulk-mail flag). If you skip it,
the app adds those columns by itself on the next boot — the script just does it up front,
with the indexes and the strict settings.

---

## Step 8b — Email through Brevo (optional)

The app sends **and receives** email through your own **Brevo** account, next to SMS. You
do not edit any file to set it up — everything is on the **Email Manager** page.

1. In Brevo: **SMTP & API → API Keys → Create a new API key** (a v3 key starting
   `xkeysib-`). Copy it.
2. In this app: **Email Manager → Senders → Add sender**. Paste the API key, then press
   **Check Brevo's verified senders &amp; domains**. The app asks Brevo which From addresses
   that key has already verified and marks the ones on an authenticated domain as
   **warm** — click one to use it (this is the address that reaches inboxes; a
   hand-typed address Brevo has not verified will bounce or land in spam). Add a
   **reply-to** address and press **Test**; a green badge means Brevo accepted the key.
3. Add as many Brevo accounts as you like (a second key for when the first runs out of
   daily sends). Exactly one is the **default**; any campaign can pick a different one.
4. **Receiving mail:** on the Email Manager page each sender shows a **webhook URL**.
   Paste it into Brevo under **Transactional → Settings → Webhook** (or *Inbound parsing*
   for replies). Brevo then delivers replies straight into **Email Inbox**, threaded
   under the message they answer.
5. **Deliverability tab** — shows whether your domain's SPF/DKIM are authenticated
   (read live from Brevo), your bounce/open/click rates, and what to fix.
6. **Before any campaign goes out**: in the composer, type your own address in
   **Send a test to** and press **Send test**. It mails exactly what you wrote —
   variables, HTML, attachments — so you can see it in a real inbox first.

**Two things worth knowing**

* The webhook URL and the one-click unsubscribe link are absolute addresses, so the server
  needs to know its own public address: set **`PUBLIC_BASE_URL`** (Render → your service →
  Environment) to `https://your-app.onrender.com`. Without it, bulk mail still carries a
  `mailto:` unsubscribe and replies still arrive, but the clickable link and the webhook
  URL cannot be built.
* Use a **verified sender domain** in Brevo (Senders & IP → Domains). Mail from an
  unauthenticated domain lands in spam no matter what the app does.

---

## Step 9 — Phone alerts, then the real test

### Get alerts on your phone when someone replies

1. Install **Pushover** (https://pushover.net) on your phone — one-off ~$5 after the
   30-day trial. Skip this section if you don't want alerts.
2. Sign up on the website. Copy your **User Key** from the front page.
3. Click **Create an Application/API Token**, name it `SendSMS`, and copy the **API Token**.
4. In SendSMS go to **Settings → Notifications → Pushover**, paste both, tick **Enabled**,
   and press **Save** then **Test**.

   > These go in the app's Settings page, **not** into Render's environment variables. The
   > app reads them from the database. Putting them only in Render will not work.

5. You should get a test notification. Alerts show the contact's **name** when you have one
   saved — *"📱 New SMS from Ada Obi"* — and fall back to the number if not.

### The final test — do not skip

1. In SendSMS, go to **Contacts** → add yourself. Put your real number in
   `+234...` format and set a first name.
2. Go to **Inbox** (or Send) and send yourself a short message.
3. ✅ The SMS arrives on your personal phone within a few seconds.
4. **Reply to it from your personal phone.**
5. ✅ Your reply appears in the SendSMS **Inbox**, in a thread labelled with your name.
6. ✅ A Pushover alert arrives showing your name, not your raw number.

If all six happen, you are fully live.

---

---

## Step 10 — Let an AI run the app (optional)

This app can be operated by an AI assistant — ChatGPT, Claude, or any tool that speaks
**MCP** (Model Context Protocol). You can ask it in plain English to "import this CSV, write a
campaign to the Abuja list, send a test to me first, then start it", and it will do it through
the same pages-and-buttons logic you use, with the same opt-out and consent rules.

1. In the app: **Settings → AI (MCP) → Create token**. Give it a name (`ChatGPT`), choose a
   permission, and press **Create token**.
   * **Read & write** — it can send. Use this when you want the AI to actually work.
   * **Read only** — it can look and summarise, but every change is refused. Good for the
     first day, or for an assistant you only want to ask questions of.
   * **Copy the token immediately.** It is shown once and cannot be shown again (the app only
     stores a fingerprint of it).
2. In **ChatGPT** (Plus/Pro): *Settings → Connectors → Add custom connector*, paste the URL the
   page shows you (it ends in `/mcp`), choose **API key** authentication and paste the token.
   In **Claude**: *Settings → Connectors → Add custom connector*, same URL and token.
3. Ask it: **"Run how_to_use_this_app first, then tell me what you can do."** That tool returns
   this app's channels, its consent rules and the safe order of operations, so the assistant
   starts from your rules instead of guessing.
4. Watch what it does in **Settings → AI (MCP)**: every tool call it makes is listed with the
   time, the endpoint it touched and whether it worked. If you change your mind, press
   **Revoke** — it stops working immediately.

**Two things worth knowing**

* The assistant must be able to reach your app from the internet, so this only works once
  Step 6 is done and `PUBLIC_BASE_URL` is set. HTTPS is required by ChatGPT and Claude.
* A **read & write** token can really send messages to real people. Until you are comfortable,
  create a read-only one; the AI can still search contacts, read replies and pull analytics.

## When something doesn't work

| What you see | What's wrong | Fix |
|---|---|---|
| **Every page shows an error / an amber notice "Database paused by Neon"** | Neon switched the free database off for the rest of the month because its **100 compute-hours** were used up. Your data is safe. | Open `https://<your-app>.onrender.com/api/v1/health/db` — it tells you the exact reason. See **"Every page shows an error"** below. |
| SMS never arrives on the target phone | Gateway phone offline, out of airtime, or Android killed the app | Check the phone; set Battery → **Unrestricted** (Step 5.6) |
| Replies never show in the Inbox | `PUBLIC_BASE_URL` wrong/blank, so the webhook points nowhere | Redo Step 7, then **Settings → Webhooks → Register webhook** |
| Replies show in Inbox but no Pushover alert | Pushover not enabled in Settings | Step 9; press **Test** |
| Login page won't load / 502 | Free service waking up | Wait 60 seconds and refresh |
| App won't start, log says `sslmode` | `?sslmode=require` left on the URL | Delete `?` and after — Step 2 |
| Campaign says "running" but nothing sends | The worker service is down | Render → `sendsms-worker` → should be green; check its Logs |
| One contact has several chat threads | Old database | Step 8 |

**Reading the logs** (your best tool): Render → the service → **Logs**. Errors are the red
lines. The last few lines before it stopped tell you what happened.

### Every page shows an error

Since this fix the app tells you *why* instead of just failing: an amber notice appears at
the top of every page (including the login screen), and the address
`https://<your-app>.onrender.com/api/v1/health/db` answers with the reason in one line.

The usual reason on the free plan is `quota_exceeded`:

> Your account or project has exceeded the compute time quota. Upgrade your plan to increase limits.

Neon's free plan gives each project **100 compute-hours a month**. When they are gone Neon
suspends the database until the **1st of next month** — every connection is refused, so
every page that needs data fails. **Nothing is deleted.** You have three options:

| Option | Cost | Data | When it works again |
|---|---|---|---|
| Wait | free | kept | 1st of next month, automatically |
| Upgrade the Neon project (Neon → Billing) | paid | kept | immediately |
| Create a **new** free Neon project and paste its address into Render → `sendsms-api` → Environment → `DATABASE_URL` | free | **starts empty** (the old data comes back with the old address after the 1st) | after the next deploy |

To stop it happening again, the app now lets the database sleep when nobody is using it
(open tabs stop refreshing in the background, and the built-in scheduler backs off when
nothing is due). Two more things help a lot:

- **Do not keep an "uptime pinger" pointed at the app.** Every ping keeps the server, and
  therefore the database, awake.
- **Suspend the `sendsms-worker` service** on Render if you are on the free plan: the API
  already does the worker's job (`ENABLE_INLINE_POLLER=true`), and the worker only adds a
  second process querying the database every 30 seconds. Render → `sendsms-worker` →
  Settings → **Suspend**.

---

## Keeping it running

- **The gateway phone is the weak link.** Keep it plugged in, on wifi, with airtime.
  Check it weekly.
- **Watch your database size.** Neon free gives 0.5 GB — plenty for tens of thousands of
  messages, but don't ignore it forever.
- **Back up.** A copy of your code sits in `backups/`. For your *data*, run the `pg_dump`
  command from Step 8 monthly and keep the file somewhere safe.
- **Never share** your notes file, and never paste those values into a chat, screenshot,
  or public repository.

---

## Your notes checklist

By the end you should have filled in all eight:

- [ ] `DATABASE_URL` (with the `?sslmode=...` part deleted)
- [ ] `REDIS_URL`
- [ ] `SECRET_KEY`
- [ ] `CREDENTIAL_ENCRYPTION_KEY`
- [ ] `ADMIN_USERNAME` / `ADMIN_PASSWORD`
- [ ] `SMSGATE_USERNAME` / `SMSGATE_PASSWORD` (freshly regenerated, not the old leaked pair)
- [ ] `SMSGATE_WEBHOOK_SECRET`
- [ ] `PUBLIC_BASE_URL`

---

*More technical detail lives in `DEPLOY.md`. Gateway specifics are in `SMS_GATE.md`.
The health report on the code is `AUDIT.md`.*
