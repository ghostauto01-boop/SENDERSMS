# Contacts: phone, email, or both

Contacts used to be phone numbers. This release makes the **email address an
equal way to identify and reach a person**, without weakening the SMS side that
already worked.

The rule, in one line:

> A contact needs a phone number **or** an email address. Not both.

---

## What changed

| Before | Now |
|---|---|
| `contacts.phone_number` was `NOT NULL` | Nullable — an email-only contact stores `NULL` |
| A CSV row with no phone was rejected as *invalid* | Imported as an email-only contact |
| The import modal disabled Import unless a Phone column was mapped | Accepts Phone **or** Email |
| A re-imported duplicate was thrown away | **Merged** into the existing contact |
| Enrichment: none | Four stages, two of them free and offline |

Everything that was true about phone-only contacts is still true: they import,
they send, they campaign. Nothing about the SMS paths changed.

---

## Importing

A row is valid when it carries at least one usable identifier:

| Row has | Result |
|---|---|
| Phone only | Imported, SMS-reachable, skipped by email sends |
| Email only | Imported, email-reachable, skipped by SMS sends |
| Both | Imported with both channels |
| Neither | Reported invalid (`No phone number and no email address`) |

The result screen and the API both report the split — `phone_only`,
`email_only`, `both` — so it is obvious *before* a campaign that 400 of your 500
new contacts cannot receive an SMS.

### The merge

When a row matches a contact that already exists, the row is folded into that
contact instead of being discarded:

* **matched by phone** → the strongest identifier
* **matched by email** → including an address parked as a reply alias
* **matched by full name** → only when exactly one contact matches *and* that
  contact is missing the thing this row supplies

The name match exists because the real workflow is two files: one with numbers,
one with addresses. They share no phone and no address, so without it you end up
with two half-empty rows per person. It is deliberately timid — an ambiguous
name (two "Ada Obi"s) imports as a new contact rather than absorbing somebody
else's record, a lone first name never matches, and every name-based merge is
counted separately as `merged_by_name` and noted in the import report.

**A merge never clears a populated field.** Re-uploading a numbers-only file
cannot blank the addresses already on file.

---

## Enrichment

Four stages, cheapest first. Stages 1–2 need no account and no key.

| Stage | What it does | Needs |
|---|---|---|
| 1 · clean | Normalises the address, strips CSV junk (`<a@b.com>,`) | nothing |
| 2 · diagnose | MX records, typo fix (`gmial.com` → `gmail.com`), disposable / shared-inbox flags | `dnspython` |
| 3 · find | Looks up the address for a named person at a known domain | `HUNTER_API_KEY` |
| 4 · verify | Proves the mailbox exists and records the verdict | `ZEROBOUNCE_API_KEY`, `NEVERBOUNCE_API_KEY` or Hunter |

### The safety rule

**A guessed address is stored, marked unverified, and held back from sends.**

Guessing (`EMAIL_ENRICHMENT_ALLOW_INFERRED`, off by default) produces
`ada.obi@company.com` from a name and a domain. It is stored with
`email_source = 'inferred'`, `email_verified = false`, and `email_confidence =
30`, and the UI labels it **Guessed**.

It is then *held back from every send path* — campaigns, follow-ups, scheduled
and direct sends all run through one gate, and a guessed address fails it. The
contact keeps the address and stays visible (the `Guessed (inferred)` filter
lists it), so it can be reviewed and corrected; it just is not emailed until
someone confirms it or the operator deliberately sets `EMAIL_SEND_INFERRED=true`.

An address the *user* supplied is a different thing and is never blocked by this
rule — imports are sent to as normal, verified or not. The rule exists because a
bounce is not a cosmetic problem: it costs sender reputation for every other
email the deployment sends.

A verifier can also *reject* an address — that is stored as
`email_status = 'invalid'` and `is_email_undeliverable = true`, which every
email send path already skips.

A provider error (missing key, exhausted free tier, network blip) is recorded as
a note on the contact. It never marks the address bad.

### Free tiers

| Provider | Free allowance | Used for |
|---|---|---|
| Hunter.io | ~25 searches, ~50 verifications / month | find + verify |
| ZeroBounce | ~100 credits | verify |
| NeverBounce | ~1,000 one-off credits | verify |

Enough to trial, not enough to enrich 10,000 rows in one click — so
`POST /contacts/enrich` caps each run at 500 contacts by default and reports
`capped: true` when it hits the ceiling. Quota exhaustion surfaces as
`N unavailable` in the UI rather than silently failing rows.

---

## Where to find it

* **Contacts page** → `Enrich emails` button (enabled once a key is configured)
* **Contacts page** → the `Email` filter: `Verified`, `Unverified`,
  `Guessed (inferred)`
* **Contacts page** → every address carries a `Verified` / `Unverified` /
  `Guessed` / `Invalid` badge
* **API** → `GET /contacts/enrich/status`, `POST /contacts/enrich`

Without a provider key the button is disabled with a tooltip explaining why, and
everything else — import, merge, cleaning, diagnosis — still works.

---

## Deploying this to an existing database

An existing database has `contacts.phone_number NOT NULL`, which would reject
every email-only insert. Two mechanisms handle it, and either is sufficient:

1. **Automatic.** `schema_repair.py` runs at startup and issues
   `ALTER TABLE contacts ALTER COLUMN phone_number DROP NOT NULL`. SQLite cannot
   alter a constraint in place, so there the table is rebuilt on the same boot —
   keeping every other column's type, nullability and default exactly as they
   were, so a legacy table whose later-added columns hold NULL (the normal case)
   is not a problem. It also adds the seven enrichment columns. It only ever
   *removes* a constraint — no row is read, rewritten or dropped.
2. **Manual.** `scripts/migrate_existing_db.sql` carries the same statements plus
   the indexes and a backfill of `email_lower` for pre-existing addresses.

If the auto-repair cannot run (a restricted database role), the log says so
explicitly and email-only contacts will fail to insert until the script is run
by hand.

### Verifying after deploy

```sql
-- phone_number should be nullable ('YES' in is_nullable on PostgreSQL)
SELECT column_name, is_nullable FROM information_schema.columns
 WHERE table_name = 'contacts' AND column_name = 'phone_number';
```

Then import a two-row CSV with an `Email` column and no `Phone` column: the
result should read `imported: 2`, `email_only: 2`, `invalid: 0`.

---

## Configuration

```ini
EMAIL_ENRICHMENT_ENABLED=true          # master switch
EMAIL_ENRICHMENT_USE_PROVIDERS=true    # false = free stages only, no network calls
EMAIL_ENRICHMENT_ALLOW_INFERRED=false  # guesses are unverified by definition
EMAIL_ENRICHMENT_KEEP_PRIMARY=true     # a found address becomes a reply alias
EMAIL_SEND_INFERRED=false              # guessed addresses are held back from sends
HUNTER_API_KEY=
ZEROBOUNCE_API_KEY=
NEVERBOUNCE_API_KEY=
```

---

## Tests

* `backend/tests/test_contacts_email_and_phone.py` — import rules, the merge, the
  API identity rule, the filters, the enrichment endpoints
* `backend/tests/test_email_enrichment.py` — cleaning, diagnosis, provider
  verdicts, and the rule that a guess is never marked verified
* `backend/tests/test_phone_optional_migration.py` — a legacy `NOT NULL` database
  is migrated and then accepts an email-only contact
* `frontend/src/utils/contact.test.ts` — channel display and trust badging,
  including that an email-only contact never renders as `null`

The campaign tests in `test_contacts_email_and_phone.py` also pin the channel
split: an **email** campaign reaches an email-only contact, an **SMS** campaign
still skips it, and an email opt-out never blocks an SMS (they are two
consents).
