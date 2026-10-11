# Sprint 3 report (P0-6 and §E)

> **Follow-up (2026-10-11):** The free-mail and sender-domain-authentication launch gates described below were subsequently removed. Existing valid email senders can be saved and campaigns continue through the configured email route; provider acceptance is decided at delivery. Usable account/API-key, recipient verification, consent/opt-out, and sending-rule checks remain. See [`CHANGELOG.md`](../CHANGELOG.md) and [`SENDING-DOMAINS.md`](SENDING-DOMAINS.md) for the current behavior. The P0-6 results below are a historical record of the earlier implementation, not the current launch policy.

Branch `arena/191f89ff-sendersms`. Raw output: [`docs/evidence/sprint-1/`](evidence/sprint-1/) (`tests_p0_6.txt`, `tests_e_quarantine.txt`);
regenerate with `SPRINT=3 docs/evidence/sprint-1/collect.sh` and `docs/evidence/sprint-1/integration.sh`.

**Same caveat as Sprints 1 and 2: verified in a sandbox with synthetic data and mocked DNS and Brevo. Nothing ran against production, a live
Brevo account or real DNS — which matters more here than anywhere, because this sprint is about what receiving servers will accept.**

## Results at a glance

| Check | Result |
|---|---|
| Backend suite | **1307 passed, 1 skipped** (+67 this sprint; the skip: `test_postgres_compat.py`, no `pgserver` here) |
| Frontend | unchanged this sprint (no UI was built; the last `tsc` = 0 and 24 files / 164 tests still describe the tree) |
| Real server: simulator, gate on · gate off · MCP smoke · every GET | **35/35** · **61/61** · **92/92** · 96 endpoints clean |

## P0-6, bullet by bullet

| Brief | Status | What it does / evidence |
|---|---|---|
| 422 on a free-mail sender | **Done** | gmail.com, yahoo.com, outlook.com, icloud.com … (a shared list of ~45 domains). A From address on one is a 422 when an account is created or changed; `POST /email/domains` for one is a 422; and a campaign from an account that *predates* the rule cannot start (classic `validate`/`start` and Ads Manager `launch` — the latter is a **409 `SENDER_NOT_READY`**, which reverses the earlier "ads launch is advisory" stance for exactly this case, as the brief asks) |
| `SendingDomain` object with Brevo TXT, DKIM and exactly one DMARC record (merge `rua`, never a second) | **Done, against mocks** | `POST/GET/DELETE /email/domains`, `POST /email/domains/{id}/verify`, tools `register_sending_domain` / `list_sending_domains` / `verify_sending_domain`. The plan holds the Brevo verification code, DKIM, and **one** DMARC record: the domain's existing record is read and the `rua` merged into it (other tags kept, a policy never weakened, several existing records become one), and the plan says **REPLACE** — never add. A failed DNS read is reported as "could not read", never mistaken for "no record" |
| Warm-up ramp (~5/day in week 1 up to ~25/day in week 4+), blocking `start_campaign` | **Done (my reading of "blocking")** | 5/day days 1–7, 10, 18, then 25/day from day 22; the clock starts when the domain first authenticates; enforced per mailbox in `email_capacity` and the sending gate (below the per-mailbox default), shown in `validate`'s projection and warnings. A campaign **cannot start** from a domain that is registered but not authenticated, or from a free-mail sender; an unregistered domain is a warning (existing installs keep working). I read "blocking" as *start is refused when the sender is not fit to send*, and *volume is held to the ramp* — a campaign larger than the ramp is paced and projected, not refused |
| Custom tracking domain | **Not done** | It needs a CNAME target that only Brevo's account settings show; the API does not expose it, and I will not invent one |
| DKIM=pass integration test | **Not done — cannot be run here** | It needs a real send through Brevo to a real mailbox. `docs/SENDING-DOMAINS.md` has the three-step manual check (Gmail → Show original → `DKIM: PASS d=<your domain>`) |

**Evidence** (`tests_p0_6.txt`): 61 tests; against the code at the end of Sprint 2, **21 fail and 9 error**; all 61 pass now. (31 "pass before" only because the new pure helper
module was copied into the old tree so the file could be collected — the free-mail and DMARC-merge unit tests exercise that module. The behavioural ones —
the 422s, the domain API, the warm-up cap, the start-blocking — are what fail.)

**Not verified:** Brevo's real response shape for `POST /senders/domains` and `PUT …/authenticate` — the parsing is lenient and written from the documented shape; it has only run against
mocks. No SPF record is planned (Brevo's current authentication is the verification code plus DKIM, but I could not confirm whether your domain's SPF should also `include:` Brevo).
No dashboard screen exists for domains or the warm-up: it is API and assistant tools only.

## §E

| Item | Status |
|---|---|
| The numbers (1,297 contacts, 0 verified, 184 bounced, ~70% generated role mailboxes) | The brief's production figures; **not re-measured by me** |
| Tag generated addresses `pattern_generated` / `sendable:false`, do not delete | **Done** — `POST /contacts/quarantine/generated` (tool `quarantine_generated_addresses`). "Generated" = `email_source = "inferred"`. A **dry run unless `dry_run=false`** (also the tool's default), idempotent, never deletes; it reports unverified role mailboxes (info@, sales@ …) *without* tagging them, since a role address someone typed or imported is real. `tests_e_quarantine.txt`: 6 fail → 6 pass. **Not run against production data.** |
| RFC 8058 headers | **Already there** — `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` are on the wire in the simulator evidence, with a one-click POST endpoint |
| Consent model | **Not done** (the contact has `consent_status` / `has_consented` for SMS and an email opt-in/out; a source-and-timestamp consent record per channel would be a design, not a fix) |
| Inbound classification | **Not done** |
| Rename `bait_sms_step_1` | **Not in the repository** — it is a production data row. Rename it with `PUT /templates/{id}` (or the sequence endpoint if it is a step) |

---

## Everything that is still open across the whole brief

1. **P0-0** — pause campaign 2 on production (yours; see the Sprint 1 report).
2. **Nothing was verified against production, a live Brevo or a real DKIM check.** The Reacher-backed verifier (P0-5), the Brevo domain calls (P0-6) and DKIM=pass are the three places that matter most.
3. **Not done:** custom tracking domain; the DKIM=pass integration test; webhook HMAC-signing (C10 — Brevo does not sign); a consent model; inbound classification; tool schemas *generated* from endpoint models (C7 — a test enforces the contract instead).
4. **API and assistant tools only:** sending domains, the warm-up, verification jobs and the quarantine have no dashboard screens.
5. **Decisions that are yours**, each flagged where it arose: whether sends should fail rather than silently drop an unknown variable (Sprint 2, C2); whether the classic engine should honour per-campaign `daily_limit`/`min_delay` or lose those fields (C7); whether `completed`/`stopped` campaigns should have an exit (P0-1); whether the one-time safe-defaults step should exist at all now that every save records a decision (P0-3).
