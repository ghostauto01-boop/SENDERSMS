# SENDERSMS


## Set up everything — the in-app guide

Open **Setup Guide** in the sidebar (`/setup`). It is a tutorial for this
deployment, not a copy of the docs: every step is checked against the database
and the environment on each load, so it tells you what *your* install is missing
and in what order to fix it.

Six sections, in the order that matters:

| # | Section | What it checks |
|---|---------|----------------|
| 1 | Foundation | placeholder secrets, `PUBLIC_BASE_URL`, which database is connected, whether background work runs (Celery worker or the inline poller) |
| 2 | SMS (Nigeria) | the SMS-Gate credentials, whether the inbound webhook is registered **and still points at this address**, the SIM slot |
| 3 | Email sending | Brevo senders, which one is default/active, and the inbound-parse webhook URL with its token |
| 4 | Email replies | connected mailboxes, last sync, imported/rescued counts, and the reply-routing report — the freemail `Reply-To` that makes Gmail file a prospect's answer in Spam is named here |
| 5 | AI connectors | one card per client (ChatGPT, Claude, Arena) with the exact MCP URL to paste, whether OAuth is reachable, and how many assistants are connected |
| 6 | Optional | push/email notifications, CallGate, sending rules and compliance, and whether anything has been sent yet |

Each step shows *why it matters* (the symptom you get without it), what the server
currently sees, the values to paste into the other system with a copy button, the
button that opens the screen where it is fixed, and how to verify it afterwards.
The number on the sidebar badge is the count of steps still blocking you; it
drops as each one is fixed.

The same data is available to scripts:

```bash
curl -s -b cookies.txt https://your-app/api/v1/guide           # full checklist
curl -s -b cookies.txt https://your-app/api/v1/guide/summary   # progress only
```

`ready_to_send` in the response is true once nothing required for sending is
missing — the mailbox and the AI connectors are deliberately not part of that,
because they are things you add after your first campaign, not before it.

## Contacts can be phone-only, email-only, or both

A contact needs a phone number **or** an email address — not both. Email-only
lists import cleanly, and when the same person appears in a numbers file and an
addresses file they are **merged into one record carrying both channels**
instead of becoming two half-empty rows.

Email enrichment fills in what is missing, in four stages: clean and diagnose
are free and offline (MX records, typo fixes, dead-domain and shared-inbox
flags); find and verify use the free tiers of Hunter / ZeroBounce / NeverBounce
once you add a key. A pattern-guessed address is stored **unverified**, badged
"Guessed" in the UI, and held back from sends — a bounce costs sender reputation
for every other email the app sends.

Full detail, including the one-line database migration an existing install
needs: [CONTACTS-PHONE-OR-EMAIL.md](CONTACTS-PHONE-OR-EMAIL.md).

## Email (Brevo) — what is wired

The app runs SMS and email side by side. Email reuses the same tables as SMS (a
`conversation` per contact *per channel*), so replies, follow-ups, automations and
campaigns work identically on both:

* **Senders** — unlimited Brevo API keys, each with its own From name/address,
  reply-to, daily cap and open/click tracking. One is the default; campaigns,
  templates and automations can each pick their own, with a fallback for when a
  key is rejected (400/401/402/403/429).
  Press **Check Brevo's verified senders & domains** while adding one — even
  before saving the key — and the app lists the addresses Brevo has already
  verified, badges the ones on an authenticated (SPF/DKIM) domain as *warm*, and
  flags a From address that is not in that list. Sending from a warm, verified
  address is the single biggest deliverability win available.
* **Composer** — plain text, HTML (images by URL, links, formatting, headings,
  bulleted/numbered lists, quotes, **buttons/CTAs**, **tables**, dividers),
  variables (`{{first_name}}` …), attachments (10 files, 4 MB each, 8 MB total),
  CC/BCC and **Send test** — which mails the composer's real content (variables
  rendered, HTML, attachments) to any address first, without creating a contact,
  a thread or any campaign history. HTML-only bodies automatically get a text
  alternative.
* **Threading** — every outgoing mail gets an RFC `Message-ID`; replies quote the
  message they answer (`In-Reply-To`/`References`) and inbound mail is matched
  back to its thread by that chain first, then by subject, then by the contact's
  newest thread.
* **Deliverability** — `GET /api/v1/email/accounts/{id}/deliverability` (the
  Email Manager's **Deliverability** tab) reports live domain authentication from
  Brevo plus bounce/open/click rates and what to fix. Bulk mail carries
  `List-Unsubscribe` + one-click `List-Unsubscribe-Post`, with a signed
  unsubscribe URL (`GET|POST /api/v1/email/unsubscribe`).
* **Receiving** — `POST /api/v1/webhooks/brevo/{account_id}?token=…` handles both
  inbound mail and delivery/open/click/bounce events. Inbound mail keeps its
  attachments: a Brevo download link is shown as a clickable file, inline base64
  is stored, and the message threads under the mail it answers.
* **Engagement, per recipient** — the same visibility Brevo gives you: every
  open and click (with **the URL that was clicked**) is recorded per message and
  per contact. The Email Inbox shows it under each sent message
  (`GET /api/v1/email/messages/{id}/events`) and the contact profile shows that
  person's whole email history (`GET /api/v1/email/contacts/{id}/engagement`):
  open/click/bounce totals, rates, the links they clicked and their recent
  messages — so a prospect who reads but never replies is visible.
* **Follow-ups stay in the chat** — a follow-up (operator follow-up or a
  campaign rule) quotes the newest message in the thread, so it arrives inside
  the same conversation in Gmail/Outlook instead of starting a new one.

## Let an AI run it (MCP)

The app is also a **Model Context Protocol** server, so ChatGPT, Claude or any MCP
client can operate it — import contacts, write and send campaigns, answer the
inbox, read analytics — using the app's own endpoints, rules and consent checks.

* **One connector per client** — ChatGPT, Claude and Arena each get their own
  endpoint, because each vendor's requirements differ and a single generic URL
  fails silently in all three:

  | Client | MCP URL | Auth | Tools |
  | --- | --- | --- | --- |
  | ChatGPT | `/connectors/chatgpt/mcp` | **OAuth 2.1 only** — OpenAI's connector platform accepts no API key, no client credentials and no pasted bearer token | focused set (33) |
  | Claude | `/connectors/claude/mcp` | OAuth 2.1 + PKCE, or `Authorization: Bearer` in Claude Code | full (60) |
  | Arena AI agent | `/connectors/arena/mcp` | bearer token or OAuth; Agent Mode has no connector screen, so it uses curl or the stdio bridge in `tools/arena-connector/` | full (60) |
  | Any other MCP client | `/mcp` | bearer token or OAuth | full (60) |

* **Sign-in happens on this app** — the OAuth 2.1 authorization server is built
  in: dynamic client registration (RFC 7591), PKCE S256, Client ID Metadata
  Documents for ChatGPT, and a login + consent screen that shows exactly which
  permissions are being granted. Discovery is published where the specs say it
  must be (`/.well-known/oauth-protected-resource/<path>`,
  `/.well-known/oauth-authorization-server`, `/.well-known/openid-configuration`),
  and an unauthenticated request gets a real `401` with
  `WWW-Authenticate: Bearer resource_metadata="…"` — the challenge Claude refuses
  to start a flow without.
* **Tokens, for clients that can set a header** — Settings → **AI (MCP)** →
  *Create token*. The token is shown once and stored only as a SHA-256 hash. Send
  it as `Authorization: Bearer <token>` (`?token=<token>` also works for clients
  that cannot set headers).
* **"Run connection test"** — Settings → AI (MCP) replays the exact handshake that
  client performs (discovery → registration → authorize → token → `tools/list` →
  `tools/call`) against this deployment and reports every step, with the fix for
  the first one that fails. That is the answer to "the connector is not working".
* **Scope** — a `read` grant can look at everything and change nothing; a `write`
  grant can do anything the app's UI can. Write tools are refused on a read-only
  grant, including through the escape hatch. Refresh tokens rotate, and replaying
  a rotated one revokes the client.
* **60 tools** in groups: guide, contacts (search/create/update, CSV import and
  export, per-channel consent), lists, templates (+ preview with a real contact's
  values), campaigns (create/update/validate/start/pause/resume/duplicate flags/
  analytics/delete draft), sending (`send_sms_now`, `send_email_now`,
  `send_test_email`), email (senders, inbox, threaded reply, per-contact
  engagement, suppression), unified inbox, follow-ups (per-contact and
  per-campaign), analytics, and `app_api_request` — an escape hatch to *any*
  endpoint, discovered with `list_api_endpoints`.
* **Same rules as the UI** — tools call the app's own REST API in-process, so
  opt-outs, unsubscribes, bounces, sending limits, placeholder-address rejection
  and campaign validation behave identically. A refusal is reported to the
  assistant, not silently skipped.
* **Guide first** — the server tells the assistant to read
  `how_to_use_this_app` (also exposed as the `sendsms://guide` resource) before
  sending: channels, consent rules and the safe order of operations.
* **Audit trail** — every tool call is journalled (tool, endpoint, status,
  duration, error) and shown in Settings → AI (MCP), so the operator can see
  exactly what the assistant did, and revoke its token in one click.

```bash
# What an assistant does, from the outside:
curl -s https://your-app/connectors/arena/mcp \
  -H "Authorization: Bearer mcp_…" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

An Arena agent gets the same thing through `tools/arena-connector/AGENTS.md`: the
curl handshake, a dependency-free stdio↔HTTP bridge for hosts that can only
launch a command, and `mcp.json` / `mcp.http.json` config blocks.

Required environment for the absolute links (webhook URL, unsubscribe URL, OAuth
metadata):

| Variable | Why |
| --- | --- |
| `PUBLIC_BASE_URL` | Builds the Brevo webhook URL, the one-click unsubscribe link and every URL in the OAuth metadata. Without it bulk mail falls back to a `mailto:` unsubscribe and **no AI client can connect** — ChatGPT and Claude both reject a relative or mismatched `resource`. |
| `MCP_OAUTH_ENABLED` | `true` (default). Set `false` to serve bearer tokens only. |
| `MCP_OAUTH_REQUIRE_LOGIN` | Default `true`: the connector's authorization page asks for the app password first, matching the login wall. Set `false` only on a deployment nobody else can reach, to get a one-tap "continue as the operator" button instead. |

## Replies come back into the app

Campaigns leave through Brevo; replies come back through one of two doors, and
Email Manager → **Replies** shows which are open:

* **A connected mailbox** (the usual one). The app reads your Gmail over IMAP with
  an app password, or over the Gmail API with OAuth: it pulls INBOX *and* Spam,
  keeps only mail that answers something this app sent, threads it onto the right
  conversation, and moves the ones Gmail filed as Spam back to the inbox. One-to-one
  replies you write in the app then leave **from that same mailbox**, which is what
  stops the next reply being treated as spoofed. Campaigns stay on Brevo.
* **Brevo inbound parsing** — only for a domain whose MX points at Brevo, which
  needs a domain you own.

Why a reply lands in Spam in the first place: when the From/Reply-To is a freemail
address (`@gmail.com`) on mail Brevo actually sent, Brevo cannot authenticate that
domain, DMARC fails, and Gmail treats the whole thread as spoofed. The prospect
still receives the campaign, but *their reply* is the thing that gets filed away.
The Replies tab says this in plain words, and with the Gmail API connection it can
install Gmail's own **Never send it to Spam** filters (`removeLabelIds: ["SPAM"]`)
for the people who reply.

Bulk mail carries a single small **Unsubscribe** button plus the `List-Unsubscribe`
headers Gmail and Yahoo require; one-to-one mail carries neither, so a personal
reply still reads as personal.

| Variable | Why |
| --- | --- |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Optional. Enables "Connect with Google" (Gmail API) — filters, labels and sending through the API. Without them, connect with an IMAP app password. |
| `GMAIL_POLL_INTERVAL` | Seconds between mailbox checks (default 60). Each mailbox can override it. |
| `GMAIL_RESCUE_FROM_SPAM` | `true` (default) moves recognised replies out of Spam. |
| `GMAIL_SEND_REPLIES` | `true` (default) sends one-to-one replies from the connected mailbox instead of Brevo. |

### Local simulator (no real sends)

```bash
# 1. A stand-in Brevo that records every payload it receives
python -m uvicorn tools.fake_brevo:app --port 8799

# 2. The app, pointed at it
cd backend && DATABASE_URL="sqlite+aiosqlite:////tmp/sim.db" APP_ENV=development \
  BREVO_API_BASE="http://127.0.0.1:8799/v3" PUBLIC_BASE_URL="http://127.0.0.1:8000" \
  python -m uvicorn app.main:app --port 8000

# 3. Drive the whole channel: senders, rich send, threaded reply, attachments,
#    one-click unsubscribe, campaign
python tools/simulate_email_flow.py

# 4. Prove nothing else broke: every parameterless GET endpoint
python tools/smoke_all_endpoints.py

# 5. Drive the app the way an AI assistant does (token → JSON-RPC → real
#    endpoints): handshake, import, template, campaign, send, inbox, guard rails
python tools/mcp_smoke.py
```

`BREVO_API_BASE` only exists so a test can point at that stand-in; production never
sets it. `tools/run_campaign_now.py <id>` runs one campaign batch inline, the same
code path the web-mode fallback uses when no Celery worker is available; it (and
`tools/run_followup_now.py`) loads the app's own `.env` first, so it shares
`CREDENTIAL_ENCRYPTION_KEY` with the server and can read the API keys the server
wrote.

### Browser walkthrough (a real user, through the real login)

```bash
npm run dev -- --host 0.0.0.0 --port 5173     # in one terminal
node tools/e2e_walk.mjs                       # in another
```

It types a wrong password (and insists it is refused), signs in with
`APP_PASSWORD` (default `12345678`), visits every page in the app, and fails if a
page throws, renders an error state, or overflows a 390px phone screen. It also
follows an email notification from the bell into its thread. `node tools/shot.mjs
<url> <out.png> [w] [h] [ms] [--login]` saves a single screenshot instead.

Both tools use the `puppeteer-core` + `@sparticuz/chromium` dev dependencies; on a
machine without the bundled Chromium, set `CHROME_PATH` to a local Chrome.
