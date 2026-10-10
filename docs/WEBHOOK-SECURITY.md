# Webhook tokens and other secrets in responses

## What was wrong

The Brevo webhook token is the credential for `POST /api/v1/webhooks/brevo/{account_id}`:
whoever holds it can inject fake inbound mail into the inbox and fake bounces onto real
contacts. Every account read (`GET /email/accounts`, the setup guide, every MCP tool that
lists senders) returned it in full, as `webhook_url` and `webhook_path`, so it travelled into
logs, HAR files, screenshots and AI-assistant transcripts. The SMS gateway's device id leaked
the same way from `/inbox/device-info` and `/inbox/poll-debug`.

## What it does now

* **Reads show a hint.** `webhook_url`, `webhook_path` and the setup guide's copy row carry
  `token=****a1b2`; `webhook_token_masked` is the hint on its own. A gateway device's `id` is
  reduced to the same kind of hint (the rest of the record is unchanged).
* **One deliberate action returns the real URL:** `POST /email/accounts/{id}/webhook/reveal`
  (the dashboard's *Copy webhook URL* button, and the setup guide's copy button, call it on
  click). The response is `Cache-Control: no-store` and the request is logged.
* **Rotation:** `POST /email/accounts/{id}/webhook/rotate` issues a new 256-bit token. **The old
  token stops working at once**, so paste the returned URL into Brevo straight away: until you do,
  Brevo's deliveries are refused with 403. (Brevo's retry behaviour for refused deliveries was not
  verified.) The dashboard has a *Rotate token* button with a confirmation.
* **The assistant bridge cannot reveal or rotate.** The MCP layer marks its in-process requests
  (`X-Sendsms-Via: mcp`) and both endpoints refuse them with `403
  SECRET_NOT_AVAILABLE_TO_ASSISTANTS`. This includes the generic `api_request` tool, which can
  reach any path.
* **Verification** uses `hmac.compare_digest` (constant time) on UTF-8 bytes, so a long or
  non-ASCII guess is a clean 403, never a 500. The token is also accepted in a header --
  `X-Webhook-Token: <token>` or `Authorization: Bearer <token>` -- which keeps it out of access
  logs and Referer headers; the query form stays because Brevo can only be given a URL.
* New tokens are `secrets.token_urlsafe(32)` (they were `uuid4().hex`).

## Not done

* HMAC-signing of webhook payloads: Brevo does not sign its transactional webhooks, so there is
  nothing to verify; the SMS gateway webhook already has a signature check
  (`SMSGATE_WEBHOOK_SECRET`).
* Other secrets-in-reads (AI provider keys, MCP tokens) were not audited in this change.
