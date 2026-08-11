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
3. **You must edit it.** Change the beginning from `postgresql://` to
   `postgresql+asyncpg://`, and delete `?sslmode=require` from the end. Result:
   ```
   postgresql+asyncpg://neondb_owner:AbC123xyz@ep-cool-name.eu-central-1.aws.neon.tech/neondb
   ```

> **Why:** the app talks to the database in a fast "async" way and needs that exact prefix.
> Getting this wrong is the single most common reason the app won't start.

**Save two versions in your notes:**
- `DATABASE_URL` = the edited one (`postgresql+asyncpg://...`) — for the app.
- `PLAIN_DATABASE_URL` = the original one Neon gave you — you'll need it in Step 8.

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

# CREDENTIAL_ENCRYPTION_KEY — encrypts saved gateway passwords (must be this exact format)
openssl rand -base64 32
```

**Save in notes:** `SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`

Also decide now:
- `ADMIN_USERNAME` — your login name, e.g. `admin`
- `ADMIN_PASSWORD` — **make this strong.** This is the front door to your whole system.

> This account is created once, the first time the app starts. Changing these values later
> will *not* change your password — you'd change it inside the app.

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
3. Choose your `SENDERSMS` repository. **Select the branch `arena/019fedaf-sendersms`.**
4. Render reads the `render.yaml` file and offers to create **two services**:
   - `sendsms-api` — the website and control panel
   - `sendsms-worker` — the background helper that sends campaigns on schedule
5. Render will ask you to fill in the blanks. Enter these on **both** services:

   | Setting | Value |
   |---|---|
   | `DATABASE_URL` | your `postgresql+asyncpg://...` from Step 2 |
   | `REDIS_URL` | from Step 3 |
   | `SECRET_KEY` | from Step 4 |
   | `CREDENTIAL_ENCRYPTION_KEY` | from Step 4 |
   | `ADMIN_USERNAME` | from Step 4 |
   | `ADMIN_PASSWORD` | from Step 4 |
   | `SMSGATE_USERNAME` | from Step 5 |
   | `SMSGATE_PASSWORD` | from Step 5 |
   | `SMSGATE_WEBHOOK_SECRET` | from Step 5 |
   | `PUBLIC_BASE_URL` | leave blank for now — Step 7 |
   | `CORS_ORIGINS` | leave blank for now — Step 7 |

   > ⚠️ **`SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY` must be *identical* on both
   > services.** Render offers to auto-generate them on the api service — don't accept
   > that. Paste your own, the same value in both places. If they differ, the worker
   > cannot read the gateway password and campaigns fail silently.

6. Click **Apply** / **Create**. Wait 5–10 minutes for the first build.
7. When `sendsms-api` goes green, copy its web address from the top of the page, e.g.
   `https://sendsms-api.onrender.com`

   **Save in notes:** `PUBLIC_BASE_URL`

**If the build fails**, click the **Logs** tab and read the last red lines:
- `Invalid argument(s) 'pool_size'` → your `DATABASE_URL` is missing `+asyncpg`. Step 2.
- `password authentication failed` → the database password is wrong; re-copy from Neon.
- Build ran out of memory → retry; the free tier is occasionally short on RAM.

---

## Step 7 — Connect the phone to the app

Now the app knows its own address, so tell it.

1. In Render, open **sendsms-api** → **Environment**. Set:
   - `PUBLIC_BASE_URL` = the address from Step 6, no trailing slash
     (`https://sendsms-api.onrender.com`)
   - `CORS_ORIGINS` = the same address
2. Do the same on **sendsms-worker**. **Save changes** — both services restart.

> **Why this matters:** on startup the app automatically tells SMS-Gate *"send new messages
> to `<your address>/api/v1/webhooks/smsgateway`"*. If the address is blank or wrong, the
> registration points nowhere and **incoming SMS never arrive** — with no error shown.
> The address must be `https://` with a valid certificate; your Render address already is.

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

Using the **plain** database URL you saved in Step 2 (the `postgresql://` one, *not*
`+asyncpg`):

```bash
# 1. Back up first — always
pg_dump "PASTE_PLAIN_DATABASE_URL_HERE" > backup-before-migration.sql

# 2. Run the migration
psql "PASTE_PLAIN_DATABASE_URL_HERE" -f scripts/migrate_existing_db.sql
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

## When something doesn't work

| What you see | What's wrong | Fix |
|---|---|---|
| SMS never arrives on the target phone | Gateway phone offline, out of airtime, or Android killed the app | Check the phone; set Battery → **Unrestricted** (Step 5.6) |
| Replies never show in the Inbox | `PUBLIC_BASE_URL` wrong/blank, so the webhook points nowhere | Redo Step 7, then **Settings → Webhooks → Register webhook** |
| Replies show in Inbox but no Pushover alert | Pushover not enabled in Settings | Step 9; press **Test** |
| Login page won't load / 502 | Free service waking up | Wait 60 seconds and refresh |
| App won't start at all | `DATABASE_URL` prefix | Must be `postgresql+asyncpg://` — Step 2 |
| Campaign says "running" but nothing sends | The worker service is down | Render → `sendsms-worker` → should be green; check its Logs |
| One contact has several chat threads | Old database | Step 8 |

**Reading the logs** (your best tool): Render → the service → **Logs**. Errors are the red
lines. The last few lines before it stopped tell you what happened.

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

- [ ] `DATABASE_URL` (with `+asyncpg`)
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
