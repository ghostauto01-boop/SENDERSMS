# Audit 3 — Duplicate, Edit & Guaranteed Delivery

Scope: add campaign duplication and post-creation editing, guarantee the campaign
still sends after those changes, then run the whole app in a simulator and fix
everything it turned up.

Verified with an end-to-end simulator: a mock SMS-Gate cloud API, a real Redis
broker, a real Celery worker and the real FastAPI app — no mocked internals.

**Result: all simulation checks pass. 210 backend tests pass (206 before + 4 new).
TypeScript and production build clean.**

---

## The serious one: most of your campaign never sent

This was already in the app before this task and would have hit you on every
real campaign.

Campaign sending queued the background job **before saving the message to the
database**:

```
create message row (uncommitted)
enqueue send_sms(message.id)      <-- worker can start here
...
commit                            <-- row only becomes visible now
```

The worker often won that race. It looked up the id, found nothing, and — because
a missing row was treated as "nothing to do" — returned **success** and moved on.
No error, no failed count, no retry. The contact was simply never texted.

Observed in the simulator with 3 contacts: **1 delivered, 2 silently dropped.**
The campaign reported itself as running and healthy. The bigger the batch and the
faster the worker, the more messages vanish, which is exactly backwards from what
you'd want.

Both halves are fixed:

- **Producer** — sends are collected in an outbox and published only *after* the
  transaction commits. If the broker is unreachable, the contact goes back to
  `pending` and the phantom message row is deleted, so the next run retries it
  rather than reporting a send that never left.
- **Consumer** — a not-yet-visible message id now raises and retries with a short
  backoff instead of being swallowed.

After the fix: **3 of 3 sent, 3 of 3 delivered**, confirmed against the gateway's
own received-message log, repeated from a clean database.

## Duplicate a campaign

- Copies dropped `message_body`, so **every copy was unsendable** — it would fail
  validation with "No message to send". Copies now carry the message, list,
  limits and gateway, and reset their stats to zero.
- Copies are named `(Copy)`, then `(Copy 2)` — no duplicate names.
- A copy of a *sent* campaign comes back as a fresh draft and sends correctly
  (verified: `sent=3`).
- The frontend threw the duplicate response away; it now uses it.

## Edit a campaign

- `PUT /campaigns/{id}` had no status guard — you could mutate a running
  campaign mid-flight. Edits are now allowed in `draft`/`scheduled` only;
  anything else returns **409** with *"Cannot edit a campaign that is running.
  Duplicate it to make changes."*
- Editing a `scheduled` campaign demotes it to `draft` and clears
  `scheduled_at`, so it can't fire with half-applied changes.
- `message_body` and `template_id` now clear each other; changing the list
  resets `total_contacts`.
- `CampaignUpdate` was missing `sequence_id` and `gateway_setting_id`.
- The UI had no edit path at all — one modal now handles create and edit.

## Message variables

Typing `{first_name}` (single braces) instead of `{{first_name}}` sent the raw
text `{first_name}` **to the customer**. Single braces now resolve for known
contact fields. Anything else — `{50}`, `{a:1}`, `{5,000}`, `{unknown}` — is left
exactly as typed, so ordinary punctuation is never mangled.

## Also fixed along the way

- Counters (`messages_sent` / `failed`) moved at *enqueue* time, so a campaign
  reported a full send even when the gateway rejected every message. They now
  move only on a real gateway outcome.
- `retrying` was a dead end — nothing ever re-queued those messages, so a
  transient gateway blip stranded the contact forever.
- `CampaignContact` rows were never settled, so campaigns could never complete.
  The lifecycle is now `pending → queued → sent → delivered/failed`, and a
  campaign auto-completes once every contact is terminal (verified).

## Checkup of the rest of the app

Exercised against the live simulator: auth, campaigns, contacts, lists,
templates, inbox, dashboard, sequences, all settings endpoints, gateway webhook
config and webhook logs — **all 200**. Dashboard totals reconcile exactly with
the gateway (6 sent / 6 delivered). Gateway password is masked in API responses.
Inbound replies land in the inbox and mark the contact as replied. The full
webhook lifecycle (`sms:sent` → `sms:delivered`, signature-verified) matched
every message.

No further defects found.

---

## Still needs you (carried over, unchanged)

1. **Rotate the Upstash Redis password** — the old one was exposed and is still live.
2. **Run `scripts/migrate_existing_db.sql`** against the production database
   before the next deploy — there is no Alembic, and `message_body` is a new column.
3. Merge PR #2.
4. Keep an off-machine copy of the database backup.
5. Set the SMS Gate app's battery mode to **Unrestricted** on the phone.
