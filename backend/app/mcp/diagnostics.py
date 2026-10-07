"""
The connector self-test: run the handshake a real AI client runs, and say which
step breaks.

"The connector is not working" is unactionable, because the failure is almost
always in a step the client will not explain: a discovery document that is
missing, a 401 without a ``WWW-Authenticate`` challenge, a ``resource`` that does
not match the endpoint the operator pasted, a token endpoint that only parses one
body encoding, or a tool list with no annotations. This module performs the whole
sequence against the live app, in-process, and records a pass/fail line per step
so the settings screen can show exactly where it stopped.

It is deliberately literal about what each client's documentation demands:

* step ``challenge`` asserts the 401 carries ``resource_metadata`` — Claude "does
  not honor the challenge on a 200 response";
* step ``resource-matches`` asserts the protected-resource document's ``resource``
  equals the endpoint URL — "the resource in protected resource metadata must
  match the MCP server URL exactly as users enter it, including its path";
* steps ``token-form`` and ``token-json`` both have to pass — Claude posts the
  token request as ``application/x-www-form-urlencoded`` and registration as JSON,
  and Anthropic's own checklist says to test both parsers;
* step ``annotations`` asserts every tool carries a ``title`` and a
  ``readOnlyHint``/``destructiveHint`` — required for Claude's directory.

The rate limiter on the public OAuth surface is bypassed for these calls with a
header only this process can produce (an HMAC of the app's own secret key), so
re-running the test cannot lock the operator out of connecting for real.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
from urllib.parse import quote, urlencode
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.mcp import connectors as C
from app.mcp import server as mcp_server
from app.mcp.registry import tools_for
from app.models.mcp import McpToken
from app.utils.urls import public_base_url, public_base_url_source

logger = logging.getLogger(__name__)

SELFTEST_HEADER = "x-mcp-selftest"


def selftest_value() -> str:
    """A header value only this deployment can produce."""
    return hmac.new((settings.SECRET_KEY or "").encode(), b"mcp-selftest", hashlib.sha256
                    ).hexdigest()


def is_selftest(request_headers) -> bool:
    presented = ""
    try:
        presented = request_headers.get(SELFTEST_HEADER) or ""
    except AttributeError:  # pragma: no cover - defensive
        return False
    return bool(presented) and hmac.compare_digest(presented, selftest_value())


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                        ).decode().rstrip("=")
    return verifier, challenge


class _Report:
    """Collects one line per step and stops at the first hard failure."""

    def __init__(self, profile: C.ConnectorProfile):
        self.profile = profile
        self.steps: list[dict] = []
        self.failed = False

    def add(self, step: str, ok: bool, detail: str, *, status: int | None = None,
            fatal: bool = True, fix: str = "") -> bool:
        """Record one step.

        ``fatal=False`` marks advice: a step that is reported (with its fix) but
        does not make the handshake a failure. That distinction matters — a
        deployment that works perfectly with a base URL detected from the
        request must not be shown as "handshake failed", or the operator goes
        looking for a bug that is not there.
        """
        self.steps.append({
            "step": step,
            "ok": bool(ok),
            "fatal": bool(fatal),
            "status": status,
            "detail": detail[:400],
            "fix": fix[:400] if not ok else "",
        })
        if not ok and fatal:
            self.failed = True
        return ok

    def result(self) -> dict:
        first_bad = next((s for s in self.steps if not s["ok"] and s.get("fatal")), None)
        advice = [s for s in self.steps if not s["ok"] and not s.get("fatal")]
        return {
            "connector": self.profile.key,
            "label": self.profile.label,
            "endpoint": self.profile.resource,
            "status": "ok" if not first_bad else "error",
            "error": None if not first_bad else f"{first_bad['step']}: {first_bad['detail']}",
            "passed": sum(1 for s in self.steps if s["ok"]),
            "total": len(self.steps),
            "advice": len(advice),
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "steps": self.steps,
        }


async def run_connector_test(profile: C.ConnectorProfile, db: AsyncSession) -> dict:
    """Every step, in the order a client performs them."""
    from app.main import app as fastapi_app

    report = _Report(profile)
    base = public_base_url()
    source = public_base_url_source()

    # A base URL learned from the request is enough to run every OAuth step —
    # and it is what makes the connector work on a deployment that never set
    # PUBLIC_BASE_URL. It is reported as a non-fatal warning, because a client
    # in *another* network must be able to reach the same host, which only the
    # operator can confirm.
    if not report.add(
        "public-url",
        bool(base),
        f"public URL = {base or '(not set)'} (source: {source})",
        fix=(
            "Could not determine this deployment's public address from the request either. "
            "Set PUBLIC_BASE_URL to the https address clients reach (e.g. "
            "https://your-app.onrender.com) and restart."
        ),
    ):
        return report.result()

    if source == "request":
        report.add(
            "public-url-configured",
            False,
            f"PUBLIC_BASE_URL is unset; using the address of this request ({base}).",
            fatal=False,
            fix=(
                "Fine for a browser session — the URL is derived from the host that asked. Set "
                "PUBLIC_BASE_URL to this deployment's permanent https address so the connector "
                "keeps working when the app is opened on a different host (a custom domain, a "
                "tunnel, or a phone on the same Wi-Fi)."
            ),
        )

    headers = {SELFTEST_HEADER: selftest_value()}
    transport = httpx.ASGITransport(app=fastapi_app, client=("127.0.0.1", 4242))
    async with httpx.AsyncClient(transport=transport, base_url=base, timeout=30.0,
                                 follow_redirects=False) as client:

        # Browser-hosted connectors can fail before the OAuth handshake starts
        # if the provider's Origin is missing from this route's CORS allowlist.
        # Exercise the real preflight, not just an unauthenticated JSON request.
        provider_origins = {"chatgpt": "https://chatgpt.com", "claude": "https://claude.ai"}
        if profile.key in provider_origins:
            origin = provider_origins[profile.key]
            response = await client.options(
                profile.path,
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": (
                        "authorization,content-type,accept,mcp-protocol-version"
                    ),
                },
            )
            allowed_headers = {
                part.strip().lower()
                for part in (response.headers.get("access-control-allow-headers") or "").split(",")
                if part.strip()
            }
            required_headers = {"authorization", "content-type", "accept", "mcp-protocol-version"}
            if not report.add(
                "browser-cors-preflight",
                response.status_code == 204
                and response.headers.get("access-control-allow-origin") == origin
                and response.headers.get("access-control-allow-credentials", "").lower() == "true"
                and required_headers.issubset(allowed_headers),
                f"OPTIONS {profile.path} from {origin} → {response.status_code}; "
                f"allow-origin={response.headers.get('access-control-allow-origin')}, "
                f"allow-headers={sorted(allowed_headers)}",
                status=response.status_code,
                fix=(
                    "Add the exact provider Origin to MCP_CORS_ORIGINS. CORS must allow "
                    "Authorization, Content-Type, Accept and Mcp-Protocol-Version on MCP routes."
                ),
            ):
                return report.result()

        # --- discovery ---------------------------------------------------
        prm_url = profile.well_known_prm()
        response = await client.get(prm_url, headers=headers)
        metadata: dict[str, Any] = {}
        if response.status_code == 200:
            try:
                metadata = response.json()
            except ValueError:
                metadata = {}
        if not report.add(
            "protected-resource-metadata",
            response.status_code == 200 and bool(metadata.get("resource")),
            f"GET {prm_url.replace(base, '')} → {response.status_code}",
            status=response.status_code,
            fix="The RFC 9728 document must be served, unauthenticated, at this exact path.",
        ):
            return report.result()

        report.add(
            "resource-matches",
            str(metadata.get("resource") or "").rstrip("/") == profile.resource.rstrip("/"),
            f"resource={metadata.get('resource')} vs endpoint={profile.resource}",
            fix=(
                "Claude rejects a connector whose protected-resource 'resource' is not exactly the "
                "URL the operator pasted, including its path."
            ),
        )
        report.add(
            "authorization-servers",
            bool(metadata.get("authorization_servers")),
            f"authorization_servers={metadata.get('authorization_servers')}",
            fix="List this deployment's issuer first — Claude only uses the first entry.",
        )

        as_url = f"{base}/.well-known/oauth-authorization-server"
        response = await client.get(as_url, headers=headers)
        as_metadata: dict[str, Any] = {}
        if response.status_code == 200:
            try:
                as_metadata = response.json()
            except ValueError:
                as_metadata = {}
        if not report.add(
            "authorization-server-metadata",
            response.status_code == 200
            and bool(as_metadata.get("authorization_endpoint"))
            and bool(as_metadata.get("token_endpoint")),
            f"GET {as_url.replace(base, '')} → {response.status_code}",
            status=response.status_code,
            fix="RFC 8414 metadata must name the authorize and token endpoints.",
        ):
            return report.result()

        report.add(
            "pkce-s256",
            "S256" in (as_metadata.get("code_challenge_methods_supported") or []),
            f"code_challenge_methods_supported={as_metadata.get('code_challenge_methods_supported')}",
            fix="Both ChatGPT and Claude require S256; 'plain' is removed in OAuth 2.1.",
        )
        if "oauth" in profile.auth_modes:
            report.add(
                "registration-endpoint",
                bool(as_metadata.get("registration_endpoint")),
                f"registration_endpoint={as_metadata.get('registration_endpoint')}",
                fix=(
                    "Without a registration endpoint a client that cannot use a Client ID Metadata "
                    "Document has no way to obtain a client_id."
                ),
            )
            report.add(
                "public-client-supported",
                "none" in (as_metadata.get("token_endpoint_auth_methods_supported") or []),
                f"token_endpoint_auth_methods_supported="
                f"{as_metadata.get('token_endpoint_auth_methods_supported')}",
                fix=(
                    "ChatGPT exchanges a code as a public client (PKCE, no secret). 'none' must be "
                    "advertised or it reports invalid_client."
                ),
            )

        # --- the 401 challenge clients depend on -------------------------
        response = await client.post(
            profile.path,
            headers={**headers, "content-type": "application/json",
                     "accept": "application/json, text/event-stream"},
            content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": C.DEFAULT_PROTOCOL,
                                           "capabilities": {},
                                           "clientInfo": {"name": "selftest", "version": "1"}}}),
        )
        challenge = response.headers.get("www-authenticate") or ""
        wants_challenge = "oauth" in profile.auth_modes
        report.add(
            "challenge",
            response.status_code == 401 and (not wants_challenge or "resource_metadata" in challenge),
            f"{response.status_code} WWW-Authenticate: {challenge or '(missing)'}",
            status=response.status_code,
            fix=(
                "An unauthenticated MCP request must return 401 with "
                'WWW-Authenticate: Bearer resource_metadata="…". Claude ignores a challenge on a '
                "200 and reports the server as broken."
            ),
        )

        if "oauth" in profile.auth_modes:
            redirect_uri = profile.redirect_uris[0] if profile.redirect_uris else \
                "http://localhost:33418/callback"
            verifier, challenge_value = _pkce_pair()
            client_id = ""

            # --- dynamic client registration -----------------------------
            response = await client.post(
                f"{profile.oauth_path}/register",
                headers={**headers, "content-type": "application/json"},
                content=json.dumps({
                    "client_name": f"{profile.label} self-test",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": "read write",
                }),
            )
            if response.status_code in (200, 201):
                try:
                    client_id = response.json().get("client_id") or ""
                except ValueError:
                    client_id = ""
            if not report.add(
                "dynamic-registration",
                bool(client_id),
                f"POST {profile.oauth_path}/register → {response.status_code} "
                f"{response.text[:160]}",
                status=response.status_code,
                fix=(
                    f"{profile.label} must be able to register itself with redirect_uri "
                    f"{redirect_uri}. Check the connector's allowlist."
                ),
            ):
                return report.result()

            # --- authorize: login, then consent --------------------------
            
            # ChatGPT may omit scope entirely. The diagnostic deliberately
            # exercises that provider behavior so a configured write default
            # cannot silently degrade into a read-only token.
            requested_scope = "" if profile.key == "chatgpt" else "&scope=read%20write"
            authorize = (
                f"{profile.oauth_path}/authorize?response_type=code"
                f"&client_id={quote(client_id, safe='')}"
                f"&redirect_uri={quote(redirect_uri, safe='')}"
                f"&code_challenge={challenge_value}&code_challenge_method=S256"
                f"&state=selftest{requested_scope}&resource={quote(profile.resource, safe='')}"
            )
            response = await client.get(authorize, headers=headers)
            if not report.add(
                "authorize-page",
                response.status_code == 200 and ("<form" in response.text),
                f"GET authorize → {response.status_code}, {len(response.text)} bytes of HTML",
                status=response.status_code,
                fix="The operator must be shown a sign-in/consent page they can approve.",
            ):
                return report.result()

            login_fields = (
                {"password": settings.ADMIN_PASSWORD}
                if settings.MCP_OAUTH_REQUIRE_LOGIN else {"one_tap": "1"}
            )
            response = await client.post(authorize, headers=headers, data=login_fields)
            ticket_match = re.search(r'name="ticket"\s+value="([^"]+)"', response.text or "")
            if not report.add(
                "authorize-login",
                response.status_code == 200 and bool(ticket_match),
                f"POST authorize (sign in) → {response.status_code}"
                + ("" if ticket_match else ", no consent page returned"),
                status=response.status_code,
                fix="Signing in must land on the consent page.",
            ):
                return report.result()

            ticket = ticket_match.group(1)
            response = await client.post(
                authorize,
                headers=headers,
                data={"decision": "approve", "ticket": ticket, "scope": ["read", "write"]},
            )
            location = response.headers.get("location") or ""
            code_match = re.search(r"[?&]code=([^&]+)", location)
            if not report.add(
                "authorize-approve",
                response.status_code in (302, 303) and bool(code_match),
                f"POST authorize (approve) → {response.status_code} "
                f"location={location[:120] or '(none)'}",
                status=response.status_code,
                fix="Approving must redirect back to the client's redirect_uri with a code.",
            ):
                return report.result()
            code = code_match.group(1)
            report.add(
                "state-and-iss",
                "state=selftest" in location and ("iss=" in location or not public_base_url()),
                f"location={location[:160]}",
                fatal=False,
                fix=(
                    "The redirect must echo 'state' and carry 'iss' — we advertise "
                    "authorization_response_iss_parameter_supported."
                ),
            )

            # --- token exchange, both encodings --------------------------
            token_body = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": profile.resource,
            }
            response = await client.post(
                f"{profile.oauth_path}/token",
                headers={**headers, "content-type": "application/x-www-form-urlencoded"},
                content=urlencode(token_body),
            )
            tokens: dict[str, Any] = {}
            if response.status_code == 200:
                try:
                    tokens = response.json()
                except ValueError:
                    tokens = {}
            if not report.add(
                "token-form",
                bool(tokens.get("access_token")),
                f"POST token (form-urlencoded) → {response.status_code} {response.text[:160]}",
                status=response.status_code,
                fix=(
                    "Claude posts the token request as application/x-www-form-urlencoded. The "
                    "endpoint must parse that, not only JSON."
                ),
            ):
                return report.result()
            report.add(
                "token-shape",
                tokens.get("token_type", "").lower() == "bearer"
                and bool(tokens.get("expires_in")) and bool(tokens.get("refresh_token")),
                f"token_type={tokens.get('token_type')} expires_in={tokens.get('expires_in')} "
                f"scope={tokens.get('scope')}",
                fatal=False,
                fix="A bearer token response needs token_type, expires_in and a refresh token.",
            )

            access_token = str(tokens.get("access_token"))
            refresh_token = str(tokens.get("refresh_token"))

            response = await client.post(
                f"{profile.oauth_path}/token",
                headers={**headers, "content-type": "application/json"},
                content=json.dumps({
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "resource": profile.resource,
                }),
            )
            refreshed = response.json() if response.status_code == 200 else {}
            report.add(
                "token-json-refresh",
                response.status_code == 200 and bool(refreshed.get("access_token")),
                f"POST token (JSON refresh) → {response.status_code} {response.text[:160]}",
                status=response.status_code,
                fatal=False,
                fix=(
                    "Refresh must work with a JSON body too, and rotate the refresh token — Claude "
                    "refreshes reactively after a 401."
                ),
            )
            if response.status_code == 200:
                report.add(
                    "refresh-scope-preserved",
                    refreshed.get("scope") == tokens.get("scope"),
                    f"original scope={tokens.get('scope')} refreshed scope={refreshed.get('scope')}",
                    fix=(
                        "When refresh scope is omitted, retain the original grant instead of "
                        "downgrading a write token to read-only."
                    ),
                )
            if refreshed.get("access_token"):
                access_token = str(refreshed["access_token"])
        else:
            access_token = ""

        # --- the protocol itself ----------------------------------------
        if not access_token:
            # A connector that only takes a bearer token: mint one for the test.
            raw, hashed, prefix = mcp_server.create_token_value()
            db.add(McpToken(name=f"{profile.label} self-test", token_hash=hashed,
                            prefix=prefix, scope="write", is_active=True))
            await db.commit()
            access_token = raw

        auth_headers = {**headers, "authorization": f"Bearer {access_token}",
                        "content-type": "application/json",
                        "accept": "application/json, text/event-stream"}

        async def rpc(payload: dict) -> httpx.Response:
            return await client.post(profile.path, headers=auth_headers,
                                     content=json.dumps(payload))

        def rpc_body(response: httpx.Response) -> dict | list | None:
            """Read either the JSON body or the single SSE event we return."""
            text = response.text or ""
            if "text/event-stream" in (response.headers.get("content-type") or ""):
                for line in text.splitlines():
                    if line.startswith("data:"):
                        try:
                            return json.loads(line[5:].strip())
                        except ValueError:
                            return None
                return None
            try:
                return response.json()
            except ValueError:
                return None

        response = await rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": C.DEFAULT_PROTOCOL, "capabilities": {},
                                         "clientInfo": {"name": f"{profile.key}-selftest",
                                                        "version": "1.0"}}})
        init = rpc_body(response) or {}
        result = init.get("result") if isinstance(init, dict) else None
        if not report.add(
            "initialize",
            response.status_code == 200 and bool(result and result.get("protocolVersion")),
            f"initialize → {response.status_code} "
            f"protocolVersion={(result or {}).get('protocolVersion')}",
            status=response.status_code,
            fix=(
                "The handshake must answer with a protocolVersion this server supports. "
                f"Supported: {', '.join(C.PROTOCOL_VERSIONS)}."
            ),
        ):
            return report.result()

        response = await rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        report.add(
            "notification-202",
            response.status_code in (200, 202, 204),
            f"notifications/initialized → {response.status_code}",
            status=response.status_code,
            fatal=False,
            fix="A notification has no reply; 202 with an empty body is the expected answer.",
        )

        response = await rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = rpc_body(response) or {}
        tools = (listed.get("result") or {}).get("tools") if isinstance(listed, dict) else None
        expected = tools_for(C.tool_names(profile))
        if not report.add(
            "tools-list",
            response.status_code == 200 and bool(tools),
            f"tools/list → {response.status_code}, {len(tools or [])} tools "
            f"(this connector publishes {len(expected)})",
            status=response.status_code,
            fix="tools/list must return the connector's tool set.",
        ):
            return report.result()

        first = (tools or [{}])[0]
        report.add(
            "annotations",
            bool(first.get("title"))
            and isinstance(first.get("annotations"), dict)
            and ("readOnlyHint" in first["annotations"] or "destructiveHint" in first["annotations"]),
            f"first tool '{first.get('name')}' title={first.get('title')!r} "
            f"annotations={first.get('annotations')}",
            fatal=False,
            fix=(
                "Anthropic requires every tool to carry a title and readOnlyHint/destructiveHint; "
                "ChatGPT uses the same hints to decide what needs confirmation."
            ),
        )

        response = await rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": "how_to_use_this_app", "arguments": {}}})
        called = rpc_body(response) or {}
        call_result = (called.get("result") or {}) if isinstance(called, dict) else {}
        report.add(
            "tools-call",
            response.status_code == 200 and not call_result.get("isError"),
            f"tools/call how_to_use_this_app → {response.status_code}, "
            f"{len(str(call_result.get('content') or ''))} chars",
            status=response.status_code,
            fatal=False,
            fix="A real tool call must succeed end to end, through the app's own API.",
        )

        response = await client.get(profile.path, headers=auth_headers)
        report.add(
            "get-stream",
            response.status_code == 405,
            f"GET {profile.path} → {response.status_code} (405 = no server-initiated stream)",
            status=response.status_code,
            fatal=False,
            fix="Streamable HTTP makes the GET stream optional, but the refusal must be a 405.",
        )

    return report.result()
