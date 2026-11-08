#!/usr/bin/env python3
"""Generate exhaustive remaining route inventory plus cross-cutting release checklist; no completion inference."""
import collections,json,re
from pathlib import Path
D=Path(__file__).resolve().parents[1]
contracts=json.loads((D/'docs/contracts.json').read_text());summary=json.loads((D/'tests/checks-summary.json').read_text());version=re.search(r'\* Version: ([^\n]+)',(D/'sender-suite/sender-suite.php').read_text())[1].strip()
missing=sum(c['state'].startswith('missing') for c in contracts);partial=sum(c['state'].startswith('partial') for c in contracts)
text=f'''# Complete remaining-work checklist — INCOMPLETE

Build: **{version}**. WordPress is the native application backend; no original Python service is the proposed runtime. Original repository application files remain unchanged.

**{missing} missing handlers, {partial} partially implemented handlers, zero native-certified contracts, {len(contracts)} source method/path contracts in total.** A partial handler has actual implementation, but does not mean its complete workflow, UI, dependencies and native behavior are certified. Every endpoint remains in the release backlog until those gates pass. Raw handler counts are not a percent-complete estimate.

Fresh offline evidence: {summary['php_assertions']:,} functional PHP-WASM assertions, {summary['php_lint_checks']} first-party syntax checks, {summary['unchanged_frontend_files']} unchanged original frontend hashes. Offline evidence is **not** real WordPress/MySQL/HTTP/provider/browser certification. This checklist is the full current implementation/release backlog, not a claim that software can be guaranteed to run perfectly on every host.

## Implementation order and acceptance gates

### 1. Native installation, data and operational safety
- [ ] Execute real WordPress activation, repeat activation, version upgrades and failure recovery on isolated supported WordPress/PHP/MySQL/MariaDB hosts.
- [ ] Verify all 73 source tables, 1,120 columns, indexes, 61 FKs, nullability/defaults, uniqueness, Unicode/collation, SQL modes, SQL packet limits, date/time precision and transactions on native engines.
- [ ] Implement/review complete legacy-data migration into WordPress, relationships/IDs, consent/opt-outs, encrypted values, attachments, rollback, backups and restore; never overwrite the original application.
- [ ] Validate schema drift/upgrade locks, partial-install repair, deactivation/reactivation, retained data, explicitly authorized uninstall/erasure and table-prefix/multisite behavior.
- [ ] Execute real Argon2id/AES-GCM/key-rotation, cookie/session expiry, role authorization, operator reset and atomic login rate-limit tests; no WordPress-admin cookie bypass for own-app APIs.
- [ ] Document and test PHP extensions, TLS CA trust, DNS/HTTP restrictions, upload/post/memory/time limits, MySQL advisory locks, DB proxies/reconnections, cron configuration and low-traffic/disabled-cron recovery.

### 2. Full native API/workflow implementation
- [ ] Finish all exact methods/paths in the endpoint inventory below with source-compatible validation, responses, permissions and substantive behavior; never replace missing features with fake successful stubs.
- [ ] Complete settings/provider wiring beyond the eight initial encrypted auth settings; all source configuration families, redacted outputs, secret storage, URL validation and rotation.
- [ ] Complete remaining profile/account/session workflows and roles/permissions.
- [ ] Complete contact/list deletion, related-record cleanup, bulk actions, enrichment, email verification, aliases/suppression and export/data-management workflows. Avoid deleting shared calendar attendees or unrelated records.
- [ ] Validate upload preview, synchronous imports, background jobs/progress/error reports and URL imports on native hosts, including consent preservation, atomic batches, crashes, duplicate IDs/memberships, locks, public-address pinning/TLS, limits and retention. These APIs are implemented, not native-certified.
- [ ] Implement actual SMS transports, provider/device/gateway configuration, one-to-one/bulk sends, scheduling, delivery/status reconciliation, bounded retries and error handling.
- [ ] Implement complete email accounts/mailboxes/transports, domains/DNS verification, SMTP/provider/OAuth paths, sender selection, bulk/one-to-one mail, attachments, threading, suppression, unsubscribe/list headers, bounce/open/click tracking and inbox ingestion.
- [ ] Implement both classic campaigns and Ads Manager: drafts, audiences/sets/creatives, assignment/targeting, transitions, budgets/throttles, queues, pause/resume/stop/delete, attribution, follow-up steps/templates/tasks and reconciliation.
- [ ] Implement sequences, standalone/campaign follow-ups, due/overdue handling, quiet hours, timezone/DST behavior, retries and stop-on-reply/opt-out policies.
- [ ] Implement inbox/conversation reads, replies, read/unread state, attachments, searches/filters, campaign attribution, polling/sync and inbound-message lifecycle.
- [ ] Implement calls/CallGate: configuration, handsets/devices, dial requests, call logs and authenticated event/webhook lifecycle. Call analytics alone is not calling.
- [ ] Implement webhook registration/verification, exact raw-body signature handling, replay protection, deduplication, event ordering, rate limits, retries and redacted logs.
- [ ] Wire auto-reply/If-Then rule execution, per-contact cooldowns, counters and supported actions into actual inbound events. Rule CRUD/classification/non-sending tests already exist; action execution does not.
- [ ] Complete calendar scheduling/attendees/invitations, invoices/items/payments/PDF/email workflows and safe deletion semantics.
- [ ] Complete dashboards/overview/unified managers and reporting. Five analytics read APIs now exist; complete dashboard, email/manager analytics and native validation remain outstanding.
- [ ] Complete notification delivery, browser push subscriptions, provider/browser permission handling, setup guide and live setup summaries. Notification read-state APIs alone do not deliver notifications.
- [ ] Complete AI assistant/agent sessions, files/memory/skills/tools, external-provider configuration, explicit action permissions and safe tool execution; complete MCP endpoints and authentication, SSE/NDJSON streaming, disconnects, buffering/proxy timeouts and browser cancellation.
- [ ] Complete validator/verification tools, diagnostics, persisted system errors and accurate worker/queue health (not source Redis/Celery status fabricated for WordPress).

### 3. Messaging and worker safety before enabling sends
- [ ] Enforce consent, channel opt-outs, suppression, unsubscribe/bounce states and recipient validity consistently at enqueue **and** delivery time.
- [ ] Prove duplicate-send prevention/idempotency, provider-ack vs DB-commit ambiguity handling, bounded retries, claims/leases and recovery across concurrent workers, crashes and lost connections.
- [ ] Reconcile late/duplicate/out-of-order events, campaign/contact/message attribution and counters across both campaign engines and channels.
- [ ] Enforce quiet hours, scheduling/timezones, per-provider/account/device throttles and safe cancellation/paused states.
- [ ] Verify attachment handling, CSV/formula export safety, stored HTML/rendering sanitization, untrusted inbound content, path/SSRF protections and size/resource caps.
- [ ] Review secrets, operator-only data, public unsubscribe/tracking links, webhook tokens, raw CSV/error retention, provider-error scrubbing and logging/privacy/backups.
- [ ] Test with isolated fake provider servers first; perform authorized staging-provider delivery/reconciliation only with explicit test credentials/recipients. Never send unsolicited real messages as a development check.

### 4. Complete WordPress frontend integration
- [ ] Adapt copied/build assets without modifying original frontend source; map every original API request to the native REST backend with correct base paths and deployment subdirectories.
- [ ] Mount the complete application UI in WordPress with working routes/history, assets, deep links, authentication/roles, login/logout/cookie behavior, loading/error states and navigation.
- [ ] Wire every original page/wizard/control, including upload/URL imports, managers, inboxes, settings, analytics, calls, follow-ups, calendar, invoices, agents and diagnostics; hide/disable genuinely unsupported actions, not pretend they work.
- [ ] Verify real browser multipart uploads, raw CSV/downloads, attachments, previews, pagination/filtering, mobile/responsive behavior, accessibility and permission boundaries.
- [ ] Validate REST reserved-query hardening, JSONP/output flags, CSRF/origin checks and compatibility with relevant WordPress themes/plugins/cache/security policies.

### 5. Native release proof and final plugin file
- [ ] Execute structured isolated native installation and HTTP reference runners. They are present but have **not run**; the current GitHub connection previously rejected workflow creation for lack of workflow-writing permission.
- [ ] Add/execute complete browser journeys, native constraint/FK/lifecycle tests, failure injection, concurrent workers, recovery, DST/locale/timezone cases and large-data performance/hosting tests.
- [ ] Reconcile every source contract and substantive workflow against fresh native evidence; partial/mock tests cannot certify release readiness.
- [ ] Confirm licenses/notices, dependency integrity, original-source preservation, security review, backup/rollback procedures and operator installation/configuration documentation.
- [ ] Produce and verify the final reproducible WordPress upload ZIP **after** the gates pass; staging activation/update tests, checksums and release evidence must accompany it.
- [ ] Deliver the completed plugin; upload to Google Drive if still requested at completion. No completed release or Drive upload has occurred.

## Current package meaning

`sender-suite-development.zip` is the **current incomplete development plugin**, not the requested finished application. Its archive layout/CRC are checked, but real WordPress activation and production operation have not been verified. Do not install it on a production messaging site. The ZIP includes this checklist and offline evidence so the remaining work is not hidden.

## API inventory by source module

All counts below are derived from `docs/contracts.json`. Missing = no native handler; partial = implementation exists but native/full-workflow certification remains open.

| Source module | Missing | Partial | Fully certified |
|---|---:|---:|---:|
'''
groups=collections.defaultdict(lambda:[0,0,0])
for c in contracts:
 module=Path(c['sources'][0].split(':')[0]).stem
 groups[module][0 if c['state'].startswith('missing') else 1]+=1
for module,counts in sorted(groups.items()):text+='| '+module+' | '+' | '.join(str(x) for x in counts)+' |\n'
text+='\n## Every remaining source contract\n\nThis exhaustive list includes partially implemented routes because none is yet native-certified. Detailed source locations are retained in `docs/REMAINING_WORK.md` and `docs/contracts.json`.\n\n| Method | Original path | Remaining state |\n|---|---|---|\n'
for c in contracts:text+='| '+c['method']+' | `'+c['path'].replace('|','\\|')+'` | '+('Missing implementation' if c['state'].startswith('missing') else 'Implemented partially; native/full-workflow proof required')+' |\n'
(D/'docs/COMPLETION_CHECKLIST.md').write_text(text)
print(json.dumps({'file':'docs/COMPLETION_CHECKLIST.md','missing':missing,'partial':partial,'contracts':len(contracts),'complete_application':False}))
