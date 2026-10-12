"""Sprint 2 — C7: a tool's parameters must match the endpoint behind it, and a default a tool
advertises must be what really happens.

Why this is a test and not a hope: the registry is hand-written, and the endpoints change
under it. Three bugs of one family were found in this sweep --

* ``reply_to_conversation`` sent ``body`` in the JSON body to an endpoint that read it from the
  query string (C3), so every call failed;
* ``list_followups`` advertised nothing and silently got the endpoint's "today, SMS only" defaults (C4);
* ``update_contact`` advertised ``country: Nigeria`` as a default (C5) -- and the dispatcher never
  applied advertised defaults at all, so the schema promised one thing and the call did another.

This walks every declared tool against the app's own OpenAPI document and fails, listing ALL the
mismatches at once, when:

1. a parameter is sent where the endpoint does not read it (a query name the endpoint does not
   declare, a body field its model does not have, a path placeholder that is not in the route);
2. the endpoint REQUIRES something the tool neither requires nor defaults;
3. an enum offers a value the endpoint rejects;
4. an advertised default differs from the endpoint's own default without being listed in
   ``INTENTIONAL_DEFAULTS`` with the reason (the assistant's defaults are allowed to differ -- a
   page size of 25 so a reply fits -- but never by accident);
5. an advertised maximum is above the endpoint's.
"""

from __future__ import annotations

import pytest

from app.mcp.registry import TOOLS
from app.mcp.server import _run_declared  # noqa: F401  (the dispatcher that applies the defaults)

HTTP = {"GET", "POST", "PUT", "PATCH", "DELETE"}

#: (tool, parameter) -> why the assistant's default intentionally differs from the endpoint's.
INTENTIONAL_DEFAULTS = {
    ("list_followups", "view"): "the page opens on 'due today'; an assistant wants every pending follow-up",
    ("list_followups", "channel"): "the page is SMS-only; an assistant wants both channels",
    ("inbox_conversations", "channel"): "the dashboard's SMS inbox is SMS-only; an assistant should see email threads too",
    ("inbox_conversations", "per_page"): "a smaller page than the dashboard's, so one reply holds it",
    ("email_inbox", "per_page"): "a smaller page than the dashboard's, so one reply holds it",
    ("send_history", "per_page"): "a smaller page than the dashboard's, so one reply holds it",
    ("export_contacts_csv", "limit"): "100 rows fit one page's 20 KB budget; the endpoint's 200 assumes a bigger max_bytes",
}

#: Tool parameters that are not sent to this endpoint at all (consumed by the server, or an alias).
NOT_SENT = {"meta", "arg"}


def _spec() -> dict:
    from app.main import app

    return app.openapi()


def _deref(schema: dict, spec: dict) -> dict:
    """Follow ``$ref``, and unwrap ``Optional[X]`` (``anyOf: [X, null]``) / ``allOf: [X]``."""
    seen = 0
    while seen < 8:
        seen += 1
        if "$ref" in schema:
            node = spec
            for part in schema["$ref"].lstrip("#/").split("/"):
                node = node[part]
            schema = node
        elif "anyOf" in schema or "oneOf" in schema:
            options = [o for o in schema.get("anyOf") or schema.get("oneOf") if o.get("type") != "null"]
            if len(options) == 1:
                merged = {k: v for k, v in schema.items() if k not in ("anyOf", "oneOf")}
                schema = {**options[0], **merged}
            else:
                return schema
        elif "allOf" in schema and len(schema["allOf"]) == 1:
            schema = {**schema["allOf"][0], **{k: v for k, v in schema.items() if k != "allOf"}}
        else:
            return schema
    return schema


def _body_schema(operation: dict, spec: dict, media: str) -> dict | None:
    content = (operation.get("requestBody") or {}).get("content", {})
    if media not in content:
        return None
    return _deref(content[media].get("schema", {}), spec)


def _declared_tools():
    """Every tool that is one declared HTTP call (the kind-routed campaign tools are checked
    against their classic endpoint; the Ads Manager side is covered by test_p0_2)."""
    return [t for t in TOOLS if t.method.upper() in HTTP and t.path]


def check_tool(tool, spec: dict) -> list[str]:
    problems: list[str] = []
    method, path = tool.method.lower(), tool.path
    where = f"{tool.name} ({tool.method} {path})"
    segment = next((p for p in tool.params if p.where == "path" and p.enum), None)
    if segment is not None:
        # "/conversations/{id}/{mark}" with mark in [mark-interested, ...] stands for one real
        # route per value: every one of them must exist.
        return [
            f"{where}: no route for {segment.name}={value!r}"
            for value in segment.enum
            if spec["paths"].get(path.replace("{" + segment.name + "}", value), {}).get(method) is None
        ]
    operation = spec["paths"].get(path, {}).get(method)
    if operation is None:
        return [f"{where}: no such endpoint in the app"]

    declared = {(p["in"], p["name"]): p for p in operation.get("parameters", [])}
    json_body = _body_schema(operation, spec, "application/json")
    form_body = _body_schema(operation, spec, "multipart/form-data") or _body_schema(
        operation, spec, "application/x-www-form-urlencoded")
    json_props = (json_body or {}).get("properties", {})
    form_props = (form_body or {}).get("properties", {})
    free_form_json = json_body is not None and not json_props and (
        json_body.get("type") in ("object", None) or "additionalProperties" in json_body)
    root_body = any(p.root for p in tool.params)

    sent = {p.name for p in tool.params}
    for param in tool.params:
        if param.where in NOT_SENT or param.root:
            continue
        label = f"{where}: parameter '{param.name}' ({param.where})"
        if param.where == "path":
            if ("path", param.name) not in declared:
                problems.append(f"{label} is not a path parameter of the route")
            continue
        if param.where == "query":
            ep = declared.get(("query", param.name))
            if ep is None:
                problems.append(f"{label} is not declared by the endpoint -- it would be ignored")
                continue
            ep_schema = _deref(ep.get("schema", {}), spec)
        elif param.where == "body":
            if json_body is None:
                problems.append(f"{label}: the endpoint takes no JSON body")
                continue
            if param.name not in json_props:
                if not free_form_json:
                    problems.append(f"{label} is not a field of the endpoint's body model")
                continue
            ep_schema = _deref(json_props[param.name], spec)
            ep = {"schema": ep_schema, "required": param.name in (json_body.get("required") or [])}
        elif param.where in ("form", "file"):
            if form_body is None:
                problems.append(f"{label}: the endpoint takes no form/multipart body")
                continue
            key = "file" if param.where == "file" else param.name
            if key not in form_props:
                problems.append(f"{label} is not a field of the endpoint's form")
            continue
        else:
            problems.append(f"{label}: unknown location")
            continue

        # 3. enums
        if param.enum and ep_schema.get("enum"):
            extra = [v for v in param.enum if v not in ep_schema["enum"]]
            if extra:
                problems.append(f"{label} offers {extra}, which the endpoint rejects ({ep_schema['enum']})")
        # 4. defaults
        if param.default is not None and "default" in ep_schema and param.default != ep_schema["default"]:
            if (tool.name, param.name) not in INTENTIONAL_DEFAULTS:
                problems.append(
                    f"{label} advertises default {param.default!r} but the endpoint's is "
                    f"{ep_schema['default']!r} (list it in INTENTIONAL_DEFAULTS with a reason if that is on purpose)")
        # 5. ceilings
        ceiling = ep_schema.get("maximum")
        if param.maximum is not None and ceiling is not None and param.maximum > ceiling:
            problems.append(f"{label} allows up to {param.maximum} but the endpoint caps at {ceiling}")

    # 2. what the endpoint requires
    if not root_body:
        for (loc, name), ep in declared.items():
            if loc == "query" and ep.get("required") and name not in sent:
                problems.append(f"{where}: the endpoint REQUIRES query parameter '{name}' and the tool has no way to send it")
        for name in (json_body or {}).get("required") or []:
            param = next((p for p in tool.params if p.name == name and p.where == "body"), None)
            if param is None:
                problems.append(f"{where}: the endpoint REQUIRES body field '{name}' and the tool has no way to send it")
            elif not param.required and param.default is None:
                problems.append(f"{where}: body field '{name}' is required by the endpoint but optional in the tool, with no default")
    return problems


@pytest.fixture(scope="module")
def spec():
    return _spec()


# --------------------------------------------------------------------------
# an advertised default is applied, not just advertised
# --------------------------------------------------------------------------


class _Capture:
    """Stands in for the in-process app and records the request the dispatcher builds."""

    def __init__(self, monkeypatch):
        import app.mcp.server as server

        self.calls: list[dict] = []

        async def fake(method, path, **kw):
            self.calls.append({"method": method, "path": path, **kw})
            return 200, {"items": [], "total": 0}

        monkeypatch.setattr(server, "call_app", fake)


@pytest.mark.asyncio
async def test_an_omitted_argument_gets_the_default_the_tool_advertises(monkeypatch):
    from tests.mcp_helpers import call_tool

    capture = _Capture(monkeypatch)

    await call_tool("list_followups")
    await call_tool("inbox_conversations")

    assert capture.calls[0]["query"] == {"view": "pending", "channel": "all", "page": 1, "per_page": 25}
    assert capture.calls[1]["query"]["channel"] == "all", "the inbox tool says 'all' and now means it"


@pytest.mark.asyncio
async def test_what_the_caller_passes_beats_the_default_and_none_means_omitted(monkeypatch):
    from tests.mcp_helpers import call_tool

    capture = _Capture(monkeypatch)

    await call_tool("list_followups", view="overdue", channel=None, per_page=10)

    assert capture.calls[0]["query"] == {"view": "overdue", "channel": "all", "page": 1, "per_page": 10}


@pytest.mark.asyncio
async def test_a_default_is_never_applied_to_an_update(monkeypatch):
    """A default sent on every call would overwrite fields nobody mentioned."""
    from tests.mcp_helpers import call_tool

    capture = _Capture(monkeypatch)

    await call_tool("update_contact", contact_id=5, city="Accra")

    assert capture.calls[0]["body"] == {"city": "Accra"}


def test_campaign_tools_expose_campaign_scoped_pacing_and_unified_email_senders():
    """The controls shown to the assistant must match what each campaign enforces."""
    from app.mcp.registry import TOOLS_BY_NAME

    for tool_name in ("create_campaign", "update_campaign"):
        names = {p.name for p in TOOLS_BY_NAME[tool_name].params}
        assert {"daily_limit", "hourly_limit", "per_minute_limit", "min_delay", "max_delay",
                "send_start_hour", "send_end_hour", "allow_weekends"} <= names
        description = TOOLS_BY_NAME[tool_name].description
        assert "campaign" in description.lower()
        assert "Sending Rules" not in description

    senders = TOOLS_BY_NAME["list_email_accounts"]
    assert senders.path == "/api/v1/email/senders"
    assert "Gmail" in senders.description and "Brevo" in senders.description


@pytest.mark.asyncio
async def test_the_email_inbox_filter_the_tool_offers_really_filters(api_client, api_db):
    from app.models.contact import Contact
    from app.models.conversation import Conversation

    ada = Contact(first_name="Ada", email="ada@leadco.io")
    bayo = Contact(first_name="Bayo", email="bayo@leadco.io")
    api_db.add_all([ada, bayo])
    await api_db.flush()
    unread = Conversation(contact_id=ada.id, channel="email", status="unread", unread_count=2)
    read = Conversation(contact_id=bayo.id, channel="email", status="active", unread_count=0)
    api_db.add_all([unread, read])
    await api_db.flush()

    everything = (await api_client.get("/api/v1/email/inbox/conversations")).json()
    only_unread = (await api_client.get("/api/v1/email/inbox/conversations", params={"unread_only": "true"})).json()

    assert everything["total"] == 2
    assert only_unread["total"] == 1
    assert [item["id"] for item in only_unread["items"]] == [unread.id]


def test_the_check_really_covers_the_registry():
    """A check that quietly skips everything proves nothing: pin how much it looks at."""
    tools = _declared_tools()
    custom = {"how_to_use_this_app", "preview_template", "list_api_endpoints", "app_api_request"}
    assert {t.name for t in TOOLS} - {t.name for t in tools} == custom, "only the hand-written handlers are skipped"
    assert len(tools) >= 58 and sum(len(t.params) for t in tools) >= 200


def test_every_tool_matches_its_endpoint(spec):
    problems: list[str] = []
    for tool in _declared_tools():
        problems.extend(check_tool(tool, spec))
    assert not problems, "\n" + "\n".join(f" - {p}" for p in problems)
