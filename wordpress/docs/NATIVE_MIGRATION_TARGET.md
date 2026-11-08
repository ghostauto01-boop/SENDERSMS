# Required completion target

The user's instruction is a **full copy/migration of the repository application into WordPress**. WordPress is the application backend, not a connected dashboard for the original Python service. Do not propose a separate original-backend deployment as the solution, request the unavailable archive again, or present another partial plugin as the completed application.

## Runtime boundary

- Recreate runtime inside the installable `wordpress/sender-suite/` plugin using WordPress/PHP and the WordPress MySQL/MariaDB database.
- No running Python/FastAPI service, Node server, original-backend proxy or companion application is required on the WordPress host.
- Python/Node tools in `wordpress/tools/` and `wordpress/tooling/` are offline development/test/build tools, not plugin runtime requirements.
- The original repository is the behavior specification. Preserve original `backend/`, `frontend/`, deployment and dependency files. Adapt copies/build assets within the recreation rather than editing the original application.
- External SMS/email providers that the original application uses are transports, not replacement application backends. Do not register providers or send messages during development checks.

## Completion gates

1. Preserve all source workflows and substantive behavior, not only generic CRUD: auth/settings, contacts/lists/imports, variables/templates, SMS/email/calls/inbox, campaigns/follow-ups/sequences/automations, scheduling/webhooks, accounts/domains/suppression, calendar/invoices, analytics/notifications and AI/MCP/remaining source functionality.
2. Inventory every original method/path in `contracts.json`; do not return pretend success for missing workflows. Current partial handlers are not complete source contracts.
3. Integrate the complete application UI with the native WordPress APIs, including authentication, URL/base-path handling and source request/response behavior. Shipping unchanged JavaScript assets alone does not meet this gate.
4. Validate actual WordPress activation/reactivation/upgrades, real MySQL/MariaDB persistence/constraints, actual HTTP/cookies, browser workflows, permissions, secrets, transaction failures, concurrency and queued-job recovery. Offline PHP-WASM/SQLite/mock tests do not replace this evidence.
5. Review destructive lifecycle/data migration, consent/suppression, duplicate-send prevention and scheduling reliability before enabling any real messaging jobs. Do not create sending callbacks merely by activating a development plugin.
6. Resolve dependency/license notices and produce a reproducible, tested WordPress-upload package only when the full application is implemented. A development ZIP is not fulfillment of the full request.
7. The earlier Google Drive upload request applies to the completed application, not to another incomplete package. No upload has occurred.

## Native test runner status

`tools/native-wordpress.php`, `tools/native-http.py` and the reference `tools/native-wordpress-workflow.yml` prepare an isolated real WordPress/MySQL/Argon2id/HTTP smoke test. They have **not run**. GitHub rejected the attempted workflow push because the current GitHub App lacks workflow-writing permission. The reference workflow is deliberately not installed in `.github/workflows/`; do not invent a successful CI result or infer native compatibility from its presence. Normal source changes were pushed successfully after moving the workflow to a reference file.

This document records the target, not a completion claim. The plugin and ledger remain explicitly incomplete.
