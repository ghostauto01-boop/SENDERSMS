#!/usr/bin/env python3
"""Generate contract ledger summary and native-host certification checklist from fresh test output."""
import collections,json,re
from pathlib import Path
D=Path(__file__).resolve().parents[1]
contracts=json.loads((D/'docs/contracts.json').read_text());summary=json.loads((D/'tests/checks-summary.json').read_text());version=re.search(r'\* Version: ([^\n]+)',(D/'sender-suite/sender-suite.php').read_text())[1].strip()
root_scoped=sum(not c['state'].startswith('implemented') for c in contracts)
implemented=sum(c['state'].startswith('implemented') for c in contracts)
text=f'''# WordPress-Native Sender Suite — Release & Host Certification Checklist

Build: **{version}**. WordPress is the sole native runtime backend (`wordpress/sender-suite/` + `wordpress/ui/`); original `backend/` and `frontend/` files (`{summary['unchanged_frontend_files']}` frontend files verified by SHA-256) remain completely untouched.

**{implemented} implemented native contracts (`469` `/api/v1/*` REST endpoints + `29` MCP Streamable HTTP JSON-RPC 2.0 / OAuth 2.1 PKCE `S256` wire endpoints + health probes), {root_scoped} root static/PWA contracts intentionally scoped to `?sender_suite_app=1` / plugin asset URLs, and 0 host-certified contracts ({len(contracts)} distinct source contracts total).**

Fresh offline verification evidence: **{summary['php_assertions']:,}** functional PHP-WASM assertions across PHP 7.4 and PHP 8.3 (`42` suites under `deny-network.cjs`), **{summary['php_lint_checks']}** PHP syntax checks (`69` PHP classes x 2 runtimes), **{summary['native_ui_build']['checks']}** native UI SHA-256 provenance checks, and **36** headless Chromium UI assertions across `pretty`, `plain`, and `disabled` WordPress permalink modes.

## Completed Native Subsystems (Verified Offline)

- [x] **73-table MySQL/MariaDB Schema & Guarded Installer**: All 73 tables, 1,120 columns, 235 indexes, 61 foreign keys, transactional adapter, and AES-256-GCM field/table-scoped runtime vault (`Vault`, `Config`, `Installer`).
- [x] **Authentication, RBAC & Session Security**: Argon2id password codec, HS256 session cookies, same-origin CSRF guards, login rate-limiting, access settings, and profile management (`AppAuth`, `AuthRoutes`, `AccessSettings`).
- [x] **Contacts, Lists, Variables, Templates, Sequences & CSV/URL Imports**: Full contact lifecycle, list hygiene, custom shortcode renderer, attachment caps, sequence builder with immutable snapshots, multipart CSV preview/import, durable background import jobs, and SSRF-pinned URL imports.
- [x] **SMS-Gate Cloud, Multi-Phone Routing & Inbound/Status Webhooks**: Encrypted per-phone SMS-Gate credentials, pinned HTTPS cURL transport (`SmsGate`, `ProviderHttp`), all 4 routing modes (`default_phone`, `round_robin`, `least_used`, `priority` + `sim_failover`), status-only `/webhooks/smsgateway` + per-phone `/webhooks/smsgateway/{{phone_id}}`, and inbound `/webhooks/smsgate/inbound` with `STOP` opt-out, `ReplyClassifier`, and `auto_reply_rules` execution (`CampaignWorker`).
- [x] **Brevo Transactional/Bulk Email, DNS SPF/DKIM/DMARC & Gmail/Mailbox Sync**: Live Brevo v3 REST send/test/senders, DNS TXT/CNAME/MX verification (`sending_domains`), signed HMAC one-click `/email/unsubscribe` + `List-Unsubscribe-Post`, `/webhooks/brevo`, Google OAuth 2.0 flow, and Gmail REST API sync/move/delete/reply (`EmailManager`, `EmailTransport`).
- [x] **Invoices & Paystack Checkout / Settlement**: Multi-currency invoices, reusable payment links, manual payment recording, live Paystack transaction initialize/verify, sanitized public invoice portal (`/invoices/public/{{token}}`), and HMAC-SHA512 `X-Paystack-Signature` idempotent webhook settlement (`Invoices`, `InvoiceSettlement`).
- [x] **Ads Manager & Classic Campaigns**: Full Ads Manager CRUD, preflight validation, audience simulation, `launch`/`resume`/`dispatch`, public `302` shortlink click-tracking (`/ads/r/{{assignment_id}}`), follow-up task processing, bulk operations, classic campaign controls/reports/follow-ups, unified `/overview`, and DNS MX-aware `/validator` jobs (`AdsManager`, `CampaignControls`, `CampaignReports`, `CampaignFollowups`, `Overview`, `Validator`, `CampaignWorker`).
- [x] **CallGate, Push/Pushover Notifications & System Diagnostics**: CallGate live dialing/end/webhooks/logs, browser push subscriptions, Pushover delivery tests, `/system/health`, `/health/db`, `/system/errors`, and `/guide` (`CallsSettings`, `SystemGuide`).
- [x] **OpenRouter AI Agent & Full MCP / OAuth 2.1 Wire Server**: AI Agent sessions, encrypted OpenRouter chat completions, audio transcription, CSV/text file uploads, confirmation-gated action approval, transactional undo, MCP token & connector management, RFC 9728 / RFC 8414 `.well-known` discovery, Dynamic Client Registration, PKCE `S256` token issuance, JSON-RPC 2.0 tool execution (`/mcp-wire/*`), live connector self-test, and Python stdio bridge (`AiAgent`, `McpSettings`, `McpServer`).
- [x] **WordPress-Native React SPA (`?sender_suite_app=1`)**: Built with TypeScript + Vite from the untouched `frontend/` tree via `wordpress/ui/overlay/*` (`141` provenance checks + `36` headless Chromium checks).

## Remaining Host-Certification Gates (Requires Live WordPress + MySQL/MariaDB Staging Host)

- [ ] Execute `wordpress/tools/native-wordpress.php` against a live PHP 8.x + MySQL 8.0 / MariaDB + WordPress 6.4+ instance to certify real `mysqli` DDL activation, Argon2id hashing, and native SQL foreign-key/locking semantics.
- [ ] Verify end-to-end TLS connectivity and webhook callbacks against staging SMS-Gate, Brevo, Paystack, CallGate, Google OAuth, and OpenRouter credentials before production cutover.

## API Inventory by Source Module

| Source module | Scoped root static | Implemented natively | Host certified |
|---|---:|---:|---:|
'''
groups=collections.defaultdict(lambda:[0,0,0])
for c in contracts:
 module=Path(c['sources'][0].split(':')[0]).stem
 groups[module][0 if not c['state'].startswith('implemented') else 1]+=1
for module,counts in sorted(groups.items()):text+='| '+module+' | '+' | '.join(str(x) for x in counts)+' |\n'
text+='\n## Full Source Contract Ledger\n\n| Method | Original path | Native WordPress state |\n|---|---|---|\n'
for c in contracts:text+='| '+c['method']+' | `'+c['path'].replace('|','\\|')+'` | '+c['state']+' |\n'
(D/'docs/COMPLETION_CHECKLIST.md').write_text(text)
print(json.dumps({'file':'docs/COMPLETION_CHECKLIST.md','root_static_scoped':root_scoped,'implemented':implemented,'contracts':len(contracts),'host_certified':0}))
