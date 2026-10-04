# SENDERSMS


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

Required environment for the absolute links (webhook URL, unsubscribe URL):

| Variable | Why |
| --- | --- |
| `PUBLIC_BASE_URL` | Builds the Brevo webhook URL and the one-click unsubscribe link. Without it bulk mail falls back to a `mailto:` unsubscribe. |

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
```

`BREVO_API_BASE` only exists so a test can point at that stand-in; production never
sets it. `tools/run_campaign_now.py <id>` runs one campaign batch inline, the same
code path the web-mode fallback uses when no Celery worker is available.
