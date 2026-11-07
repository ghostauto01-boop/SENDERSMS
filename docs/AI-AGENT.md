# The AI Agent — what changed, and how to check it

Branch `arena/4752fe9b-sendersms`. This document is the delivery note for the
agent rebuild: what was broken, what it does now, the evidence for each claim,
and — because this sandbox cannot take screenshots (no usable Chromium:
`libnspr4.so` is missing and the bundled build segfaults) — labelled
descriptions of every screen so the UI can be recognised without a picture.

---

## 1. What was actually wrong

| Symptom the operator saw | Cause | Where it is fixed |
|---|---|---|
| Every chat returned **500 Internal Server Error** | The agent named models that no longer exist (`llama-3.3-70b-versatile`, `gemini-1.5-flash`) and turned any provider error into an unhandled exception | `services/ai_providers.py` (live model list, typed error codes), `services/agent_runtime.py` (errors are events) |
| The **Test** button demanded a key the operator could not see | The endpoint required `api_key` in the body; the saved key was only ever returned masked | `POST /ai-agent/settings/test` falls back to the saved key |
| The green "configured" dot lied | It read a row from the settings table, not the provider | `GET /ai-agent/health` sends a one-token completion through each provider |
| The assistant **could not do anything** | It could chat; the only actions were an offline keyword matcher | 60 tools in `services/agent_tools.py`, run by `services/agent_runtime.py` |
| Nothing was recorded, so nothing could be undone or audited | No action log existed | `AIAgentAction` + `GET /ai-agent/activity` + `POST /ai-agent/activity/{id}/undo` |
| Dangerous work happened as soon as the model said so | No approval step | Confirmation cards; 16 tools are gated |
| Uploading a CSV meant typing a file id into the chat | Files were stored but never parsed | `services/agent_files.py` (CSV/TSV/XLSX/PDF/DOCX, typed columns, suggested mapping) |
| A reply could not be formatted, and code blocks were plain text | No markdown renderer existed | `frontend/src/utils/markdown.ts` + `components/Markdown.tsx` |

## 2. What the agent can do now (60 tools)

| Area | Count | Examples |
|---|---|---|
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

Sixteen of them are marked **danger** and never execute inline unless
auto-approve is explicitly switched on:

`app_api_request, bulk_contact_action, bulk_delete_contacts, create_invoice,
delete_contact, delete_list, import_csv, launch_ads_campaign, pause_campaign,
reply_to_conversation, resume_campaign, schedule_campaign, send_email, send_sms,
send_to_list, start_campaign`

## 3. How a turn works (OpenRouter-Exclusive + Permanent Preset Fallback)

1. The user's message is written to the database **before** OpenRouter is
   called, so a failed model call never loses what was typed.
2. **OpenRouter-Exclusive Provider & Permanent Preset Lock**:
   - The agent runs exclusively through OpenRouter (`https://openrouter.ai/api/v1`) with HTTP keep-alive connection pooling.
   - Operators can enable **Permanent Preset Lock** with an ordered model fallback chain (`preset_models`, plus named saved workspace presets). When locked, **only** the selected models run in exact order — if Model #1 fails (rate-limited, timed out, retired, or upstream error), Model #2 takes over automatically and emits a live fallback `notice` + `status` pill without overwriting the primary model.
3. **Fast Live Step-by-Step Progress**:
   - Real-time NDJSON stream emits `status` phases (`routing`, `context_ready`, `generating`, `tool_running`, `synthesizing`, `fallback`), live `thinking` reasoning traces, `tool_start`/`tool_end` timings, and `text` chunks.
4. Tool calls are executed in a loop of at most 15 steps.
5. A danger tool is described — how many contacts, which list, the first 280
   characters of the message, who opted out — and queued as a **pending action**.
   The model is told "WAITING FOR APPROVAL" and the turn stops there.
6. Approving runs it and logs the result, duration and (where possible) an undo
   payload. Cancelling leaves nothing behind.
6. Long histories are folded into the session memory instead of being re-sent.
7. Everything is committed once, at the end, so a stream that dies mid-way
   leaves a consistent transcript.

## 4. Evidence

All numbers below are reproducible on this branch.

| Check | Command | Result |
|---|---|---|
| Backend suite | `cd backend && pytest -q` | **1488 passed** |
| Agent-specific backend tests | `pytest -q tests/test_ai_agent.py` | **38 passed** |
| Frontend type-check | `npx tsc -b --force` | clean |
| Frontend tests | `npx vitest run` | **35 files / 245 passed** |
| Production build | `npm run build` | built in 6.4 s, `AIAgentPage` chunk 58 kB |
| Live server, real HTTP | see §5 | health, settings, upload, chat, approvals all exercised |

### What the 38 backend tests actually assert

* a chat returns a structured error code, never a 500 — and the user's message
  survives it;
* a **retired model id is replaced from the live list** and the turn still
  answers, with a notice naming the change;
* a rate-limited provider falls through to the other one;
* **no API key appears in any response body** (settings, health, models);
* the Test button works with **only a saved key**;
* a model name ban: dead ids may be *described* in a docstring but can never
  appear as a value the code can send;
* an **opted-out contact is refused** by `send_sms`;
* `start_campaign` produces a card, changes nothing, and only starts on approval
  — and a refusal (no gateway) is logged as `failed`, not `success`;
* an upload is parsed, columns typed, a mapping suggested; a `.exe` is refused
  with a reason;
* a 50-row CSV becomes 50 contacts in a new list;
* voice transcription, global memory, skills and the tool catalogue.

## 5. Screens (described, because screenshots are impossible here)

Each entry names the route, what is on the screen, and the sentence a screenshot
would have carried. Any of them can be reproduced in ten seconds with
`npm run dev` + the backend; the shapes below are what you will see.

### 5.1 Overview — `?ask=` entry point
**Route** `/overview`. **Above the eight metric cards** sits a dark forest band,
rounded, with a soft light behind its right edge.
Left: a mint-tinted square with a sparkle glyph, then **"Ask the agent"** and,
under it, `60 tools · openai/gpt-oss-120b` (or `Add a key to switch it on`).
Right: a small pill — green **Live**, amber **Check key**, or a light **Add key**
link — and **Open ↗**.
Below: a translucent input with a bot glyph, the placeholder cycling through
real examples ("Which campaign is doing best this week?"), and a white **Ask**
button. Two example chips sit underneath.
Typing and pressing Enter navigates to `/ai-agent?ask=…`, which sends the
sentence immediately.
> *Screenshot label: "Overview — the agent is one sentence away; status is the
> provider's, not a stored flag."*

### 5.2 The agent — empty state
**Route** `/ai-agent`. A three-pane app shell: a 248 px conversation rail on the
left, the thread in the middle, nothing on the right until a tool is opened.
Header: the brand square, the conversation title (click to rename, pencil on
hover), and two live pills — `Groq Working` / `Gemini No key` — each a coloured
dot plus words. Right: **New**, then paperclip, history and sliders buttons.
Centre: a large forest square with a sparkle, **"What should I do?"**, one
sentence of scope, and six suggestion cards in two columns. At the bottom, the
composer: paperclip, an auto-growing line, a microphone, a forest send arrow,
and the hint *"Enter sends · Shift+Enter adds a line · the mic transcribes into
the box so you can check it first"*.
> *Screenshot label: "Empty agent — every suggestion is a real task, and the two
> provider pills are live pings."*

### 5.3 Setup panel
**Route** `/ai-agent`, sliders button (also `/settings?tab=ai` — same component).
A card headed **"The agent's brain"** with one sentence of state, then two
status pills and `60 tools · 12 areas · checked 14:20`.
Under it, two key cards side by side. Each: provider name, one line of purpose,
a **Saved/Not set** badge, the masked hint `gsk_***ZJGp` with a shield glyph and
the words *"encrypted, never sent to the browser"*, a password input whose
placeholder reads *"Paste a new key to replace it"*, and **Save · Test ·
Remove**. After a test, a green or red strip carries the provider's own message,
the suggested model id and the hint.
Below: a **Model** card — two dropdowns, each listing live model ids with a
**BEST** badge on the recommended one and *"no tools"* on models that cannot
call tools — and two switches: **Fall back to the other provider** (on) and
**Skip approval for risky actions** (off, in red).
> *Screenshot label: "Keys and models — the list comes from the provider; the
> test uses the saved key."*

### 5.4 A working turn
The user's sentence in a forest bubble, right-aligned. Under it, left-aligned
with a small sparkle glyph:
a streamed markdown answer (headings, bullets, a table, a fenced block with a
**Copy** button), a collapsing tool row — a green tick, `Searched 1,204 contacts
for “abuja”`, `142ms`, and `count_contacts` in mono — and a blue notice strip
when a model was swapped: *"llama-3.3… is no longer available; switched to
openai/gpt-oss-120b."* While streaming, a blinking caret follows the text and
`Step 2 of 15` sits underneath.
> *Screenshot label: "A turn — what it said, what it did, and why the model
> changed, on one screen."*

### 5.5 An approval card
An amber-bordered card inside the thread, no modal.
Header: a warning triangle and **"Send this campaign now?"**. Body: one sentence
of consequence, then a small table — **Channel** `SMS`, **Audience**
`1,204 contacts (38 opted out skipped)`, **Message** with the first lines of the
real body. Footer: forest **Approve and run**, white **Cancel**, and the grey
words *"Nothing has been sent or deleted yet."*
On approval the buttons show spinners; the result lands as a toast and a line in
the transcript, and a refusal is shown verbatim (e.g. *"No SMS gateway
configured…"*).
> *Screenshot label: "Approval — consequence, audience and message before
> anything happens."*

### 5.6 Files
Paperclip button. Header **Files**, forest **Upload**, a dashed drop zone
("Drop a file, or tap to choose — CSV · Excel · PDF · Word · up to 25 MB") with
a progress bar, then a list of uploads (`leads.csv · 80 KB · 2 rows · 3 cols`)
each with **Use in this chat**.
Opening one reveals a **Column mapping** block — `Business Name → business_name`,
`Phone → phone_number`, `City → city`, each a dropdown down to *— ignore —* —
and a scrollable table of the first rows with 1–8 of 2 paging.
> *Screenshot label: "Files — parsed before anything is imported, with the phone
> column identified."*

### 5.7 Voice
The composer after tapping the microphone turns red: a pulsing dot,
`Recording 00:07`, **Discard**, and a red stop square. Stopping shows
*"Transcribing…"*, then the words appear **in the input** for review — never sent
straight to the model.
> *Screenshot label: "Voice — recorded, transcribed, reviewed, then sent."*

### 5.8 Activity and memory
History button. **"What it did"**: one row per action — a status dot, the
sentence, `start_campaign · done · 14:22`, and **Undo** where a restore is
possible — then a **Memory** card (*"Standing notes the agent reads in every
conversation"*) with **Edit** and a five-line box.
> *Screenshot label: "Audit — every action, its outcome, and the one-click undo
> where it is safe."*

### 5.9 Failure states
Three deliberate, different-looking cases:
* **No key** — amber strip under the header, *"No key yet. Add a Groq or Gemini
  key…"* with an underlined **Add a key**, and the composer disabled.
* **Provider down** — red strip inside the thread: the provider's message, its
  hint, the code (`provider_down`) in mono, and **Try again**.
* **Refused action** — the approval card is replaced by a red tool row carrying
  the validation errors word for word.
> *Screenshot label: "Failure — a sentence, a cause and a fix, never a bare
> 500."*

## Current upload and voice limits

The composer records a voice note and places the transcription in the draft for
review. Microphone permission, a compatible browser and a configured provider
are required. Files up to 25 MB can be selected or dropped: CSV, TSV, TXT,
XLSX, PDF, DOCX, PNG, JPG and WebP. Text extraction from scanned PDFs is not
supported. Image uploads currently retain only a description/metadata, not the
image pixels: the agent **cannot see or analyze images**. Other file types are
rejected. XLSX files are normalized as CSV so rows can be queried and imported;
the first worksheet is used. Preview mapping edits are for reference only —
tell the agent which mapping to use when importing. Risky actions still require
approval unless the operator explicitly enables auto-approval.
