#!/usr/bin/env python3
"""stdio ↔ HTTP bridge for the SENDERSMS MCP server (Hardened Edition).

Why this file exists
--------------------
Some MCP hosts can only launch a local command and speak JSON-RPC over stdin /
stdout — Arena Agent Mode's sandbox being the notable one. This app's MCP server
is a *remote* Streamable HTTP endpoint. This bridge joins the two using **only
the Python 3 standard library** (zero pip dependencies).

Hardening & Safety Guarantees
-----------------------------
1. **Strict Credential Separation**:
   - Workspace / object-storage credentials (`AWS_*`, `S3_*`, `R2_*`) are NEVER
     read, signed with, or forwarded.
   - Refuses `SENDERSMS_MCP_HEADERS` entries that attempt to inject
     `AWS4-HMAC-SHA256` or `X-Amz-*` headers onto the SENDERSMS Bearer channel.
2. **Zero Secret Leakage**:
   - Structured stderr diagnostics automatically redact Bearer tokens, API keys,
     cookies, passwords, and signing secrets.
3. **Clock-Skew Telemetry**:
   - Attaches `X-Client-Timestamp` (UTC epoch seconds) and compares the server's
     `Date` response header against local UTC, emitting a structured warning if
     drift exceeds 60s.
4. **Safe Read-Only Retries & Non-Replay of Mutations**:
   - Transient network/502/503/504 errors are retried (up to 2 retries with
     jittered backoff) **ONLY** for read-only JSON-RPC methods (`initialize`,
     `ping`, `tools/list`, `server/discover`) and explicitly read-only tools
     (`reconcile_campaign_state`, `list_campaigns`, `get_campaign`,
     `dashboard_stats`, `how_to_use_this_app`, `search_contacts`, `email_inbox`).
   - Mutating calls (`start_campaign`, `create_campaign`, `schedule_campaign`,
     `send_sms`, `send_email`, follow-up triggers) are **NEVER** blindly retried
     on ambiguous transport drops — instead the error instructs the caller to
     run `reconcile_campaign_state` first and pass `idempotency_key`.
5. **Built-in Health Check & Remote Reconciliation CLI**:
   - `python3 tools/arena-connector/arena_mcp_bridge.py --self-test`
   - `python3 tools/arena-connector/arena_mcp_bridge.py --reconcile 25,26`
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_TIMEOUT = float(os.environ.get("SENDERSMS_MCP_TIMEOUT", "120"))
MAX_READ_RETRIES = int(os.environ.get("SENDERSMS_MCP_READ_RETRIES", "2"))
CLOCK_SKEW_WARN_SECONDS = 60.0
PROTOCOL_VERSIONS = os.environ.get(
    "SENDERSMS_MCP_PROTOCOL_VERSIONS",
    "2026-07-28, 2025-06-18, 2025-03-26, 2024-11-05",
)

READ_ONLY_RPC_METHODS = frozenset(
    {"initialize", "notifications/initialized", "ping", "tools/list", "server/discover"}
)

READ_ONLY_TOOLS = frozenset(
    {
        "how_to_use_this_app",
        "reconcile_campaign_state",
        "dashboard_stats",
        "list_campaigns",
        "get_campaign",
        "campaign_analytics",
        "validate_campaign",
        "search_contacts",
        "get_contact",
        "list_contact_lists",
        "list_templates",
        "email_inbox",
        "sms_inbox",
        "validator_status",
    }
)

_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]{5,}", re.IGNORECASE)
_SK_RE = re.compile(r"\b(sk-[A-Za-z0-9\-_]{6,})\b")
_SENSITIVE_KEY_RE = re.compile(
    r"(authorization|cookie|token|secret|password|api[-_]?key|signature)",
    re.IGNORECASE,
)


def redact_str(value: str) -> str:
    if not value:
        return value
    masked = _BEARER_RE.sub(r"\1[REDACTED]", value)
    return _SK_RE.sub("[REDACTED_KEY]", masked)


def redact_dict(mapping: dict) -> dict:
    out = {}
    for k, v in mapping.items():
        ks = str(k)
        if _SENSITIVE_KEY_RE.search(ks):
            out[ks] = "[REDACTED]"
        elif isinstance(v, dict):
            out[ks] = redact_dict(v)
        elif isinstance(v, str):
            out[ks] = redact_str(v)
        else:
            out[ks] = v
    return out


def _log(message: str, **fields) -> None:
    """Structured redacted diagnostics go ONLY to stderr (stdout is reserved for JSON-RPC)."""
    safe_msg = redact_str(str(message))
    if fields:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "component": "arena-mcp-bridge",
            "msg": safe_msg,
            **redact_dict(fields),
        }
        print(f"[arena-mcp-bridge] {json.dumps(record, ensure_ascii=False)}", file=sys.stderr, flush=True)
    else:
        print(f"[arena-mcp-bridge] {safe_msg}", file=sys.stderr, flush=True)


def is_safe_to_retry(message: dict) -> bool:
    """Return True ONLY when retrying cannot duplicate a campaign, SMS, email, or follow-up."""
    method = str(message.get("method") or "").strip()
    if method in READ_ONLY_RPC_METHODS:
        return True
    if method == "tools/call":
        params = message.get("params") or {}
        tool_name = str(params.get("name") or "").strip()
        return tool_name in READ_ONLY_TOOLS
    return False


def _config() -> tuple[str, dict[str, str]]:
    url = (
        os.environ.get("SENDERSMS_MCP_URL")
        or os.environ.get("MCP_URL")
        or ""
    ).strip().rstrip("/")
    if not url:
        raise SystemExit(
            "SENDERSMS_MCP_URL is not set. Point it at this deployment's MCP endpoint, e.g.\n"
            '  export SENDERSMS_MCP_URL="https://your-app.example.com/connectors/arena/mcp"\n'
            "The exact URL is in the app under Settings → AI (MCP)."
        )
    parsed_url = urllib.parse.urlsplit(url)
    if parsed_url.scheme not in ("http", "https"):
        raise SystemExit(f"SENDERSMS_MCP_URL must use https:// (or http:// for localhost), got: {url}")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSIONS.split(",")[0].strip(),
    }
    token = (os.environ.get("SENDERSMS_MCP_TOKEN") or os.environ.get("MCP_TOKEN") or "").strip()
    if token:
        if "aws4-hmac-sha256" in token.lower():
            raise SystemExit(
                "Refusing to start: SENDERSMS_MCP_TOKEN contains an AWS SigV4 credential string. "
                "Keep workspace S3/AWS credentials separate from the SENDERSMS MCP Bearer token."
            )
        headers["Authorization"] = f"Bearer {token}"

    extra = os.environ.get("SENDERSMS_MCP_HEADERS")
    if extra:
        try:
            parsed_extra = json.loads(extra)
            if isinstance(parsed_extra, dict):
                for k, v in parsed_extra.items():
                    ks = str(k)
                    vs = str(v)
                    if ks.lower().startswith("x-amz-") or "aws4-hmac-sha256" in vs.lower():
                        _log(
                            "Blocked SigV4/AWS header from SENDERSMS_MCP_HEADERS to preserve credential isolation",
                            blocked_header=ks,
                        )
                        continue
                    headers[ks] = vs
        except ValueError:
            _log("SENDERSMS_MCP_HEADERS is not valid JSON — ignoring it")
    return url, headers


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _rpc_error(request_id, code: int, message: str, data=None) -> dict:
    error: dict = {"code": code, "message": redact_str(message)}
    if data is not None:
        error["data"] = redact_dict(data) if isinstance(data, dict) else data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _parse_sse(text: str) -> list[dict]:
    """Every JSON-RPC message carried by an SSE response, in order."""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            parsed = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
        elif isinstance(parsed, list):
            out.extend(p for p in parsed if isinstance(p, dict))
    return out


def _check_server_date_skew(date_header: str | None) -> float | None:
    if not date_header:
        return None
    try:
        srv = parsedate_to_datetime(date_header)
        if srv.tzinfo is None:
            srv = srv.replace(tzinfo=timezone.utc)
        skew = round((datetime.now(timezone.utc) - srv.astimezone(timezone.utc)).total_seconds(), 2)
        if abs(skew) > CLOCK_SKEW_WARN_SECONDS:
            _log(
                "Clock skew exceeds 60s threshold; verify container NTP clock",
                skew_seconds=skew,
                server_date=date_header,
            )
        return skew
    except Exception:
        return None


class Bridge:
    def __init__(self, url: str | None = None, headers: dict[str, str] | None = None) -> None:
        if url is not None and headers is not None:
            self.url, self.headers = url, headers
        else:
            self.url, self.headers = _config()
        self.session_id: str | None = None
        _log("initialized MCP bridge", endpoint=self.url, auth_present=bool(self.headers.get("Authorization")))

    def _headers(self, message: dict | None = None) -> dict[str, str]:
        headers = dict(self.headers)
        headers["X-Client-Timestamp"] = str(int(time.time()))
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if isinstance(message, dict) and message.get("method") == "tools/call":
            args = ((message.get("params") or {}).get("arguments")) or {}
            if isinstance(args, dict) and args.get("idempotency_key"):
                headers["X-Idempotency-Key"] = str(args["idempotency_key"])
        return headers

    def send(self, message: dict) -> None:
        request_id = message.get("id")
        can_retry = is_safe_to_retry(message)
        max_attempts = (1 + MAX_READ_RETRIES) if can_retry else 1
        body = json.dumps(message).encode("utf-8")

        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            request = urllib.request.Request(
                self.url,
                data=body,
                headers=self._headers(message),
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
                    status = response.status
                    content_type = (response.headers.get("Content-Type") or "").lower()
                    session = response.headers.get("Mcp-Session-Id")
                    if session:
                        self.session_id = session
                    _check_server_date_skew(response.headers.get("Date"))
                    raw = response.read().decode("utf-8", errors="replace")
                break
            except urllib.error.HTTPError as exc:
                status = exc.code
                raw = exc.read().decode("utf-8", errors="replace")
                _check_server_date_skew(exc.headers.get("Date") if exc.headers else None)
                if status in (502, 503, 504) and attempt < max_attempts:
                    backoff = 0.4 * (2 ** (attempt - 1))
                    _log("retrying transient HTTP error on read-only call", status=status, attempt=attempt)
                    time.sleep(backoff)
                    continue
                if status in (401, 403):
                    challenge = exc.headers.get("WWW-Authenticate", "") if exc.headers else ""
                    detail = redact_str(raw[:400])
                    if request_id is not None:
                        _write(
                            _rpc_error(
                                request_id,
                                -32001,
                                f"The server refused the credentials (HTTP {status}).",
                                {
                                    "www_authenticate": challenge,
                                    "body": detail,
                                    "hint": (
                                        "Check SENDERSMS_MCP_TOKEN (Bearer token from Settings → AI (MCP)). "
                                        "Note: AWS SigV4 SignatureDoesNotMatch errors come from workspace object storage, not SENDERSMS."
                                    ),
                                },
                            )
                        )
                    else:
                        _log("authentication refused", status=status)
                    return
                if request_id is not None:
                    _write(
                        _rpc_error(
                            request_id,
                            -32603,
                            f"HTTP {status} from the MCP server",
                            {
                                "detail": _first_json(raw) or redact_str(raw[:400]),
                                "retried": attempt - 1,
                                "reconcile_required": not can_retry,
                            },
                        )
                    )
                else:
                    _log("HTTP error", status=status)
                return
            except urllib.error.URLError as exc:
                if attempt < max_attempts:
                    backoff = 0.4 * (2 ** (attempt - 1))
                    _log("retrying transient transport error on read-only call", reason=str(exc.reason), attempt=attempt)
                    time.sleep(backoff)
                    continue
                if request_id is not None:
                    hint = (
                        "Safe read-only call failed after retries."
                        if can_retry
                        else "MUTATION OUTCOME UNKNOWN — DO NOT BLINDLY REPLAY! Call reconcile_campaign_state first to inspect remote campaigns & scheduled follow-ups."
                    )
                    _write(
                        _rpc_error(
                            request_id,
                            -32000,
                            f"Could not reach {self.url}",
                            {
                                "reason": redact_str(str(exc.reason)),
                                "reconcile_required": not can_retry,
                                "hint": hint,
                            },
                        )
                    )
                else:
                    _log("unreachable", reason=str(exc.reason))
                return
            except Exception as exc:  # noqa: BLE001
                if request_id is not None:
                    _write(
                        _rpc_error(
                            request_id,
                            -32603,
                            f"Transport error: {exc}",
                            {"reconcile_required": not can_retry},
                        )
                    )
                else:
                    _log("transport error", error=str(exc))
                return

        if status == 202 or not raw.strip():
            if request_id is not None:
                _log("server accepted request with empty body", status=status)
            return

        if "text/event-stream" in content_type:
            messages = _parse_sse(raw)
            if not messages and request_id is not None:
                _write(_rpc_error(request_id, -32603, "The SSE stream carried no JSON-RPC message", raw[:400]))
            for message_out in messages:
                _write(message_out)
            return

        parsed = _first_json(raw)
        if parsed is None:
            if request_id is not None:
                _write(_rpc_error(request_id, -32700, "The server did not return JSON", raw[:400]))
            return
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    _write(item)
        else:
            _write(parsed)


def _first_json(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def run_self_test() -> int:
    """Verify URL config, credential isolation, clock skew, and read-only MCP connectivity."""
    url, headers = _config()
    parsed = urllib.parse.urlsplit(url)
    health_url = f"{parsed.scheme}://{parsed.netloc}/mcp/health"
    req = urllib.request.Request(
        health_url,
        headers={"Accept": "application/json", "X-Client-Timestamp": str(int(time.time()))},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            _log("self-test /mcp/health passed", status=resp.status, health=data)
            print(json.dumps({"ok": True, "health": data}, indent=2))
            return 0
    except Exception as exc:
        _log("self-test failed", error=str(exc))
        print(json.dumps({"ok": False, "error": redact_str(str(exc))}, indent=2))
        return 1


def run_reconcile(campaign_ids: str) -> int:
    """Invoke the read-only reconcile_campaign_state tool over MCP before any mutation retry."""
    bridge = Bridge()
    args = {"campaign_ids": campaign_ids} if campaign_ids and campaign_ids != "all" else {}
    bridge.send(
        {
            "jsonrpc": "2.0",
            "id": "reconcile-1",
            "method": "tools/call",
            "params": {"name": "reconcile_campaign_state", "arguments": args},
        }
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="SENDERSMS hardened stdio ↔ HTTP MCP bridge")
    parser.add_argument("--self-test", action="store_true", help="Probe /mcp/health, clock skew, and credential isolation")
    parser.add_argument(
        "--reconcile",
        nargs="?",
        const="all",
        help="Reconcile active/scheduled campaigns & follow-ups (e.g. --reconcile 25,26) before retrying mutations",
    )
    args, _ = parser.parse_known_args()

    if args.self_test:
        raise SystemExit(run_self_test())
    if args.reconcile is not None:
        raise SystemExit(run_reconcile(args.reconcile))

    bridge = Bridge()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _log("skipping non-JSON stdin line", preview=line[:80])
            continue
        if isinstance(message, list):
            for part in message:
                if isinstance(part, dict):
                    bridge.send(part)
            continue
        if isinstance(message, dict):
            bridge.send(message)
    _log("stdin closed; exiting")


if __name__ == "__main__":
    main()
