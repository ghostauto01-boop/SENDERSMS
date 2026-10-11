# Sending domains: a sender the internet will deliver for

## The rules

1. **No free-mail senders.** A From address on gmail.com, yahoo.com, outlook.com, icloud.com … cannot be
   authenticated through Brevo (the app does not control that domain's DNS), so the mail fails DMARC and is
   junked or bounced. Creating or changing a sender to one is a **422**, `POST /email/domains` for one is a 422,
   and a campaign from an account that predates the rule cannot start.
2. **A sending domain is an object.** `POST /api/v1/email/domains {"domain": "acme-leads.io", "rua": "dmarc@acme-leads.io"}`
   registers it with Brevo and returns the DNS records to publish: the Brevo verification code, DKIM, and **one** DMARC
   record. `GET /email/domains[/{id}]` reads it back; `POST /email/domains/{id}/verify` asks Brevo to check, re-reads
   your DNS, and the first time the domain is authenticated starts the warm-up clock.
3. **Exactly one DMARC record.** A domain may publish one DMARC TXT record at `_dmarc.<domain>`; with two, receivers
   treat DMARC as absent. So the app *reads* what is already there and merges your `rua` into it (every other tag is
   kept, a policy is never weakened); the plan's note says **REPLACE the existing record**, never add a second. If a
   domain already has several, they are merged into one and `verify` warns you to delete the extras. If DNS cannot be read from
   here the plan says so instead of assuming nothing is published.
4. **Warm up.** Once a domain is authenticated each of its mailboxes may send about 5 a day in week one, 10 in week two,
   18 in week three and 25 from week four (`services/warmup.py`). This is enforced in the sending gate below the per-mailbox
   default in Sending Rules, and `validate` projects the campaign's duration with it.
5. **A campaign needs a ready sender.** `validate`/`start` (classic) and `launch` (Ads Manager) refuse a free-mail sender,
   and a domain that is registered but not authenticated. A domain nobody registered is a *warning* (existing installs
   keep working), with a pointer to register it.

MCP tools: `register_sending_domain`, `list_sending_domains`, `verify_sending_domain`.

## Checking DKIM yourself (this was NOT automated)

Whether a receiver actually reports `dkim=pass` can only be seen by sending real mail to a real mailbox, which needs Brevo
credentials and a published domain. After `verify` says `authenticated`:

1. Send a test email from the sender (`POST /email/accounts/{id}/test-send`) to a Gmail address you control.
2. In Gmail open the message → ⋮ → **Show original**.
3. You want `SPF: PASS`, `DKIM: PASS` with `d=<your domain>` (not brevo's), and `DMARC: PASS`.

## Not done

* **Custom tracking domain** — not implemented. It needs a CNAME target that only Brevo's account settings show, which the API does not expose.
* **The DKIM=pass integration test** — see above.
* The Brevo calls (`POST /senders/domains`, `PUT …/authenticate`) are written from Brevo's documented shape with lenient parsing and are
  covered by mocks; **they have not been run against a live Brevo account.**
