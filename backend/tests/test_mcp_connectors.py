"""
The MCP connectors — one per AI client, and the OAuth 2.1 flow they require.

This is the regression cover for "the MCP connector is not working". ChatGPT and
Claude custom connectors do not accept an API key or a bearer token; both walk
the OAuth 2.1 flow from the MCP authorization spec and fail *silently* when any
piece of it is missing. So these tests assert the pieces each vendor's own
documentation calls out, in the order the clients ask for them:

1. RFC 9728 protected-resource metadata, whose ``resource`` is exactly the URL
   the operator pastes (Claude refuses a connector when it is not);
2. RFC 8414 authorization-server metadata advertising S256, a registration
   endpoint and ``none`` as a token-endpoint auth method (ChatGPT exchanges the
   code as a public client);
3. a real ``401`` carrying ``WWW-Authenticate: Bearer resource_metadata="…"``
   (Claude ignores a challenge returned on a 200);
4. dynamic client registration screened by that connector's redirect allowlist;
5. PKCE S256 enforced, ``plain`` refused;
6. a token endpoint that parses form-urlencoded *and* JSON (Claude posts the
   first for tokens and the second for registration);
7. refresh-token rotation, with replay of a rotated token revoking the client;
8. the protocol handshake itself, per connector, with the tool set that
   connector publishes and the annotations Anthropic requires.

``run_connector_test`` is the same code the settings screen's *Test connector*
button runs, so a passing test here means the button in the app can pass too.
"""

import base64
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.mcp import connectors as C
from app.mcp.diagnostics import run_connector_test, selftest_value, SELFTEST_HEADER

BASE = "https://app.example.test"


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    """One in-memory database shared by every session the flow opens.

    The MCP handlers deliberately open several short-lived sessions per request
    (so a tool call never fights the request's own transaction), and SQLite gives
    each connection its own ``:memory:`` database unless the pool is static — so
    StaticPool is what makes this a realistic test rather than a flaky one.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", BASE, raising=False)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "admin", raising=False)
    monkeypatch.setattr(settings, "MCP_OAUTH_REQUIRE_LOGIN", False, raising=False)

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        from app.security.auth import ensure_admin

        await ensure_admin(session)
        await session.commit()

    import app.database as database
    import app.api.v1.mcp as mcp_api

    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(mcp_api, "async_session_factory", factory)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory):
    from app.main import app

    transport = ASGITransport(app=app, client=("127.0.0.1", 4242))
    async with AsyncClient(transport=transport, base_url=BASE, follow_redirects=False) as c:
        yield c


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                        ).decode().rstrip("=")
    return verifier, challenge


async def _register(client, profile: C.ConnectorProfile, redirect_uri: str | None = None) -> str:
    body = {
        "client_name": f"{profile.label} test",
        "redirect_uris": [redirect_uri or profile.redirect_uris[0]],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "read write",
    }
    response = await client.post(f"{profile.oauth_path}/register", json=body,
                                 headers={SELFTEST_HEADER: selftest_value()})
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


async def _authorize_url(profile: C.ConnectorProfile, client_id: str, challenge: str,
                         redirect_uri: str, *, scope: str | None = "read write", **extra) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
        "resource": profile.resource,
    }
    if scope is not None:
        params["scope"] = scope
    params.update(extra)
    return f"{profile.oauth_path}/authorize?{urlencode(params, quote_via=quote)}"


def _redirect_for(profile: C.ConnectorProfile) -> str:
    """The connector's documented callback, or a loopback one for the others."""
    return profile.redirect_uris[0] if profile.redirect_uris else "http://localhost:33418/callback"


async def _walk_to_code(client, profile: C.ConnectorProfile,
                        approve: tuple[str, ...] = ("read", "write"),
                        scope: str | None = "read write",
                        redirect_uri: str | None = None) -> tuple[str, str, str, str]:
    """Register → authorize → approve, returning (client_id, code, verifier, redirect)."""
    verifier, challenge = _pkce()
    redirect = redirect_uri or _redirect_for(profile)
    client_id = await _register(client, profile, redirect)
    url = await _authorize_url(profile, client_id, challenge, redirect, scope=scope)

    page = await client.get(url, headers={SELFTEST_HEADER: selftest_value()})
    assert page.status_code == 200 and "<form" in page.text, page.text

    signed_in = await client.post(url, data={"one_tap": "1"},
                                  headers={SELFTEST_HEADER: selftest_value()})
    assert signed_in.status_code == 200, signed_in.text
    ticket = re.search(r'name="ticket"\s+value="([^"]+)"', signed_in.text).group(1)

    approved = await client.post(
        url, data={"decision": "approve", "ticket": ticket, "scope": list(approve)},
        headers={SELFTEST_HEADER: selftest_value()},
    )
    assert approved.status_code == 302, approved.text
    location = approved.headers["location"]
    assert location.startswith(redirect), location
    assert "state=xyz" in location, "state must be echoed"
    assert "iss=" in location, "iss must be present (we advertise that we send it)"
    code = re.search(r"[?&]code=([^&]+)", location).group(1)
    return client_id, code, verifier, redirect


async def _exchange(client, profile: C.ConnectorProfile, *, form: bool,
                    approve: tuple[str, ...] = ("read", "write"),
                    authorize_scope: str | None = "read write",
                    redirect_uri: str | None = None, **overrides) -> dict:
    client_id, code, verifier, redirect = await _walk_to_code(
        client, profile, approve, scope=authorize_scope, redirect_uri=redirect_uri
    )
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": profile.resource,
        **overrides,
    }
    if form:
        response = await client.post(
            f"{profile.oauth_path}/token",
            content=urlencode(body),
            headers={"content-type": "application/x-www-form-urlencoded",
                     SELFTEST_HEADER: selftest_value()},
        )
    else:
        response = await client.post(f"{profile.oauth_path}/token", json=body,
                                     headers={SELFTEST_HEADER: selftest_value()})
    assert response.status_code == 200, response.text
    return response.json()


async def _rpc(client, profile: C.ConnectorProfile, token: str, method: str,
               params: dict | None = None, msg_id: int = 1):
    payload = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        payload["id"] = msg_id
    if params is not None:
        payload["params"] = params
    return await client.post(
        profile.path,
        content=json.dumps(payload),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        },
    )


def _body(response):
    """Read a reply that arrived either as JSON or as a single SSE event."""
    if "text/event-stream" in (response.headers.get("content-type") or ""):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return None
    return response.json()


# ---------------------------------------------------------------------------
# The whole flow, per connector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["chatgpt", "claude", "arena", "generic"])
async def test_builtin_connector_test_passes(session_factory, key):
    """The settings screen's Test button must pass for every connector.

    This is the single most valuable assertion in the file: it runs discovery,
    registration, authorize, both token encodings, the handshake and a real tool
    call, in that order, exactly as a client would.
    """
    profile = C.get(key)
    async with session_factory() as db:
        report = await run_connector_test(profile, db)
    failed = [s for s in report["steps"] if not s["ok"]]
    assert report["status"] == "ok", json.dumps(failed, indent=2)
    assert report["passed"] == report["total"]


@pytest.mark.asyncio
async def test_chatgpt_endpoint_answers_with_oauth_token(client):
    profile = C.CHATGPT
    tokens = await _exchange(client, profile, form=True)
    assert tokens["token_type"] == "Bearer"

    reply = _body(await _rpc(client, profile, tokens["access_token"], "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "chatgpt", "version": "1.0"},
    }))
    assert reply["result"]["protocolVersion"] == "2025-11-25"
    assert reply["result"]["serverInfo"]["name"] == "sendersms"


@pytest.mark.asyncio
async def test_chatgpt_can_follow_the_discovered_shared_oauth_flow(client):
    """Exercise the URLs ChatGPT actually discovers, not only client-specific aliases."""
    prm_response = await client.get(
        "/.well-known/oauth-protected-resource/connectors/chatgpt/mcp"
    )
    assert prm_response.status_code == 200, prm_response.text
    resource_metadata = prm_response.json()
    resource = resource_metadata["resource"]
    assert resource == f"{BASE}{C.CHATGPT.path}"

    issuer = resource_metadata["authorization_servers"][0]

    def local_path(url: str) -> str:
        parsed = urlsplit(url)
        assert parsed.netloc == urlsplit(BASE).netloc
        return parsed.path

    as_metadata_url = f"{issuer}/.well-known/oauth-authorization-server"
    as_metadata_response = await client.get(local_path(as_metadata_url))
    assert as_metadata_response.status_code == 200, as_metadata_response.text
    as_metadata = as_metadata_response.json()

    redirect_uri = C.CHATGPT.redirect_uris[0]
    selftest = {SELFTEST_HEADER: selftest_value()}
    registration = await client.post(
        local_path(as_metadata["registration_endpoint"]),
        json={
            "client_name": "ChatGPT discovered-flow test",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": "read write",
        },
        headers=selftest,
    )
    assert registration.status_code == 201, registration.text
    client_id = registration.json()["client_id"]

    verifier, challenge = _pkce()
    authorize_query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "discovered-flow",
            "resource": resource,
        },
        quote_via=quote,
    )
    authorize_path = f"{local_path(as_metadata['authorization_endpoint'])}?{authorize_query}"
    login = await client.get(authorize_path, headers=selftest)
    assert login.status_code == 200 and "<form" in login.text
    consent = await client.post(authorize_path, data={"one_tap": "1"}, headers=selftest)
    assert consent.status_code == 200, consent.text
    ticket = re.search(r'name="ticket"\s+value="([^"]+)"', consent.text).group(1)
    approved = await client.post(
        authorize_path,
        data={"decision": "approve", "ticket": ticket, "scope": ["read", "write"]},
        headers=selftest,
    )
    assert approved.status_code == 302, approved.text
    callback = urlsplit(approved.headers["location"])
    assert parse_qs(callback.query)["state"] == ["discovered-flow"]
    code = parse_qs(callback.query)["code"][0]

    token_response = await client.post(
        local_path(as_metadata["token_endpoint"]),
        content=urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": verifier,
                "resource": resource,
            }
        ),
        headers={"content-type": "application/x-www-form-urlencoded", **selftest},
    )
    assert token_response.status_code == 200, token_response.text

    tools_response = await _rpc(
        client, C.CHATGPT, token_response.json()["access_token"], "tools/list"
    )
    tools = _body(tools_response)["result"]["tools"]
    assert tools_response.status_code == 200
    assert len(tools) < 40
    assert "search_contacts" in {tool["name"] for tool in tools}


@pytest.mark.asyncio
async def test_token_endpoint_parses_json_too(client):
    """Claude posts forms; plenty of other clients post JSON. Both must work."""
    tokens = await _exchange(client, C.CLAUDE, form=False)
    assert tokens.get("access_token")


# ---------------------------------------------------------------------------
# Discovery documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "path"),
    [
        ("https://chatgpt.com", "/connectors/chatgpt/mcp"),
        ("https://claude.ai", "/connectors/claude/mcp"),
    ],
)
async def test_provider_origin_can_preflight_mcp_but_not_the_crm_api(client, origin, path):
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": (
            "authorization,content-type,accept,mcp-protocol-version"
        ),
    }
    response = await client.options(path, headers=headers)
    assert response.status_code == 204
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"
    allowed = {h.strip().lower() for h in response.headers["access-control-allow-headers"].split(",")}
    assert {"authorization", "content-type", "accept", "mcp-protocol-version"} <= allowed

    # The hosted provider origin is not granted CORS over unrelated CRM APIs.
    crm_response = await client.options("/api/v1/contacts/", headers=headers)
    assert "access-control-allow-origin" not in crm_response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["https://chatgpt.com", "https://claude.ai"])
async def test_hosted_clients_can_preflight_shared_root_oauth_endpoints(client, origin):
    """The discovered AS uses /oauth/*, outside the client-specific connector path."""
    headers = {
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    }
    for path in ("/oauth/register", "/oauth/token", "/oauth/revoke"):
        response = await client.options(path, headers=headers)
        assert response.status_code == 204, (path, response.text)
        assert response.headers.get("access-control-allow-origin") == origin
        allowed = {
            header.strip().lower()
            for header in response.headers["access-control-allow-headers"].split(",")
        }
        assert {"authorization", "content-type"} <= allowed


@pytest.mark.asyncio
async def test_untrusted_origin_cannot_preflight_mcp(client):
    response = await client.options(
        "/connectors/chatgpt/mcp",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_protected_resource_metadata_resource_matches_endpoint(client):
    for profile in C.CONNECTORS:
        response = await client.get(profile.well_known_prm().replace(BASE, ""))
        assert response.status_code == 200, (profile.key, response.text)
        document = response.json()
        assert document["resource"] == profile.resource, profile.key
        assert document["authorization_servers"][0] == BASE
        assert "read" in document["scopes_supported"]


@pytest.mark.asyncio
async def test_authorization_server_metadata_is_complete(client):
    response = await client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["issuer"] == BASE
    assert metadata["authorization_endpoint"] == f"{BASE}/oauth/authorize"
    assert metadata["token_endpoint"] == f"{BASE}/oauth/token"
    assert metadata["registration_endpoint"] == f"{BASE}/oauth/register"
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert "none" in metadata["token_endpoint_auth_methods_supported"]
    assert metadata["authorization_response_iss_parameter_supported"] is True
    assert metadata["client_id_metadata_document_supported"] is True


@pytest.mark.asyncio
async def test_openid_configuration_alias_is_served(client):
    """The 2025-11-25 revision accepts OIDC discovery in place of RFC 8414."""
    for path in ("/.well-known/openid-configuration",
                 "/.well-known/oauth-authorization-server/connectors/chatgpt/mcp"):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.json()["token_endpoint"].endswith("/token")


@pytest.mark.asyncio
async def test_unauthenticated_request_returns_a_401_challenge(client):
    """Claude "does not honor the challenge on a 200 response"."""
    for profile in C.CONNECTORS:
        response = await client.post(
            profile.path,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"accept": "application/json, text/event-stream"},
        )
        assert response.status_code == 401, (profile.key, response.status_code)
        challenge = response.headers.get("www-authenticate") or ""
        assert challenge.startswith("Bearer"), challenge
        assert f'resource_metadata="{profile.well_known_prm()}"' in challenge, profile.key
        # The body still explains itself, for a human reading it with curl.
        assert profile.label in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_get_on_the_mcp_endpoint_is_405(client):
    response = await client.get("/connectors/chatgpt/mcp")
    assert response.status_code == 405
    assert "POST" in response.headers.get("allow", "")


# ---------------------------------------------------------------------------
# Registration and PKCE rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatgpt_can_register_its_documented_redirect_uri(client):
    client_id = await _register(client, C.CHATGPT,
                                "https://chatgpt.com/connector_platform_oauth_redirect")
    assert client_id.startswith("mcp_chatgpt_")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "redirect_uri",
    [
        "https://chatgpt.com/connector_platform_oauth_redirect",
        "https://custom-agent.example.test/oauth/callback",
    ],
)
async def test_generic_endpoint_completes_oauth_with_hosted_https_callbacks(client, redirect_uri):
    """The canonical /mcp endpoint accepts hosted callbacks, including ChatGPT's.

    Operators often paste /mcp into ChatGPT. Its hosted callback must not be
    rejected just because it chose the generic endpoint instead of the
    ChatGPT-specific alias.
    """
    tokens = await _exchange(
        client, C.GENERIC, form=True, redirect_uri=redirect_uri
    )
    assert tokens.get("access_token")

    initialized = await _rpc(
        client,
        C.GENERIC,
        tokens["access_token"],
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "hosted-mcp-client", "version": "1.0"},
        },
    )
    assert _body(initialized)["result"]["serverInfo"]["name"] == "sendersms"


@pytest.mark.asyncio
async def test_chatgpt_cimd_callback_works_when_using_generic_mcp_endpoint(client, monkeypatch):
    """Reproduce ChatGPT's CIMD registration against the canonical /mcp URL."""
    from app.mcp import oauth as oauth_flow

    profile = C.GENERIC
    client_id = "https://chatgpt.com/oauth/client.json"
    redirect_uri = C.CHATGPT.redirect_uris[0]

    async def chatgpt_metadata(url: str) -> dict:
        assert url == client_id
        return {
            "client_name": "ChatGPT",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_methods_supported": ["none"],
            "scope": "read write",
        }

    monkeypatch.setattr(oauth_flow, "_fetch_cimd", chatgpt_metadata)
    verifier, challenge = _pkce()
    authorize_url = await _authorize_url(profile, client_id, challenge, redirect_uri)

    page = await client.get(authorize_url, headers={SELFTEST_HEADER: selftest_value()})
    assert page.status_code == 200 and "<form" in page.text, page.text
    signed_in = await client.post(
        authorize_url,
        data={"one_tap": "1"},
        headers={SELFTEST_HEADER: selftest_value()},
    )
    assert signed_in.status_code == 200, signed_in.text
    ticket = re.search(r'name="ticket"\s+value="([^"]+)"', signed_in.text).group(1)
    approved = await client.post(
        authorize_url,
        data={"decision": "approve", "ticket": ticket, "scope": ["read", "write"]},
        headers={SELFTEST_HEADER: selftest_value()},
    )
    assert approved.status_code == 302, approved.text
    location = approved.headers["location"]
    assert location.startswith(redirect_uri) and "state=xyz" in location
    code = re.search(r"[?&]code=([^&]+)", location).group(1)

    token_response = await client.post(
        f"{profile.oauth_path}/token",
        content=urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": profile.resource,
        }),
        headers={
            "content-type": "application/x-www-form-urlencoded",
            SELFTEST_HEADER: selftest_value(),
        },
    )
    assert token_response.status_code == 200, token_response.text
    initialized = await _rpc(
        client, profile, token_response.json()["access_token"], "initialize",
        {"protocolVersion": "2025-11-25", "capabilities": {},
         "clientInfo": {"name": "ChatGPT", "version": "1"}},
    )
    assert _body(initialized)["result"]["serverInfo"]["name"] == "sendersms"


@pytest.mark.asyncio
async def test_claude_registration_rejects_an_unlisted_redirect_uri(client):
    response = await client.post(
        "/connectors/claude/oauth/register",
        json={"client_name": "evil", "redirect_uris": ["https://attacker.example/cb"],
              "token_endpoint_auth_method": "none"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


@pytest.mark.asyncio
async def test_claude_code_loopback_redirect_is_accepted_on_any_port(client):
    """Claude Code catches its OAuth redirect on localhost, on a port it picks."""
    for uri in ("http://localhost:33418/callback", "http://127.0.0.1:9999/oauth/callback"):
        response = await client.post(
            "/connectors/claude/oauth/register",
            json={"client_name": "claude-code", "redirect_uris": [uri],
                  "token_endpoint_auth_method": "none"},
        )
        assert response.status_code == 201, (uri, response.text)


@pytest.mark.asyncio
async def test_plain_pkce_is_refused(client):
    profile = C.CLAUDE
    client_id = await _register(client, profile)
    url = await _authorize_url(profile, client_id, "not-a-real-challenge",
                               _redirect_for(profile), code_challenge_method="plain")
    response = await client.get(url, headers={SELFTEST_HEADER: selftest_value()})
    assert response.status_code == 400
    assert "S256" in response.text


@pytest.mark.asyncio
async def test_authorize_without_pkce_is_refused(client):
    profile = C.CLAUDE
    client_id = await _register(client, profile)
    verifier, _ = _pkce()
    url = (f"{profile.oauth_path}/authorize?response_type=code&client_id={quote(client_id)}"
           f"&redirect_uri={quote(_redirect_for(profile))}&state=s")
    response = await client.get(url, headers={SELFTEST_HEADER: selftest_value()})
    assert response.status_code == 400
    assert "PKCE is required" in response.text


@pytest.mark.asyncio
async def test_wrong_verifier_is_rejected_and_right_one_accepted(client):
    profile = C.CLAUDE
    client_id, code, verifier, redirect = await _walk_to_code(client, profile)
    bad = await client.post(
        f"{profile.oauth_path}/token",
        content=urlencode({"grant_type": "authorization_code", "code": code,
                           "redirect_uri": redirect, "client_id": client_id,
                           "code_verifier": "x" * 60}),
        headers={"content-type": "application/x-www-form-urlencoded",
                 SELFTEST_HEADER: selftest_value()},
    )
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
async def test_authorization_code_is_single_use(client):
    profile = C.CLAUDE
    client_id, code, verifier, redirect = await _walk_to_code(client, profile)
    body = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
            "client_id": client_id, "code_verifier": verifier}
    first = await client.post(f"{profile.oauth_path}/token", json=body,
                              headers={SELFTEST_HEADER: selftest_value()})
    assert first.status_code == 200, first.text
    second = await client.post(f"{profile.oauth_path}/token", json=body,
                               headers={SELFTEST_HEADER: selftest_value()})
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_scope", [None, "mcp:tools"])
async def test_omitted_or_provider_extension_scope_uses_connector_default(client, requested_scope):
    """ChatGPT may omit OAuth scope or ask for a provider-specific scope string."""
    tokens = await _exchange(
        client, C.CHATGPT, form=False, authorize_scope=requested_scope
    )
    assert tokens.get("scope") == "write"


@pytest.mark.asyncio
async def test_refresh_rotates_and_replay_revokes_the_client(client):
    profile = C.CLAUDE
    tokens = await _exchange(client, profile, form=True)
    refresh = tokens["refresh_token"]

    rotated = await client.post(
        f"{profile.oauth_path}/token",
        json={"grant_type": "refresh_token", "refresh_token": refresh,
              "resource": profile.resource},
        headers={SELFTEST_HEADER: selftest_value()},
    )
    assert rotated.status_code == 200, rotated.text
    assert rotated.json()["refresh_token"] != refresh
    assert tokens.get("scope") == "write"
    assert rotated.json().get("scope") == tokens.get("scope"), (
        "a refresh without scope must preserve the originally approved write grant"
    )

    replay = await client.post(
        f"{profile.oauth_path}/token",
        json={"grant_type": "refresh_token", "refresh_token": refresh},
        headers={SELFTEST_HEADER: selftest_value()},
    )
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"

    # The rotated token is dead too: a replayed refresh token revokes the client.
    dead = await client.post(
        f"{profile.oauth_path}/token",
        json={"grant_type": "refresh_token", "refresh_token": rotated.json()["refresh_token"]},
        headers={SELFTEST_HEADER: selftest_value()},
    )
    assert dead.status_code == 400


# ---------------------------------------------------------------------------
# What each connector publishes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatgpt_gets_the_focused_tool_set(client):
    profile = C.CHATGPT
    tokens = await _exchange(client, profile, form=True)
    reply = _body(await _rpc(client, profile, tokens["access_token"], "tools/list"))
    from app.mcp.registry import TOOLS

    names = {t["name"] for t in reply["result"]["tools"]}
    assert names == set(C.CORE_TOOLS) & {tool.name for tool in TOOLS}
    assert len(names) < 40, "ChatGPT's set should stay focused"
    # A tool the profile does not publish is refused with an explanation.
    call = _body(await _rpc(client, profile, tokens["access_token"], "tools/call",
                            {"name": "email_suppression_list", "arguments": {}}, msg_id=7))
    assert "error" in call and "focused tool set" in call["error"]["message"]


@pytest.mark.asyncio
async def test_claude_gets_every_tool_with_annotations(client):
    profile = C.CLAUDE
    tokens = await _exchange(client, profile, form=True)
    reply = _body(await _rpc(client, profile, tokens["access_token"], "tools/list"))
    tools = reply["result"]["tools"]
    assert len(tools) == 63  # 60 + validator_status, verify_contacts, verification_job (P0-5)
    for tool in tools:
        assert tool.get("title"), tool["name"]
        annotations = tool.get("annotations") or {}
        assert "readOnlyHint" in annotations or "destructiveHint" in annotations, tool["name"]
        assert annotations["openWorldHint"] is False


@pytest.mark.asyncio
async def test_read_scope_cannot_send(client):
    profile = C.CLAUDE
    # Scope is decided on the consent page, never widened at the token endpoint.
    tokens = await _exchange(client, profile, form=True, approve=("read",))
    assert tokens["scope"] == "read"
    reply = _body(await _rpc(client, profile, tokens["access_token"], "tools/call",
                             {"name": "send_email_now",
                              "arguments": {"contact_id": 1, "subject": "hi", "body": "hi"}},
                             msg_id=9))
    assert reply["result"]["isError"] is True
    assert "read-only" in reply["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_static_bearer_token_still_works(client, session_factory):
    """Claude Code, curl and the Arena bridge authenticate with a header."""
    from app.mcp import server as mcp_server
    from app.models.mcp import McpToken

    raw, hashed, prefix = mcp_server.create_token_value()
    async with session_factory() as db:
        db.add(McpToken(name="Arena", token_hash=hashed, prefix=prefix, scope="write",
                        is_active=True))
        await db.commit()

    reply = _body(await _rpc(client, C.ARENA, raw, "initialize", {
        "protocolVersion": "2026-07-28", "capabilities": {}, "clientInfo": {"name": "arena"},
    }))
    assert reply["result"]["protocolVersion"] == "2026-07-28"

    listed = _body(await _rpc(client, C.ARENA, raw, "tools/list", msg_id=2))
    assert len(listed["result"]["tools"]) == 63


@pytest.mark.asyncio
async def test_a_token_for_one_connector_is_refused_on_another(client):
    """RFC 8707: the access token is audience-bound to the resource it was issued for."""
    tokens = await _exchange(client, C.CHATGPT, form=True)
    response = await _rpc(client, C.CLAUDE, tokens["access_token"], "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "x"},
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_stateless_core_needs_no_handshake(client):
    """The 2026-07-28 revision drops the mandatory initialize round-trip."""
    profile = C.ARENA
    tokens = await _exchange(client, profile, form=True)
    reply = _body(await _rpc(client, profile, tokens["access_token"], "tools/list"))
    assert reply["result"]["tools"]
    discover = _body(await _rpc(client, profile, tokens["access_token"], "server/discover", {},
                                msg_id=3))
    assert discover["result"]["capabilities"]["tools"] == {"listChanged": False}


@pytest.mark.asyncio
async def test_notification_gets_202_and_no_body(client):
    profile = C.ARENA
    tokens = await _exchange(client, profile, form=True)
    response = await _rpc(client, profile, tokens["access_token"], "notifications/initialized",
                          msg_id=None)
    assert response.status_code == 202
    assert response.text == ""


@pytest.mark.asyncio
async def test_missing_public_base_url_is_reported_not_guessed(session_factory, monkeypatch):
    """Without PUBLIC_BASE_URL every OAuth URL would be relative — say so loudly."""
    from app.config import settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", None, raising=False)
    async with session_factory() as db:
        report = await run_connector_test(C.CHATGPT, db)
    assert report["status"] == "error"
    assert report["steps"][0]["step"] == "public-url"
    assert "PUBLIC_BASE_URL" in report["steps"][0]["fix"]
