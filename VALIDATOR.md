# Validator

## Where to go

Open **Validator** in the sidebar, or **More → Validator** on a phone.
The Contacts toolbar opens the same page with its selected contacts or current
filters. The list editor's **Validator** action opens it for that list.

1. Pick **Current contacts**, **Contact list**, **CSV file**, or **Single contact**.
2. Choose email, phone, or both. Phones are checked against this app's Nigerian
   mobile-number rules; an international number is not called globally invalid,
   but is unsuitable for this app's Nigerian SMS route.
3. For email, choose **Quick** (syntax, DNS, disposable/role flags) or **Deep**
   (also attempt a live mailbox check). Quick checks cannot prove a mailbox exists.
4. Press **Start validation**. Existing contacts are checked across *every* page,
   not limited to the old 200/500-contact preview. IDs are snapshotted at the
   start, so changes to a filter's matching population cannot skip later contacts.
5. Click a result to inspect both channels, reasons, suggested corrections,
   normalization, DNS/SMTP/catch-all flags, provider, and check time.
6. Use the Good/Bad/Risky/Unknown/Missing buttons to filter. **Export shown**
   downloads *all* matching results, not just the visible page; **Export all**
   includes every completed result and its original input. Exported cells are
   protected against spreadsheet formula injection.

CSV uploads support column mapping, quoted cells, UTF-8 BOM, common delimiters,
and a headerless mode. Leading zeros in numbers are preserved. Up to 10 MB and
50,000 data rows per file; larger files are rejected with an explanation, never
silently truncated. Invalid/missing data rows are reported, not discarded.
CSV and single-contact checks **do not create contacts**. To add reviewed data,
export the desired results and use Contacts → Import with the column mapping.

Runs process small batches with live progress. **Stop after batch** keeps completed
results; **Resume remaining** continues the same frozen selection and options.
Errors preserve completed results and allow the failed batch to be retried.
Keep the page open; a navigation/reload ends this browser-run session. Export
before leaving: there is no persisted validation-run history. An in-flight batch
may finish after leaving the page (including opt-in saved flags).

## What the results mean

| Result | Meaning |
| --- | --- |
| **Good** | Email: exact mailbox accepted by Reacher or by SMTP with a negative catch-all check. Phone: valid Nigerian mobile format, **not** proof that the SIM is active or can receive a message. |
| **Bad** | At least one checked field has confirmed bad data (e.g. invalid syntax, nonexistent domain/null MX, explicit unknown-mailbox rejection, invalid/unsupported phone format). A good second channel is still shown. |
| **Risky** | Review first: disposable/role mailbox, catch-all domain, inconclusive catch-all check after mailbox acceptance, or suspected sample phone data. |
| **Unknown** | Could not prove the result. Examples: quick-mode mailbox not checked, DNS timeout/SERVFAIL, blocked SMTP, temporary/policy refusal, or provider failure. Never interpreted as a hard bounce. |
| **Missing** | No value for the chosen check. Phone-only and email-only contacts remain valid records. |

Sending restrictions (opt-out, unsubscribe, suppression, previous bounce or
quarantine) are displayed separately. **Good does not mean permission to send.**
No validation can guarantee future delivery, mailbox ownership, or consent.
The validator never sends an SMS or an email body; deep built-in checks use SMTP
RCPT commands without DATA.

## Does it need an API key?

**No key is required for the built-in engine.** `phonenumbers` and `dnspython`
are already in `requirements.txt`. The engine status card shows the installed
DNS capability, the SMTP setting and whether Reacher is configured. These are
configuration checks, not a claim that external networking is reachable.

- `EMAIL_VALIDATOR_SMTP=true` allows built-in SMTP probing.
- `EMAIL_VALIDATOR_SMTP=false` disables it; syntax/DNS/risk checks still work.
- Many managed hosts block outbound TCP port 25 or are rejected by mail servers.
  Those mailbox checks correctly return **Unknown**. You do not need to add a
  key just to use the Validator page.
- Optionally set `REACHER_API_URL` to your complete Reacher `check_email` endpoint
  on the API server. A self-hosted instance only needs an authentication key if
  configured that way. A hosted Reacher provider may require its own key/quota.
- If needed by that provider, set `REACHER_API_KEY` in the server environment.
  Credentials are never returned to the browser. The client uses the existing
  Bearer authentication integration. Provider errors fall back to built-in checks
  and the per-row explanation identifies the fallback.
- Hunter/ZeroBounce/NeverBounce keys used by **email enrichment** are separate
  and not required for this page's built-in validation.

After changing server environment variables, redeploy/restart the API service.

### How to prove the engine is working

Click **Run engine self-test**. It runs the actual validator functions against
malformed email, a reserved `.invalid` domain, a valid-format Nigerian mobile
and a short invalid number. It makes no external calls and changes no contacts.
The UI explicitly labels this a **local** self-test; it does not verify Reacher
credentials, live DNS/SMTP connectivity or real delivery. Use a single address
in Deep mode to inspect the real network/provider result separately.

## Safe writes

**Save results to existing contacts** is off by default and requires a
confirmation when enabled:

- Only confirmed-undeliverable email or structurally invalid/unsupported phone
  numbers are quarantined. Timeouts/unknowns and soft sample-number warnings
  do not create hard blocks.
- Confirmed-good emails receive verification timestamps. Risky results remove
  verification; unknown results preserve prior flags and store an explanation.
- No records or list memberships are deleted. Opt-outs, suppressions and
  previous delivery blocks are never cleared by validation.
- Before saving, each batch re-reads/locks contacts and checks that their
  email/phone still match the values tested. If edited or deleted during a
  network check, the result is shown but not applied to the changed record.

## API and tests

All routes require the existing authenticated session:

- `GET /api/v1/validator/status` — sanitized engine configuration.
- `POST /api/v1/validator/self-test` — deterministic local self-test.
- `POST /api/v1/validator/selection` — exact matching contact IDs, including
  list, search, lead-status, channel and email-state filters.
- `POST /api/v1/validator/batch` — up to five raw rows **or** saved contact IDs;
  returns one result per input, including missing and deleted contacts.

No new database tables, migrations or worker services are required. Frontend
and backend must be deployed together; the existing Render build does this.

```sh
npm ci
npm test
npm run build
npm run lint
python -m venv .venv
.venv/bin/pip install -r requirements.txt
DATABASE_URL=sqlite+aiosqlite:///:memory: .venv/bin/python -m pytest
```

Tests cover scopes beyond 500 contacts, all verdicts, CSV parsing/exports,
stop/resume and failure recovery, auth and input limits, read-only defaults,
stale-contact saves, opt-out/suppression protection, DNS outages/null MX/A/AAAA
fallback, SMTP mailbox-vs-policy rejections, malformed Reacher responses,
Contacts filter scoping and the SMS/Email channel-switch hook-order regression.
The optional PostgreSQL compatibility test needs `pgserver`; without it pytest
reports that file as skipped.

## Deployment verification

`render.yaml` specifies `branch: main` and automatic deployment for API and
worker. An existing Render service created from an older feature branch may
still track that branch despite a merged Blueprint edit. Check **Settings →
Build & Deploy → Branch** on the existing service, set it to `main` if needed,
then **Manual Deploy → Deploy latest commit**. Verify the deployed commit matches
the merged change before concluding the feature is live. Open `/validator` and
run the self-test after deployment.
