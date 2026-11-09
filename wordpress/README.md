# Sender Suite — WordPress-Native Application (`1.0.0`)

This directory contains the complete **WordPress-native** migration of Sender Suite (`wordpress/sender-suite/` and `wordpress/ui/`). WordPress and PHP 7.4–8.3 replace the FastAPI/Python backend at runtime with zero dependency on any external Python service, while leaving all original `backend/` and `frontend/` (`165` source files verified by SHA-256) completely untouched.

## Architecture & Implemented Subsystems (`69` PHP Classes, `313` WP REST Routes)

- **73-Table Native Schema & Encrypted Runtime Vault (`Schema`, `Database`, `Installer`, `Vault`, `Config`)**: Full MySQL/MariaDB DDL planner covering all `73` tables, `1,120` columns, `235` indexes, and `61` foreign keys, paired with AES-256-GCM field/table-scoped secret sealing derived from WordPress salts.
- **Own-Application Authentication & RBAC (`AppAuth`, `AuthRoutes`, `AccessSettings`)**: Argon2id password hashing, HS256 session cookies, same-origin CSRF enforcement, atomic login rate-limiting, operator setup screen (`Settings > Sender Suite`), and role-based access (`admin` / `viewer`).
- **Contacts, Lists, Variables, Templates, Sequences & Imports (`Contacts`, `ContactLists`, `ContactActions`, `ContactExport`, `Variables`, `Templating`, `Templates`, `Sequences`, `CsvImport`, `ImportJobs`, `CsvUrl`)**: Full CRM lifecycle, Nigeria-aware E.164 phone normalization (`libphonenumber` 8.13.52), spreadsheet-safe CSV exports, multipart CSV preview/import, durable background import jobs, and SSRF-pinned HTTPS URL imports.
- **SMS-Gate Cloud, Multi-SIM Routing & Inbound/Status Webhooks (`SmsGate`, `GatewayPhones`, `SmsOutbox`, `ScheduledSms`, `SmsReceipts`, `CampaignWorker`)**:
  - Pinned-IP HTTPS cURL transport (`ProviderHttp` / `SmsGate`) with all 4 gateway routing modes (`default_phone`, `round_robin`, `least_used`, `priority` + `sim_failover`).
  - Status-only HMAC-SHA256 delivery reconciliation on `/webhooks/smsgateway` and `/webhooks/smsgateway/{phone_id}`.
  - Live inbound SMS ingestion on `/webhooks/smsgate/inbound` with immediate `STOP` opt-out enforcement, deterministic `ReplyClassifier` sentiment/intent tagging, pending follow-up cancellation, and `auto_reply_rules` execution.
- **Brevo Email, DNS SPF/DKIM/DMARC & Gmail/Mailbox Sync (`EmailManager`, `EmailTransport`)**:
  - Live Brevo v3 REST API account testing, sender discovery, personalized email delivery with `List-Unsubscribe` + `List-Unsubscribe-Post` headers, signed HMAC public `/email/unsubscribe`, and `/webhooks/brevo` event ingestion.
  - Sending-domain creation and live DNS TXT/CNAME/MX verification (`sending_domains`).
  - Google OAuth 2.0 authorization/callback and Gmail REST API inbox sync, folder moves, delete, refresh, and thread replies.
- **Invoices & Paystack Settlement (`Invoices`, `InvoiceSettlement`)**:
  - Multi-currency invoices (`NGN`, `USD`, `GHS`, `ZAR`, `KES`, `XOF`, `EGP`), reusable payment links, manual payment recording, live Paystack checkout initialization & verification, sanitized public invoice portal (`/invoices/public/{token}`), and idempotent HMAC-SHA512 `X-Paystack-Signature` webhook settlement (`/invoices/paystack/webhook`).
- **Ads Manager, Classic Campaigns, Overview & Validator (`AdsManager`, `CampaignControls`, `CampaignReports`, `CampaignFollowups`, `Overview`, `Validator`, `CampaignWorker`)**:
  - Full SMS/Email Ads Manager CRUD, preflight validation, split simulation, `launch`/`resume`/`dispatch`, public `302` click redirect (`/ads/r/{assignment_id}`), follow-up task processing, bulk actions, classic campaign controls/reports/follow-ups, unified `/overview` (including `kind=ads` detail), and DNS MX-aware `/validator` jobs & contact enrichment.
- **CallGate, Notifications & Diagnostics (`CallsSettings`, `NotificationCenter`, `SystemGuide`)**:
  - CallGate live dial/end/test/webhook lifecycle, browser push subscriptions, Pushover delivery tests, `/system/health`, `/health/db`, scrubbed `/system/errors`, and `/guide`.
- **OpenRouter AI Agent & Full MCP / OAuth 2.1 Server (`AiAgent`, `McpSettings`, `McpServer`)**:
  - AI Agent sessions, encrypted OpenRouter chat completions, voice transcription, CSV/text file uploads, confirmation-gated tool approval, and transactional undo.
  - Full Model Context Protocol (Streamable HTTP JSON-RPC 2.0, protocol `2025-06-18`) + OAuth 2.1 Authorization Server (RFC 9728 protected-resource metadata, RFC 8414 authorization-server metadata, Dynamic Client Registration, PKCE `S256` token exchange, revocation), live connector handshake self-tests, and downloadable `arena_mcp_bridge.py` stdio bridge under `/wp-json/sender-suite/v1/mcp-wire/*`.
- **WordPress-Native React SPA (`AppShell`, `wordpress/ui/overlay/*`)**:
  - Served cleanly via `?sender_suite_app=1` without hijacking the WordPress site root or registering a site-wide service worker.

## Verification Evidence

- **`499 / 508` source contracts implemented natively** (`469` `/api/v1/*` REST contracts + `29` MCP/OAuth 2.1 wire contracts + health probes); the remaining `9` FastAPI root static/PWA contracts (`/manifest.json`, `/sw.js`, icons, `/{fp:path}`) are intentionally scoped under `?sender_suite_app=1` and plugin asset URLs.
- **`45,038` PHP assertions** passing across `42` offline test suites on **both PHP 7.4 and PHP 8.3** under `wordpress/tools/deny-network.cjs` (`0` external network sockets).
- **`138` PHP syntax checks** (`69` PHP classes across PHP 7.4 & 8.3), **`3,075` vendor parser checks**, and **`1,547` vendor SHA-256 checks**.
- **`141` native UI provenance checks** (`verify_native_ui.py`), **`165` unchanged `frontend/` source file hashes**, and **`36` headless Chromium integration assertions** (`test_native_ui.mjs`) across `pretty`, `plain`, and `disabled` WordPress permalink modes.
- **Host certification boundary**: Native `mysqli` / MySQL 8.0 / MariaDB DDL activation and live third-party provider HTTP credentials have not been executed inside this offline WASM sandbox (`certified: 0` in `STATUS.json`). Run `wordpress/tools/native-wordpress.php` on a staging WordPress + MySQL/MariaDB host before production deployment.

## Packaging & Installation

1. Run full verification and build the installable WordPress plugin archive:
   ```bash
   python3 wordpress/tools/build_native_ui.py
   python3 wordpress/tools/run_checks.py
   python3 wordpress/tools/checkpoint.py
   python3 wordpress/tools/write_remaining_checklist.py
   python3 wordpress/tools/package_plugin.py
   ```
2. Upload `wordpress/artifacts/sender-suite.zip` in WordPress (**Plugins > Add New > Upload Plugin**), activate **Sender Suite**, configure operator credentials & live workers under **Settings > Sender Suite**, and launch the workspace via `?sender_suite_app=1`.
