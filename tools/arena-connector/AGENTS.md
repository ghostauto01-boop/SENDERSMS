# Connecting an Arena AI agent to this app (SENDERSMS)

Read this file and you can drive the whole application — contacts, lists,
templates, campaigns, SMS and email sending, the two-way inbox, follow-ups and
analytics — through the Model Context Protocol endpoint this app exposes.

Arena Agent Mode has **no custom-connector screen**: its built-in tools are web
search, image generation, coding, file upload and a sandbox with bash. So there
is nothing to click. You reach this app one of three ways, in order of simplicity:

| # | How | When to use it |
|---|-----|----------------|
| A | `curl` straight at the HTTP endpoint | You have bash. **This is the default — no setup at all.** |
| B | `arena_mcp_bridge.py` as a stdio MCP server | Your host can only launch a local command and speak MCP over stdin/stdout. |
| C | `mcp.json` / `mcp.http.json` | Your host reads a config block (Cursor, Cline, Windsurf, Claude Code, MCP Inspector). |

Everything below assumes two values you get from the operator:

```
SENDERSMS_MCP_URL    https://<the-app>/connectors/arena/mcp
SENDERSMS_MCP_TOKEN  a token from the app: Settings → AI (MCP) → Access tokens
```

The exact URL for this deployment is printed on the **Arena AI agent** card in
Settings → AI (MCP). `/connectors/arena/mcp` is the Arena-specific connector: it
accepts a bearer token *and* OAuth, and exposes the full tool set. `/mcp` is the
generic endpoint and behaves identically.

---

## A. curl (recommended)

The transport is **Streamable HTTP**: one endpoint, `POST` only, JSON-RPC 2.0 in
the body. There is no `initialize` handshake to perform and no session to keep —
the server is stateless — but you must send both `Accept` types, because the
server may answer with `application/json` or with a single `text/event-stream`
event.

```bash
export URL="https://your-app.example.com/connectors/arena/mcp"
export TOKEN="paste-your-token"

call() {   # call <method> <params-json> [id]
  curl -sS "$URL" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer $TOKEN" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":${3:-1},\"method\":\"$1\",\"params\":${2:-{\}}}"
}
```

The first call you should ever make is the guide — it returns this app's own
channels, sending rules and the safe order of operations:

```bash
call tools/call '{"name":"how_to_use_this_app","arguments":{}}'
```

Then list what else is available (60 tools):

```bash
call tools/list '{}'
```

Read something:

```bash
call tools/call '{"name":"dashboard_stats","arguments":{}}'
call tools/call '{"name":"search_contacts","arguments":{"query":"ada","limit":10}}'
call tools/call '{"name":"email_inbox","arguments":{"unread_only":true}}'
```

Write something — always validate a campaign before starting it, and send a test
before sending to real people:

```bash
call tools/call '{"name":"create_contact","arguments":{"first_name":"Ada","email":"ada@acme.com","phone_number":"+2348012345678"}}'
call tools/call '{"name":"create_contact_list","arguments":{"name":"April pilot"}}'
call tools/call '{"name":"add_contacts_to_list","arguments":{"list_id":1,"contact_ids":[1,2,3]}}'
call tools/call '{"name":"create_campaign","arguments":{"name":"April pilot","channel":"email","subject":"Quick question","body":"Hi {{first_name}}, …"}}'
call tools/call '{"name":"validate_campaign","arguments":{"campaign_id":1}}'
call tools/call '{"name":"send_test_email","arguments":{"to":"you@example.com","subject":"test","body":"test"}}'
call tools/call '{"name":"start_campaign","arguments":{"campaign_id":1}}'
call tools/call '{"name":"campaign_analytics","arguments":{"campaign_id":1}}'
```

### Reading the response

`application/json` answers are the JSON-RPC result directly. A `text/event-stream`
answer wraps it — strip the `data:` prefix:

```bash
curl -sS "$URL" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | sed -n 's/^data: //p'
```

A tool result looks like this; `isError` is the flag that matters, and `text` is
usually JSON encoded as a string:

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\"contacts\": …}"}],"isError":false}}
```

---

## B. The stdio bridge

For a host that can only launch a process. No dependencies — Python 3 standard
library only, so it works in a sandbox with no package index.

```bash
export SENDERSMS_MCP_URL="https://your-app.example.com/connectors/arena/mcp"
export SENDERSMS_MCP_TOKEN="paste-your-token"
python3 tools/arena-connector/arena_mcp_bridge.py
```

It reads one JSON-RPC message per line from stdin and writes one per line to
stdout; diagnostics go to stderr. Try it by hand:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | SENDERSMS_MCP_URL="$URL" SENDERSMS_MCP_TOKEN="$TOKEN" \
    python3 tools/arena-connector/arena_mcp_bridge.py
```

Optional environment: `SENDERSMS_MCP_TIMEOUT` (seconds, default 120),
`SENDERSMS_MCP_HEADERS` (extra headers as a JSON object).

## C. Config blocks

* `mcp.json` — the bridge (stdio). Works everywhere a `command`-based server does.
* `mcp.http.json` — the remote endpoint with an `Authorization` header, for hosts
  that support `"type": "http"` natively. Prefer this one when the host offers it:
  no extra process, and no Python in the sandbox.

Fix the `url` and the token in whichever file you use, then point your host at it.

Claude Code, if that is the client you are actually in:

```bash
claude mcp add --transport http sendsms "$URL" --header "Authorization: Bearer $TOKEN"
```

---

## How to behave once you are in

1. **Call `how_to_use_this_app` first.** It is the app's own briefing: which
   channels are configured, what the sending limits are, what the compliance rules
   say. Everything else is easier after it.
2. **Read before you write.** `dashboard_stats`, `search_contacts`,
   `list_campaigns`, `email_inbox` cost nothing and prevent duplicate contacts and
   campaigns that were already sent.
3. **Never send to real people without a test first.** `send_test_email` /
   `send_sms_now` to your own address, then `validate_campaign`, then
   `start_campaign`.
4. **Ask before anything irreversible or expensive**: `start_campaign`,
   `send_followup_now`, `delete_campaign`, bulk `import_contacts_csv`, or any send
   to more than a handful of recipients. State the audience size and the exact
   message before you do it.
5. **Use the app's own reply tools** (`reply_to_email`, `reply_to_conversation`)
   rather than composing something new: they keep the threading headers, so the
   reply lands in the same conversation instead of starting a new one.
6. **A read-only token cannot write.** If a call comes back with a permission
   error, the token was created as *Read only* — say so, and ask for a Read &
   write token instead of trying to work around it.
7. **Report failures verbatim.** The server returns a plain sentence and, where it
   can, the fix. Pass that on rather than paraphrasing it into "it didn't work".

---

## When it does not connect

| Symptom | Cause | Fix |
|---|---|---|
| `401` with `WWW-Authenticate: Bearer resource_metadata="…"` | No token, or a revoked/expired one. This is the *correct* response for an unauthenticated request — an OAuth client uses it to discover where to sign in. | Send `Authorization: Bearer <token>`, or create a new token in Settings → AI (MCP). |
| `403` | The token is read-only and you tried to write. | Ask for a Read & write token. |
| `404` on the URL | The path is wrong. | It is `/connectors/arena/mcp` (or `/mcp`), including the leading path. |
| `Could not reach …` / TLS error | The sandbox cannot see the deployment, or `PUBLIC_BASE_URL` points somewhere that does not resolve. | The app must be reachable from where you are running, over HTTPS. |
| Every URL in the OAuth metadata is relative | `PUBLIC_BASE_URL` is unset on the server. | Set it to the app's public address and restart; the Settings → AI (MCP) screen says so in red when it is missing. |
| `405 Method Not Allowed` with `Allow: POST, DELETE` | You sent a `GET` expecting an SSE stream. | This server is stateless: `POST` only. |

---

## What this endpoint is, precisely

* **Transport:** MCP Streamable HTTP, JSON-RPC 2.0, `POST` only.
* **Protocol versions:** `2026-07-28`, `2025-06-18`, `2025-03-26`, `2024-11-05`
  (send `MCP-Protocol-Version` if your host does; the server does not require it).
* **Auth on this connector:** `Authorization: Bearer <token>`, or OAuth 2.1 with
  PKCE (S256) via the app's own login and consent screens. Discovery documents
  live at `/.well-known/oauth-protected-resource/connectors/arena/mcp` and
  `/.well-known/oauth-authorization-server`.
* **Stateless:** no session id is issued. If one comes back, the bridge replays it
  for older servers.
* **Tools:** 60 on the `full` tool set (this connector and Claude). ChatGPT's
  connector deliberately exposes the focused 33-tool `core` set instead, because
  OpenAI's guidance is to publish what an assistant actually needs.
* **Logging:** every call is recorded with its tool, arguments, duration and
  outcome, and is visible in the app under Settings → AI (MCP). Assume the
  operator reads it.
