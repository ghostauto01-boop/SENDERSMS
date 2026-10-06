#!/usr/bin/env python3
"""stdio ↔ HTTP bridge for the SENDERSMS MCP server.

Why this file exists
--------------------
Some MCP hosts can only launch a local command and speak JSON-RPC over stdin /
stdout — Arena Agent Mode's sandbox being the notable one, since it has no
custom-connector screen at all. This app's MCP server is a *remote* Streamable
HTTP endpoint. This bridge is the fifty lines that join the two: it reads one
JSON-RPC message per line from stdin, POSTs it to the server, and writes the
response back on stdout as one line.

It uses nothing but the Python 3 standard library, so it runs in a bare sandbox
with no `pip install` and no network access to a package index.

Usage
-----
    export SENDERSMS_MCP_URL="https://your-app.example.com/connectors/arena/mcp"
    export SENDERSMS_MCP_TOKEN="sk-..."        # optional: OAuth-only endpoints ignore it
    python3 tools/arena-connector/arena_mcp_bridge.py

Or point a client at it with `tools/arena-connector/mcp.json`.

Protocol notes
--------------
* **Stateless.** The 2026-07-28 MCP core has no `initialize` requirement and no
  session lifecycle, so the bridge forwards every message verbatim and never
  answers on the server's behalf. If the server *does* return an
  `Mcp-Session-Id`, the bridge remembers it and replays it, which keeps older
  (2025-03-26) servers working too.
* **Both response shapes.** A Streamable HTTP server may answer with
  `application/json` or with `text/event-stream` carrying one or more `data:`
  events. Both are handled; with SSE, every JSON-RPC message in the stream is
  written out on its own line.
* **Notifications** (requests with no `id`) are forwarded and produce no output,
  per JSON-RPC 2.0.
* **Errors never kill the bridge.** A transport failure is reported as a
  JSON-RPC error object for that one request, so the host stays usable.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = float(os.environ.get("SENDERSMS_MCP_TIMEOUT", "120"))
PROTOCOL_VERSIONS = os.environ.get(
    "SENDERSMS_MCP_PROTOCOL_VERSIONS",
    "2026-07-28, 2025-06-18, 2025-03-26, 2024-11-05",
)


def _log(message: str) -> None:
    """Diagnostics go to stderr — stdout is the JSON-RPC channel and nothing else."""
    print(f"[arena-mcp-bridge] {message}", file=sys.stderr, flush=True)


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
    headers = {
        "Content-Type": "application/json",
        # The server answers with JSON or with SSE; asking for both is required.
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSIONS.split(",")[0].strip(),
    }
    token = (os.environ.get("SENDERSMS_MCP_TOKEN") or os.environ.get("MCP_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    extra = os.environ.get("SENDERSMS_MCP_HEADERS")
    if extra:
        try:
            headers.update({str(k): str(v) for k, v in json.loads(extra).items()})
        except ValueError:
            _log("SENDERSMS_MCP_HEADERS is not valid JSON — ignoring it")
    return url, headers


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _rpc_error(request_id, code: int, message: str, data=None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
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


class Bridge:
    def __init__(self) -> None:
        self.url, self.headers = _config()
        self.session_id: str | None = None
        _log(f"forwarding to {self.url}")

    def _headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def send(self, message: dict) -> None:
        request_id = message.get("id")
        body = json.dumps(message).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                raw = response.read().decode("utf-8", "replace")
                content_type = (response.headers.get("Content-Type") or "").lower()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            content_type = (exc.headers.get("Content-Type") or "").lower() if exc.headers else ""
            status = exc.code
            # A 401 here means the token is wrong, expired or revoked — the most
            # common failure, so say it plainly instead of dumping a body.
            if status in (401, 403):
                challenge = (exc.headers.get("WWW-Authenticate") or "") if exc.headers else ""
                detail = _first_json(raw) or raw[:400]
                if request_id is not None:
                    _write(_rpc_error(
                        request_id, -32001,
                        f"The server refused the credentials (HTTP {status}).",
                        {"www_authenticate": challenge, "body": detail,
                         "hint": "Check SENDERSMS_MCP_TOKEN, or sign in with OAuth — this "
                                 "endpoint's URL is in the app under Settings → AI (MCP)."},
                    ))
                else:
                    _log(f"HTTP {status}: {detail}")
                return
            if request_id is not None:
                _write(_rpc_error(request_id, -32603, f"HTTP {status} from the MCP server",
                                  _first_json(raw) or raw[:400]))
            else:
                _log(f"HTTP {status}: {raw[:200]}")
            return
        except urllib.error.URLError as exc:
            if request_id is not None:
                _write(_rpc_error(
                    request_id, -32000, f"Could not reach {self.url}",
                    {"reason": str(exc.reason),
                     "hint": "The MCP server must be reachable from this machine, over HTTPS, "
                             "from the public address in PUBLIC_BASE_URL."},
                ))
            else:
                _log(f"unreachable: {exc.reason}")
            return
        except Exception as exc:  # noqa: BLE001 — keep the bridge alive for the next message
            if request_id is not None:
                _write(_rpc_error(request_id, -32603, f"Transport error: {exc}"))
            else:
                _log(f"transport error: {exc}")
            return

        if status == 202 or not raw.strip():
            # 202 Accepted is the correct answer to a notification: nothing to relay.
            if request_id is not None:
                _log(f"server accepted the request but returned no body (HTTP {status})")
            return

        if "text/event-stream" in content_type:
            messages = _parse_sse(raw)
            if not messages and request_id is not None:
                _write(_rpc_error(request_id, -32603, "The SSE stream carried no JSON-RPC message",
                                  raw[:400]))
            for message_out in messages:
                _write(message_out)
            return

        parsed = _first_json(raw)
        if parsed is None:
            if request_id is not None:
                _write(_rpc_error(request_id, -32700, "The server did not return JSON",
                                  raw[:400]))
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


def main() -> None:
    bridge = Bridge()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _log(f"skipping a line that is not JSON: {line[:120]}")
            continue
        if isinstance(message, list):
            # Batching was removed from MCP in 2025-06-18, but an older host may
            # still send one; forward the parts individually.
            for part in message:
                if isinstance(part, dict):
                    bridge.send(part)
            continue
        if isinstance(message, dict):
            bridge.send(message)
    _log("stdin closed; exiting")


if __name__ == "__main__":
    main()
