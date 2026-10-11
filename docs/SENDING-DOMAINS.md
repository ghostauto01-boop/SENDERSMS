# Sending domains: optional DNS guidance

Domain authentication is useful for deliverability, but it is **not an app-side campaign launch requirement**. Campaigns continue to use the existing configured email sending route. The app does not reject a sender or prevent a launch just because its From address is a free-mail address, or because a sending domain is missing or not authenticated. The provider's normal response at send time determines whether the message is accepted.

A Brevo sender still needs a usable account/API key and a syntactically valid From address. Recipient eligibility, opt-outs, and other existing campaign checks are unchanged.

## Optional domain setup

`POST /api/v1/email/domains {"domain": "acme-leads.io", "rua": "dmarc@acme-leads.io"}` registers a domain you control with Brevo and returns the DNS records to publish: the Brevo verification code, DKIM, and **one** DMARC record. This helper only accepts a domain you control; it does not restrict which configured From address the campaign can use. `GET /email/domains[/{id}]` reads the plan back; `POST /email/domains/{id}/verify` asks Brevo to check it and starts the optional warm-up clock when first authenticated.

A free-mail domain such as `gmail.com` cannot be registered for DNS setup because its DNS is not controlled by the sender. That restriction applies only to the domain-registration helper; it does not prevent saving or launching with a valid From address through the configured email sender.

## Exactly one DMARC record

A domain may publish one DMARC TXT record at `_dmarc.<domain>`; with two, receivers treat DMARC as absent. The app reads what is already there and merges the requested `rua` into it (every other tag is kept and a policy is never weakened). The plan's note says **REPLACE the existing record**, never add a second. If a domain already has several, they are merged into one and `verify` warns you to delete the extras. If DNS cannot be read, the plan says so instead of assuming nothing is published.

## Optional warm-up

For a domain registered and authenticated through this helper, the app can display a warm-up schedule: about 5 messages a day in week one, 10 in week two, 18 in week three and 25 from week four (`services/warmup.py`). Domain-authentication status itself does not prevent campaign launch. Sending capacity and the account's configured sending rules continue to apply as usual.

MCP tools: `register_sending_domain`, `list_sending_domains`, `verify_sending_domain`.

## Checking DKIM yourself

Whether a receiver actually reports `dkim=pass` can only be seen by sending real mail to a real mailbox, which requires Brevo credentials and a published domain. After `verify` says `authenticated`:

1. Send a test email from the sender (`POST /email/accounts/{id}/test-send`) to an address you control.
2. In Gmail, open the message → ⋮ → **Show original**.
3. Check the reported SPF, DKIM, and DMARC results.

## Not done

* **Custom tracking domain** — not implemented. It needs a CNAME target that only Brevo's account settings show, which the API does not expose.
* **Live DKIM integration test** — needs a real send through Brevo to a real mailbox.
* The Brevo calls (`POST /senders/domains`, `PUT …/authenticate`) are covered by mocks; **they have not been run against a live Brevo account.**
