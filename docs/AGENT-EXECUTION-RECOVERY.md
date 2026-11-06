# Agent Mode Execution Diagnostics, Root-Cause Analysis & Safe Recovery

## 1. Root-Cause Diagnosis: `SignatureDoesNotMatch` During Agent Execution

### Observed Symptom
An Agent Mode turn failed before reaching the SENDERSMS campaign API — including on local sandbox tool invocations (`bash` `echo`/`date` and `read_file`) — with:

> `The request signature we calculated does not match the signature you provided. Check your secret access key and signing method.`

### End-to-End Trace Across Execution Layers

| Layer | Protocol & Auth | Uses AWS SigV4? | Verdict |
|---|---|---|---|
| **1. Agent Mode Host Workspace / Snapshot Storage** | S3 / R2 pre-signed or SigV4-signed object storage backing sandbox snapshots & tool-call state | **YES (`AWS4-HMAC-SHA256`)** | **Primary Root Cause**: Expired/rotated temporary STS credentials, header canonicalization mismatch, or host clock drift in the external sandbox orchestrator caused local workspace tool calls (`bash`, `read_file`) to fail with S3 `SignatureDoesNotMatch` before any packet left for SENDERSMS. |
| **2. Upstream Model Routing via OpenRouter** | HTTPS Bearer (`sk-or-v1-...`) → upstream model host (e.g., AWS Bedrock) | Upstream Bedrock uses SigV4 internally | **Secondary Vector Hardened**: Previously, `ai_providers._classify()` treated any `403` from OpenRouter as `key_invalid` (`retryable=False`). If an upstream Bedrock region returned a SigV4 `SignatureDoesNotMatch` error through OpenRouter, the turn halted instead of falling back to the next preset model. Now classified as `upstream_error` (`retryable=True`). |
| **3. `tools/arena-connector/arena_mcp_bridge.py`** | Python `urllib.request` over HTTPS with `Authorization: Bearer <SENDERSMS_MCP_TOKEN>` | **NO** | Never computes SigV4. Now hardened to strip/block accidental `X-Amz-*` / `AWS4-HMAC-SHA256` headers, log redacted JSON diagnostics, check clock skew, and retry **only** read-only calls. |
| **4. SENDERSMS `/mcp` & `/api/v1/*`** | OAuth 2.1 / Static Bearer Token (`McpToken`) + Fernet DB encryption | **NO** | Bearer tokens remained valid throughout. Now exposes `/mcp/health`, `/api/v1/mcp/execution-health`, `/api/v1/campaigns/reconcile`, and server-side `Idempotency-Key` + `expected_status` guards. |

---

## 2. Hardening Implemented

1. **Strict Credential Separation (`execution_guard.py`, `mcp.py`, `arena_mcp_bridge.py`)**:
   - Incoming MCP requests carrying `Authorization: AWS4-HMAC-SHA256` or `X-Amz-*` headers are immediately rejected with JSON-RPC `-32001` (`sigv4_credentials_on_bearer_endpoint`) and redacted structured logs.
   - `arena_mcp_bridge.py` refuses tokens containing SigV4 strings and filters `x-amz-*` headers out of `SENDERSMS_MCP_HEADERS`.
2. **Redacted Structured Logging**:
   - `redact_headers()`, `redact_payload()`, and `arena_mcp_bridge.redact_dict()` scrub all Bearer tokens, `sk-*` keys, cookies, passwords, and webhook secrets before logging.
3. **Clock-Skew Detection & Health Endpoints**:
   - `GET /mcp/health` (unauthenticated probe) and `GET /api/v1/mcp/execution-health` compare `X-Client-Timestamp` / `Date` against server UTC and raise an alert when drift exceeds `60s`.
4. **Safe Read-Only Retries Only**:
   - `arena_mcp_bridge.py` retries transient `502`/`503`/`504` and `URLError` failures **only** for read-only methods (`tools/list`, `initialize`, `reconcile_campaign_state`, `list_campaigns`, `get_campaign`, `dashboard_stats`, etc.). Mutating calls are never blindly replayed.
5. **Remote State Reconciliation & Idempotency Ledger**:
   - `GET /api/v1/campaigns/reconcile` (and MCP/Agent tool `reconcile_campaign_state`) inspects live `Campaign` rows (e.g., `#25`, `#26`), `CampaignFollowUp` rules/logs, and pending `ScheduledMessage` counts (e.g., `54` scheduled opener follow-ups), returning `already_active_or_sent` and `safe_action`.
   - `POST /api/v1/campaigns/`, `POST /api/v1/campaigns/{id}/schedule`, `POST /api/v1/campaigns/{id}/start`, `POST /api/v1/campaign-followups/campaigns/{id}`, `POST /api/v1/campaign-followups/{id}/run`, and `POST /api/v1/campaign-followups/campaigns/{id}/process-now` enforce `X-Idempotency-Key` / `idempotency_key` deduplication (24h ledger) and `expected_status` conflict checks (`HTTP 409`).

---

## 3. Operator & Agent Recovery Runbook

Whenever an Agent Mode session experiences a sandbox reset or `SignatureDoesNotMatch` interruption:

1. **Verify Local Sandbox Tools First** (confirm S3 snapshot layer recovered):
   - Execute harmless checks across `bash` (`date -u`), `read_file`, and a third workspace check before invoking external APIs.
2. **Probe MCP Health & Clock Sync**:
   ```bash
   python3 tools/arena-connector/arena_mcp_bridge.py --self-test
   ```
3. **Reconcile Campaigns & Scheduled Follow-Ups Before Mutating**:
   ```bash
   python3 tools/arena-connector/arena_mcp_bridge.py --reconcile 25,26
   ```
   - If `already_active_or_sent: true` or `scheduled_messages.pending_count` already reflects the expected batch (e.g., `54` pending opener follow-ups), **do not re-run** `start_campaign` or follow-up creation.
4. **Supply `X-Idempotency-Key` + `expected_status` on New Mutations**:
   - Pass `X-Idempotency-Key: <deterministic-operation-id>` and `expected_status=draft` when starting or scheduling any remaining draft campaign.
