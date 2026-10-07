"""
The MCP server: what an AI assistant is allowed to do, and how it is asked to do it.

Two layers are covered here, both without a live server:

* **the contract** — tool names, schemas, and the promise that every declared
  tool points at a route this app really has (checked against the app's own
  OpenAPI, so a renamed endpoint cannot silently break the assistant);
* **the translation** — how a tool's arguments become an HTTP request. These are
  the details that are easy to get wrong and invisible from the outside: a
  trailing slash here, a bare-array body there, a query-string-only endpoint,
  multipart for CSV imports.

The end-to-end path (token → JSON-RPC → real endpoint → audit row) is exercised
by ``tools/mcp_smoke.py`` against a running app.
"""

import json

import pytest
from starlette.requests import Request

from app.mcp import server
from app.mcp.registry import TOOLS, TOOLS_BY_NAME, Tool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubSession:
    """Stands in for an AsyncSession: ``_audit`` only adds and flushes."""

    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        return None


def _token(scope: str = "write") -> server.TokenView:
    return server.TokenView(id=1, name="Test assistant", scope=scope, prefix="mcp_test")


def _request(headers: dict | None = None, query: str = "") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": "/mcp", "query_string": query.encode(),
                    "headers": raw})


@pytest.fixture()
def captured(monkeypatch):
    """Capture the HTTP requests the executor builds, without calling the app."""
    calls: list[dict] = []

    async def fake_call_app(method, path, *, query=None, body=None, form=None, files=None):
        calls.append({
            "method": method, "path": path, "query": dict(query or {}),
            "body": body, "form": dict(form or {}),
            "files": {k: (v[0], v[2]) for k, v in (files or {}).items()},
        })
        return 200, {"success": True, "id": 7}

    monkeypatch.setattr(server, "call_app", fake_call_app)
    return calls


async def _run(tool_name: str, token: server.TokenView | None = None, **args):
    tool = TOOLS_BY_NAME[tool_name]
    return await server.run_tool(_StubSession(), token or _token(), tool, args)


# ---------------------------------------------------------------------------
# The tool contract
# ---------------------------------------------------------------------------


def test_tool_names_are_unique_and_described():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))
    for tool in TOOLS:
        assert tool.description and len(tool.description) > 20, tool.name
        assert tool.method in {"GET", "POST", "PUT", "PATCH", "DELETE",
                               "GUIDE", "OPENAPI", "REQUEST", "PREVIEW_TEMPLATE"}, tool.name
        assert tool.scope in {"read", "write"}, tool.name


def test_required_arguments_are_required_in_the_schema():
    for tool in TOOLS:
        schema = tool.input_schema()
        required = set(schema.get("required") or [])
        for param in tool.params:
            if param.required:
                assert param.name in required, f"{tool.name}: {param.name}"
        assert schema["additionalProperties"] is False, tool.name


def test_path_placeholders_are_all_declared():
    for tool in TOOLS:
        for param in tool.params:
            if param.where == "path":
                assert "{" + param.name + "}" in tool.path, f"{tool.name}: {param.name}"
        for chunk in tool.path.split("{")[1:]:
            name = chunk.split("}")[0]
            assert any(p.name == name and p.where == "path" for p in tool.params), \
                f"{tool.name}: {{name}} has no argument"


def test_contact_creation_advertises_either_channel():
    """A contact needs a phone number *or* an email address.

    Neither may be advertised as mandatory: an assistant that believes a phone
    number is required will refuse to create an email-only contact, which is
    exactly what the import and the API now accept. The API still refuses a
    contact with neither, so both fields must be offered.
    """
    create = TOOLS_BY_NAME["create_contact"].input_schema()
    required = create.get("required") or []
    assert "phone_number" not in required
    assert "email" not in required
    assert {"phone_number", "email"} <= set(create["properties"])

    # …and the update endpoint has no such field, so the tool must not advertise one.
    update = TOOLS_BY_NAME["update_contact"].input_schema()
    assert "phone_number" not in update["properties"]


def test_contact_creation_description_states_the_rule():
    """The one thing the schema cannot express: *at least one* of the two."""
    description = TOOLS_BY_NAME["create_contact"].description.lower()
    assert "phone number" in description and "email" in description
    assert "neither" in description


def test_every_declared_tool_points_at_a_real_endpoint():
    """Catch a renamed route here, not in a user's chat with an assistant."""
    from app.main import app as fastapi_app

    spec = fastapi_app.openapi()
    known: set[tuple[str, str]] = set()
    for path, operations in spec.get("paths", {}).items():
        normalised = path.rstrip("/") or "/"
        for method in operations:
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                known.add((method.upper(), normalised))

    import itertools

    for tool in TOOLS:
        if tool.method in {"GUIDE", "OPENAPI", "REQUEST", "PREVIEW_TEMPLATE"}:
            continue
        # A path parameter with an enum (mark-interested, mark-close, …) stands
        # for that set of literal routes — check every one of them.
        segments = [
            [(p.enum or [f"{{{p.name}}}"]) for p in tool.params if p.where == "path"
             and "{" + p.name + "}" in tool.path][i]
            for i in range(len([p for p in tool.params if p.where == "path"
                                and "{" + p.name + "}" in tool.path]))
        ]
        concrete = []
        for combination in itertools.product(*segments) if segments else [()]:
            path = tool.path
            for param, value in zip(
                [p for p in tool.params if p.where == "path" and "{" + p.name + "}" in tool.path],
                combination,
            ):
                path = path.replace("{" + param.name + "}", str(value))
            concrete.append(path.rstrip("/") or "/")
        for path in concrete:
            assert (tool.method.upper(), path) in known, \
                f"{tool.name} → {tool.method} {path} is not a route"


def test_guide_names_the_rules_that_matter():
    guide = server.APP_GUIDE.lower()
    for phrase in ("sms", "email", "opt", "unsubscribe", "list_id", "validate", "consent"):
        assert phrase in guide, phrase
    assert "search_contacts" in server.APP_GUIDE
    assert "how_to_use_this_app" in server.APP_GUIDE_SUMMARY


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_token_values_are_random_and_hashed():
    raw_a, hash_a, prefix_a = server.create_token_value()
    raw_b, hash_b, _ = server.create_token_value()
    assert raw_a != raw_b
    assert raw_a.startswith("mcp_") and prefix_a == raw_a[:12]
    assert hash_a == server.hash_token(raw_a) and hash_a != raw_a
    assert len(hash_a) == 64


@pytest.mark.parametrize(
    "headers,query,expected",
    [
        ({"Authorization": "Bearer abc123"}, "", "abc123"),
        ({"Authorization": "bearer  spaced  "}, "", "spaced"),
        ({"X-MCP-Token": "xyz"}, "", "xyz"),
        ({}, "token=q123", "q123"),
        ({"Authorization": ""}, "", None),
        ({}, "", None),
    ],
)
def test_token_is_read_from_header_or_query(headers, query, expected):
    assert server.token_from_request(_request(headers, query)) == expected


@pytest.mark.asyncio
async def test_authenticate_round_trip():
    """A token created in the app authenticates; a revoked one does not."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import Base
    from app.models.mcp import McpToken

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    raw, hashed, prefix = server.create_token_value()
    async with factory() as db:
        db.add(McpToken(name="ChatGPT", token_hash=hashed, prefix=prefix, scope="read"))
        await db.commit()

    async with factory() as db:
        found = await server.authenticate(db, raw)
        assert found is not None and found.name == "ChatGPT" and not found.can_write
        assert await server.authenticate(db, "mcp_wrong") is None
        assert await server.authenticate(db, None) is None

        # Revoking kills it immediately — the app's promise.
        from sqlalchemy import update
        await db.execute(update(McpToken).values(is_active=False))
        await db.commit()
        assert await server.authenticate(db, raw) is None

    await engine.dispose()


# ---------------------------------------------------------------------------
# Translation: arguments → HTTP request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sms_send_uses_the_query_string(captured):
    """POST /send/ takes everything in the query string — no JSON body."""
    await _run("send_sms_now", body="Hello there", contact_id=4)
    call = captured[0]
    assert call["method"] == "POST" and call["path"] == "/api/v1/send/"
    assert call["query"] == {"body": "Hello there", "contact_id": 4}
    assert call["body"] is None


@pytest.mark.asyncio
async def test_list_creation_puts_the_name_in_the_query(captured):
    await _run("create_contact_list", name="Q4 Leads", description="cold")
    assert captured[0]["path"] == "/api/v1/lists/"
    assert captured[0]["query"] == {"name": "Q4 Leads", "description": "cold"}
    assert captured[0]["body"] is None


@pytest.mark.asyncio
async def test_list_membership_sends_a_bare_array_body(captured):
    """POST /lists/{id}/contacts takes ``list[int]`` as the whole body."""
    await _run("add_contacts_to_list", list_id=3, contact_ids=[1, 2, 3])
    call = captured[0]
    assert call["path"] == "/api/v1/lists/3/contacts"
    assert call["body"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_csv_import_is_multipart_with_an_uploaded_file(captured):
    await _run("import_contacts_csv", csv_text="phone,email\n+2348030000000,a@b.io\n",
               list_id=9, skip_duplicates=False)
    call = captured[0]
    assert call["path"] == "/api/v1/contacts/import/csv"
    assert list(call["files"]) == ["file"]
    assert call["files"]["file"] == ("import.csv", "text/csv")
    assert call["form"]["list_id"] == 9
    assert call["form"]["skip_duplicates"] is False
    assert call["body"] is None, "multipart requests must not also send JSON"


@pytest.mark.asyncio
async def test_tools_whose_body_is_mandatory_always_send_one(captured):
    """POST /campaigns/{id}/schedule rejects a missing body with 422."""
    await _run("schedule_campaign", campaign_id=3)
    assert captured[0]["body"] == {}
    await _run("schedule_campaign", campaign_id=3,
               scheduled_start_at="2030-01-01T09:00:00+01:00")
    assert captured[1]["body"] == {"scheduled_start_at": "2030-01-01T09:00:00+01:00"}


@pytest.mark.asyncio
async def test_path_arguments_are_substituted_and_quoted(captured):
    await _run("get_contact", contact_id=42)
    assert captured[0]["path"] == "/api/v1/contacts/42"
    assert captured[0]["method"] == "GET"

    await _run("app_api_request", method="GET", path="/api/v1/contacts/?search=ada")
    assert captured[1]["path"] == "/api/v1/contacts/"
    assert captured[1]["query"] == {"search": "ada"}


@pytest.mark.asyncio
async def test_empty_arguments_are_not_sent(captured):
    """Blank optional values must not become ``?search=`` on the wire."""
    await _run("search_contacts", search="", per_page=None)
    assert captured[0]["query"] == {}


@pytest.mark.asyncio
async def test_trailing_slash_paths_are_preserved(captured):
    for tool_name, args in (
        ("create_contact", {"phone_number": "+2348030000000"}),
        ("create_template", {"name": "t", "body": "b"}),
        ("create_campaign", {"name": "c", "channel": "sms"}),
        ("create_followup", {"contact_id": 1, "scheduled_at": "2030-01-01T00:00:00Z",
                             "message_text": "hi"}),
    ):
        await _run(tool_name, **args)
    for call in captured:
        assert call["path"].endswith("/"), call["path"]


@pytest.mark.asyncio
async def test_preview_template_uses_the_apps_own_renderer(captured):
    """Two reads then the real preview endpoint — never a second renderer."""
    calls: list[dict] = []

    async def fake_call_app(method, path, **kwargs):
        calls.append({"method": method, "path": path, **kwargs})
        if path == "/api/v1/templates/preview":
            return 200, {"preview": "Hi Ada", "subject": "About Acme"}
        if path.startswith("/api/v1/templates/"):
            return 200, {"id": 5, "name": "Nudge", "channel": "email",
                         "body": "Hi {{first_name}}", "subject": "About {{business_name}}",
                         "html_body": "<p>hi</p>"}
        if path.startswith("/api/v1/contacts/"):
            return 200, {"id": 2, "first_name": "Ada", "business_name": "Acme",
                         "email": "ada@acme-leads.io"}
        return 200, {"preview": "Hi Ada", "subject": "About Acme"}

    import app.mcp.server as mcp_server

    original = mcp_server.call_app
    mcp_server.call_app = fake_call_app  # type: ignore[assignment]
    try:
        outcome = await _run("preview_template", template_id=5, contact_id=2)
    finally:
        mcp_server.call_app = original  # type: ignore[assignment]

    assert outcome.result["isError"] is False
    assert [c["path"] for c in calls] == [
        "/api/v1/templates/5", "/api/v1/contacts/2", "/api/v1/templates/preview",
    ]
    preview_query = calls[2]["query"]
    assert preview_query["body"] == "Hi {{first_name}}"
    assert preview_query["subject"] == "About {{business_name}}"
    assert preview_query["first_name"] == "Ada" and preview_query["business_name"] == "Acme"
    assert outcome.result["structuredContent"]["preview"] == "Hi Ada"


# ---------------------------------------------------------------------------
# Guard rails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_token_cannot_write(captured):
    outcome = await _run("create_contact", _token("read"), phone_number="+2348030000000")
    assert outcome.result["isError"] is True
    assert "read-only" in outcome.result["content"][0]["text"]
    assert captured == [], "a refused call must never reach the app"


@pytest.mark.asyncio
async def test_read_only_token_can_still_read(captured):
    outcome = await _run("dashboard_stats", _token("read"))
    assert outcome.result["isError"] is False
    assert captured[0]["path"] == "/api/v1/dashboard/stats"
@pytest.mark.asyncio


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_read_only_token_cannot_sneak_a_write_through_the_escape_hatch(captured, method):
    outcome = await _run("app_api_request", _token("read"), method=method,
                         path="/api/v1/contacts/", body={})
    assert outcome.result["isError"] is True
    assert captured == []


@pytest.mark.asyncio
async def test_escape_hatch_get_is_allowed_for_read_tokens(captured):
    outcome = await _run("app_api_request", _token("read"), method="GET",
                         path="/api/v1/settings/sending-rules")
    assert outcome.result["isError"] is False
    assert captured[0]["path"] == "/api/v1/settings/sending-rules"


@pytest.mark.asyncio
async def test_escape_hatch_requires_a_leading_slash(captured):
    outcome = await _run("app_api_request", method="GET", path="api/v1/contacts/")
    assert outcome.result["isError"] is True and captured == []


@pytest.mark.asyncio
async def test_refusals_are_recorded_in_the_audit_trail():
    """The operator should be able to see that the AI tried and was fenced in."""
    db = _StubSession()
    tool = TOOLS_BY_NAME["create_contact"]
    await server.run_tool(db, _token("read"), tool, {"phone_number": "+2348030000000"})
    assert len(db.rows) == 1
    row = db.rows[0]
    assert row.ok is False and row.status_code == 403 and "read-only" in row.error
    assert row.tool == "create_contact" and row.token_name == "Test assistant"


@pytest.mark.asyncio
async def test_http_failures_come_back_as_tool_errors(monkeypatch):
    async def failing(method, path, **kwargs):
        return 422, {"detail": "The email body is empty"}

    monkeypatch.setattr(server, "call_app", failing)
    db = _StubSession()
    outcome = await server.run_tool(db, _token(), TOOLS_BY_NAME["send_email_now"],
                                    {"contact_id": 1})
    assert outcome.result["isError"] is True
    assert "The email body is empty" in outcome.result["content"][0]["text"]
    assert db.rows and db.rows[0].ok is False
    assert "The email body is empty" in (outcome.token_error or "")


# ---------------------------------------------------------------------------
# JSON-RPC protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_echoes_a_supported_protocol_version():
    reply = await server.dispatch(_StubSession(), _token(), {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    result = reply["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "sendersms"
    assert result["capabilities"]["tools"] == {"listChanged": False}


@pytest.mark.asyncio
async def test_initialize_falls_back_on_an_unknown_protocol_version():
    reply = await server.dispatch(_StubSession(), _token(), {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "1999-01-01"},
    })
    assert reply["result"]["protocolVersion"] == server.DEFAULT_PROTOCOL


@pytest.mark.asyncio
async def test_notifications_get_no_reply():
    assert await server.dispatch(_StubSession(), _token(),
                                 {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


@pytest.mark.asyncio
async def test_tools_list_is_json_serialisable():
    reply = await server.dispatch(_StubSession(), _token(), {"jsonrpc": "2.0", "id": 2,
                                                             "method": "tools/list"})
    tools = reply["result"]["tools"]
    encoded = json.dumps(tools)  # a prompt object must never leak a callable
    assert len(tools) == len(TOOLS) and "how_to_use_this_app" in encoded


@pytest.mark.asyncio
async def test_prompts_list_is_json_serialisable():
    reply = await server.dispatch(_StubSession(), _token(), {"jsonrpc": "2.0", "id": 3,
                                                             "method": "prompts/list"})
    prompts = reply["result"]["prompts"]
    assert json.dumps(prompts)
    assert all("render" not in prompt for prompt in prompts)
    assert {p["name"] for p in prompts} >= {"run_a_campaign", "triage_the_inbox"}


@pytest.mark.asyncio
async def test_prompt_renders_a_full_briefing():
    reply = await server.dispatch(_StubSession(), _token(), {
        "jsonrpc": "2.0", "id": 4, "method": "prompts/get",
        "params": {"name": "run_a_campaign", "arguments": {"channel": "email"}},
    })
    text = reply["result"]["messages"][0]["content"]["text"]
    assert "how_to_use_this_app" in text and "email" in text and "start_campaign" in text


@pytest.mark.asyncio
async def test_unknown_tool_and_method_report_jsonrpc_errors():
    unknown_tool = await server.dispatch(_StubSession(), _token(), {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "does_not_exist", "arguments": {}},
    })
    assert unknown_tool["error"]["code"] == -32602

    unknown_method = await server.dispatch(_StubSession(), _token(),
                                           {"jsonrpc": "2.0", "id": 6, "method": "nope"})
    assert unknown_method["error"]["code"] == -32601

    no_method = await server.dispatch(_StubSession(), _token(), {"jsonrpc": "2.0", "id": 7})
    assert no_method["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_ping_and_resources():
    assert (await server.dispatch(_StubSession(), _token(),
                                  {"jsonrpc": "2.0", "id": 8, "method": "ping"}))["result"] == {}
    listed = await server.dispatch(_StubSession(), _token(), {"jsonrpc": "2.0", "id": 9,
                                                              "method": "resources/list"})
    uris = {r["uri"] for r in listed["result"]["resources"]}
    assert uris == {"sendsms://guide", "sendsms://reference", "sendsms://inbox/recent"}
    missing = await server.dispatch(_StubSession(), _token(), {
        "jsonrpc": "2.0", "id": 10, "method": "resources/read",
        "params": {"uri": "sendsms://nope"},
    })
    assert missing["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_guide_and_endpoint_catalogue_tools(monkeypatch):
    guide = await server.run_tool(_StubSession(), _token(),
                                  TOOLS_BY_NAME["how_to_use_this_app"], {})
    assert "Operating this app" in guide.result["content"][0]["text"]

    catalogue = await server._endpoint_catalogue()
    assert "POST   /api/v1/campaigns/" in catalogue
    assert "GET    /api/v1/contacts/import/csv" not in catalogue  # POST only
    assert "/api/v1/email/send" in catalogue


def test_tool_is_hashable_by_name():
    assert isinstance(TOOLS_BY_NAME["start_campaign"], Tool)
    assert TOOLS_BY_NAME["start_campaign"].scope == "write"
