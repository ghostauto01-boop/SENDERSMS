=== Sender Suite ===
Contributors: ghostauto01boop
Requires at least: 6.4
Tested up to: 6.7
Requires PHP: 7.4
Stable tag: 1.0.0
License: Proprietary

WordPress-native SMS, Email (Brevo + Gmail), Ads Manager, Invoices (Paystack), CallGate, OpenRouter AI Agent, and MCP / OAuth 2.1 platform.

== Description ==

Sender Suite brings the complete 73-table multi-channel messaging, CRM, invoicing, Ads Manager, AI Agent, and Model Context Protocol (MCP) workspace natively into WordPress:

* **Contacts, Lists, Variables, Templates & Sequences**: Nigeria-aware E.164 normalization, custom fields, CSV/URL imports, and multi-step sequence snapshots.
* **SMS-Gate Cloud & Multi-Phone Routing**: Encrypted per-SIM credentials, 4 routing modes, HMAC-SHA256 delivery receipts, and inbound `STOP` + auto-reply execution.
* **Brevo Email, DNS Authentication & Gmail Sync**: Transactional/campaign email via Brevo v3, SPF/DKIM/DMARC DNS verification, signed one-click unsubscribe, Google OAuth 2.0, and Gmail thread sync/reply.
* **Invoices & Paystack Checkout**: Multi-currency invoices, payment links, public checkout portal, and HMAC-SHA512 Paystack webhook settlement.
* **Ads Manager & Classic Campaigns**: Audience targeting, A/B creative split simulation, campaign dispatch, `302` click tracking, follow-ups, and MX-aware email validation.
* **AI Agent & MCP / OAuth 2.1 Wire Server**: OpenRouter chat completions, voice transcription, approval-gated actions with undo, and full MCP JSON-RPC 2.0 + OAuth 2.1 PKCE `S256` connector endpoints.

== Installation ==

1. Upload `sender-suite.zip` through **Plugins > Add New > Upload Plugin** in WordPress 6.4+ running on MySQL 8.0+ or MariaDB 10.6+ over HTTPS.
2. Activate **Sender Suite** to create the 73 prefixed application tables.
3. Open **Settings > Sender Suite** in `wp-admin` to initialize your operator username and password.
4. Launch the full native application at `https://your-site.example/?sender_suite_app=1`.
