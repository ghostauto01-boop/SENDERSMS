# Email verification: unknown is not a yes

A campaign mails an address only if something has shown the address exists. Before P0-5 an
address nobody had checked was as sendable as one a verifier had confirmed, so a list that was
mostly generated role mailboxes (info@, sales@) and already-bounced addresses went straight to
a brand-new sending mailbox.

## The states

`email_verdict` on the contact (with `email_verified`, `email_verified_at`, `email_confidence`):

| state | meaning | outreach (campaigns, Ads Manager, follow-ups, bulk mail) |
|---|---|---|
| `deliverable` (`status: valid`, confidence 95) | the mailbox was confirmed on a non-catch-all domain | sent |
| `risky` (`status: risky`, confidence 60) | a catch-all domain, or a role / disposable mailbox | sent **last**, after every deliverable address |
| `undeliverable` (`status: invalid`, confidence 0) | no MX, bad syntax, or the server rejected the mailbox | never (quarantined) |
| `unknown` (`status: unknown`) | never checked, or the verifier could not decide (SMTP blocked, timeout) | **never** |

`unknown` never quarantines an address and never undoes what an earlier check proved (one
timeout does not un-verify a deliverable address). It is simply not a yes. Editing a contact's
address clears its verdict, because what was learned about the old address says nothing about
the new one.

## Where the rule is applied

One function decides (`email_service._static_problem` + `_verification_problem`), and the audience
screening and the send path both call it, so a validation report promises numbers the send path
keeps:

* `POST /campaigns/{id}/validate` **fails** when the list contains unknown contacts, with the
  count (`audience.unverified`), and `start` / the scheduler's launch refuse it. Risky contacts are
  counted and warned about, not failed.
* Ads Manager `validate` fails the same way, and launch assigns only eligible contacts.
* Audience population is set-based and queues deliverable contacts before risky ones.
* At send time a campaign message to an address that became unverified after the audience was built
  (the address was edited) is refused, not sent.
* **Not gated:** a one-to-one message, a reply, an address the person wrote to us from, and test
  sends. `EMAIL_REQUIRE_VERIFIED=false` turns the gate off for a closed test environment.

## Verifying a list

```
POST /api/v1/validator/jobs        {"scope": "list", "list_id": 7}      -> 202, a job
GET  /api/v1/validator/jobs/{id}   status, processed/total, valid/invalid/risky/unknown
POST /api/v1/validator/jobs/{id}/cancel
```

A job is a durable row: it snapshots the contacts, checks them in chunks of 25 (10 at a time),
**writes every verdict onto the contact as it goes**, and resumes where it stopped after a restart.
`scope` is `all`, `list` or `ids` (with the same filters as the contact list); contacts without an
address, and (unless `recheck`) ones already proven deliverable, are left out of `total`.
`POST /validator/batch` still takes up to 25 rows (was 5) for a quick, read-only check.

MCP: `verify_contacts`, `verification_job`, `validator_status`.

## Can this server confirm a mailbox at all?

Confirming a mailbox needs an SMTP conversation on port 25, which most hosts block.
`GET /validator/status` therefore reports `smtp_enabled` truthfully, with `smtp_reason`:

* `REACHER_API_URL` set: a Reacher service makes the SMTP connection, so `smtp_enabled: true`.
* `EMAIL_VALIDATOR_SMTP=false` and no Reacher: `false`, and the reason says so.
* Otherwise the server connects to a public mail server on port 25 and waits for the `220`
  greeting (a bare TCP connect proves nothing: some networks accept it and then close the
  connection with no banner). No greeting: `false`, with what was seen and what to do.

When it is `false`, a job still runs a DNS-only pass (syntax, MX) and records `smtp_reason`; every
mailbox stays `unknown`, which means **nothing becomes sendable until a Reacher service or an open
port 25 is available**. That is the intended outcome, not a bug.
