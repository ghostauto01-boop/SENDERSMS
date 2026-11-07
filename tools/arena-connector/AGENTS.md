# Connecting an Arena AI agent to this app (SENDERSMS)

Read this file and you can drive the whole application — contacts, lists,
templates, campaigns, SMS and email sending, the two-way inbox, follow-ups and
analytics — through the Model Context Protocol endpoint this app exposes.

Arena Agent Mode has **no custom-connector screen**: its built-in tools are web
search, image generation, coding, file upload and a sandbox with bash. You reach
this app one of three ways, in order of simplicity:

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

---

## Credential Isolation & Recovery Rules (CRITICAL)

1. **Workspace Storage vs. Campaign MCP Credentials**:
   - SENDERSMS MCP uses **only** `Authorization: Bearer <SENDERSMS_MCP_TOKEN>` (or OAuth 2.1). It **never** uses AWS S3 / Cloudflare R2 SigV4 signing (`AWS4-HMAC-SHA256` or `X-Amz-*`).
   - If Agent Mode surfaces `The request signature we calculated does not match the signature you provided. Check your secret access key and signing method.` even on local `bash` (`echo`/`date`) or `read_file` calls before any packet reaches SENDERSMS, that error originates in the **Agent Mode sandbox object-storage / snapshot layer** (or an upstream Bedrock SigV4 model host), **not** `SENDERSMS_MCP_TOKEN`.
   - Never forward `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or `X-Amz-*` headers to `SENDERSMS_MCP_URL` — both `arena_mcp_bridge.py` and `/mcp` actively block SigV4 header contamination.
   - Never print or log `SENDERSMS_MCP_TOKEN`, `OPENROUTER_API_KEY`, or SMS-Gate passwords in plain text.

2. **Reconcile Remote State Before Retrying Any Mutation**:
   - After **any** interrupted turn, sandbox reset, or ambiguous timeout, **DO NOT blindly replay** `create_campaign`, `start_campaign`, `schedule_campaign`, `send_sms`, `send_email`, or follow-up execution (for example, when Campaigns `#25` / `#26` or `54` scheduled opener follow-ups may already be queued or running).
   - Always call `reconcile_campaign_state` (or `GET /api/v1/campaigns/reconcile?campaign_ids=25,26`) **first**, inspect `already_active_or_sent` and `scheduled_messages.pending_count`, and include an `X-Idempotency-Key` header (or `idempotency_key` field) + `expected_status` on any subsequent write.

---

## A. curl (recommended)

```bash
export URL="https://your-app.example.com/connectors/arena/mcp"
export TOKEN="paste-your-token"

call() {   # call <method> <params-json> [id]
  curl -sS "$URL" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Authorization: Bearer $TOKEN" \
    -H "X-Client-Timestamp: $(date +%s)" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":${3:-1},\"method\":\"$1\",\"params\":${2:-{\}}}"
}
```

### 1. Health & Clock-Skew Probe

```bash
curl -sS "https://your-app.example.com/mcp/health" \
  -H "X-Client-Timestamp: $(date +%s)"
```

### 2. Reconcile Active/Pending Campaigns Before Mutating

```bash
call tools/call '{"name":"reconcile_campaign_state","arguments":{"campaign_ids":"25,26"}}'
```

### 3. Safe Idempotent Campaign Creation & Launch

```bash
call tools/call '{"name":"how_to_use_this_app","arguments":{}}'
call tools/list '{}'
call tools/call '{"name":"create_campaign","arguments":{"name":"April pilot","channel":"sms","list_id":1,"message_body":"Hi {{first_name}}","idempotency_key":"pilot-2026-11-07-v1"}}'
call tools/call '{"name":"validate_campaign","arguments":{"campaign_id":1}}'
call tools/call '{"name":"start_campaign","arguments":{"campaign_id":1}}'
```

---

## B. The Hardened stdio Bridge (`arena_mcp_bridge.py`)

Uses only the Python 3 standard library. Retries transient `502`/`503`/`504` and transport drops **only** on read-only calls, redacts secrets in stderr logs, checks server `Date` clock skew, and blocks AWS SigV4 header leakage.

```bash
export SENDERSMS_MCP_URL="https://your-app.example.com/connectors/arena/mcp"
export SENDERSMS_MCP_TOKEN="paste-your-token"

# 1. Verify connectivity, clock sync, and credential isolation:
python3 tools/arena-connector/arena_mcp_bridge.py --self-test

# 2. Reconcile remote Campaigns (e.g. #25, #26) and pending scheduled follow-ups:
python3 tools/arena-connector/arena_mcp_bridge.py --reconcile 25,26

# 3. Start stdio JSON-RPC bridge:
python3 tools/arena-connector/arena_mcp_bridge.py
```

## C. Config blocks

* `mcp.json` — the stdio bridge (`arena_mcp_bridge.py`).
* `mcp.http.json` — native Streamable HTTP endpoint with `Authorization: Bearer ...`.
