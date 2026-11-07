# AI Agent — routing and operations

This document describes the current AI Agent interface and routing contract. The
agent uses OpenRouter only. Operators choose an OpenRouter preset when they want
to control model and provider routing; individual models and providers are not
separately selectable in the app.

## Routing contract

- The OpenRouter API key is encrypted at rest. API responses expose only a
  masked hint, never the secret.
- The single routing choice in the UI is an OpenRouter preset. The preset slug
  is sent to OpenRouter as `@preset/<slug>`; the preset owns its model,
  provider routing, and OpenRouter-side fallback behavior. The app does not add
  a separate provider-routing field or the unsupported plural `presets` field
  to the request.
- With no preset selected, the backend chooses a suitable OpenRouter model
  internally from the live model catalogue. The user does not pick or pin that
  model. If **Automatic recovery without a preset** is enabled, an unavailable
  model or rate limit can trigger one retry with a different automatically
  selected model. Turning `auto_fallback` off disables that retry. This local
  recovery is not applied over a selected preset; the preset owns its routing.
- Voice transcription follows the same rule: use the selected preset, or
  internally choose an audio-capable OpenRouter model when no preset is set.
- Chat and session-create requests have no `provider` or `model` override
  fields. `GET /ai-agent/models` is a diagnostic catalogue for internal routing,
  not a settings picker; it does not select or save a model.

Relevant endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /ai-agent/settings` / `PUT /ai-agent/settings` | Masked key status, `auto_fallback`, and action-approval behavior |
| `GET /ai-agent/presets` / `PUT /ai-agent/presets` | List, validate, select, or clear the OpenRouter preset |
| `GET /ai-agent/health` | Live OpenRouter health and agent tool status |
| `GET /ai-agent/models` | Diagnostic model catalogue; never a user selection |
| `POST /ai-agent/sessions/{id}/chat` | Chat with message, file IDs, and stream preference; no routing overrides |

When OpenRouter does not provide a preset list for a key, settings accepts a
preset slug and validates it with a small real OpenRouter request before saving.
The selected slug is stored in application settings and shared by agent turns.

## Turn and action safety

1. The user's message is persisted before OpenRouter is called, so a failed
   request does not lose the prompt.
2. A turn streams progress, text, tool calls, and typed errors. In no-preset
   mode, eligible internal recovery emits a notice/status update. A selected
   preset remains the displayed route; no provider or model picker is shown.
3. Tool calls run in a loop of at most 15 steps. Destructive or external actions
   create a confirmation card unless auto-approval has explicitly been enabled.
4. Approvals are logged with the result and, where supported, an undo payload.
   Cancelling an approval does not run the action.
5. Long histories are summarized into session memory, and the final transcript
   is committed consistently after the turn.

Sixteen tools are marked as dangerous and require confirmation by default:

`app_api_request, bulk_contact_action, bulk_delete_contacts, create_invoice,
delete_contact, delete_list, import_csv, launch_ads_campaign, pause_campaign,
reply_to_conversation, resume_campaign, schedule_campaign, send_email, send_sms,
send_to_list, start_campaign`

## Settings and interface

### OpenRouter setup

The settings panel has one OpenRouter API-key card and one OpenRouter-preset
card. The preset card lists presets when the API can return them, or accepts a
slug when it cannot. Its help text explains that a preset controls model,
provider routing, and fallback behavior. There are no provider or model
selectors. The behavior section contains:

- **Automatic recovery without a preset** — enabled by default; permits one
  internal retry after an unavailable automatic model or a rate limit, only when
  no preset is selected.
- **Auto-approve agent actions** — disabled by default; bypasses confirmation
  cards if explicitly enabled.

The agent header shows OpenRouter and the current preset (or prompts the operator
to choose one). It does not show a set of provider pills. Progress uses route
labels; automatic recovery messages may name the replacement model for
troubleshooting, but that name is not a user-controlled setting.

### Screens and workflows

- **Overview** — the Ask the agent card shows live OpenRouter status, tool
  count, and an entry field. Submitting a prompt opens the agent and sends it
  once.
- **Agent thread** — a session rail, streamed conversation, and optional files,
  activity, and settings panels. The composer supports text, file attachment,
  and voice-note transcription into the draft for review before sending.
- **Settings** — manage the OpenRouter key and preset, then review recovery and
  approval behavior. The Test key action can use a typed key or the saved key.
- **Approvals and activity** — consequence, audience, and content are presented
  before a gated action runs; completed actions are recorded in the activity
  panel, with undo offered when safe.
- **Files** — uploads are parsed before import, with preview and column mapping
  for supported tabular and document formats. Scanned-PDF text extraction is
  not supported.

## Tool areas

| Area | Count | Examples |
|---|---:|---|
| contacts | 9 | search, count, create, update, tag, bulk action, bulk delete |
| lists | 6 | create, rename, add members, count, delete |
| files | 4 | list uploads, read rows, query a file, import into a list |
| templates | 4 | list, create, update, preview with a real contact |
| campaigns | 13 | validate, start, pause, resume, schedule, create, clone, stats |
| sending | 4 | send one SMS, send an email, send to a list, launch an ads campaign |
| inbox | 5 | list conversations, read a thread, reply, mark, hand off |
| calendar | 2 | list meetings, create a meeting |
| invoices | 2 | create, send a payment link |
| automation | 1 | toggle an auto-reply / automation |
| analytics | 3 | overview, campaign report, export |
| system | 7 | app API request, gateway status, queue health, errors |

## Verification

These checks cover the preset-only routing contract and the related backend and
frontend behavior. Results are recorded after running them on the current tree:

| Check | Command | Result |
|---|---|---|
| Focused backend regressions | `cd backend && ../.venv/bin/python -m pytest -q tests/test_openrouter_provider.py tests/test_openrouter_preset_fallback.py tests/test_ai_agent.py tests/test_email_body_and_sender_pool.py tests/test_email_routing_and_mail_app.py tests/test_mailbox_replies.py` | **154 passed** (one `slowapi` deprecation warning) |
| Frontend tests | `npm test` | **43 files / 269 tests passed** (jsdom navigation and React `act()` warnings from `ContactsPage.bulk.test.tsx`) |
| Targeted ESLint | `npx eslint frontend/src/api/aiAgent.ts frontend/src/components/agent/AgentModelSettings.tsx frontend/src/components/agent/AgentModelSettings.test.tsx frontend/src/components/agent/AgentThread.tsx frontend/src/components/agent/AgentTurn.tsx frontend/src/pages/AIAgentPage.tsx frontend/src/pages/AIAgentPage.test.tsx` | **Clean** |
| Production build | `npm run build` | **Passed** (`tsc -b` and Vite production build) |
| Python bytecode compilation | `.venv/bin/python -m compileall -q backend/app backend/tests` | **Passed** |
| Whitespace/conflict check | `git diff --check` | **Passed** |
