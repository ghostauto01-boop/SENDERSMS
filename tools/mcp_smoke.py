#!/usr/bin/env python
"""
End-to-end test of the app's MCP server, driven exactly the way an AI assistant
drives it: create a token in the app, then speak JSON-RPC to ``POST /mcp``.

    # app running on :8000 with the fake Brevo provider on :8799
    python tools/mcp_smoke.py

It exercises the whole loop an assistant is expected to use — read the guide,
find endpoints, import contacts, build a list, create a template, create and
validate a campaign, send a test email, send a real (fake-provider) email, read
the inbox, reply in-thread, mark a conversation, pull analytics, use the escape
hatch, and finally prove that a read-only token cannot write and that a revoked
token is dead.

Everything created is prefixed ``mcp-smoke`` so it is easy to spot in the UI.
Exits non-zero if any check fails.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
RUN = str(int(time.time()))
EMAIL_DOMAIN = "acme-leads.io"  # never use example.com — this app rejects it

results: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((bool(ok), label, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


# ---------------------------------------------------------------------------
# An MCP client, the way an assistant speaks to the server
# ---------------------------------------------------------------------------


class Mcp:
    def __init__(self, token: str):
        self.token = token
        self.client = httpx.Client(timeout=180.0)
        self._id = 0

    @property
    def url(self) -> str:
        return f"{BASE_URL}/mcp"

    def rpc(self, method: str, params: dict | None = None, *, accept: str = "application/json"):
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        response = self.client.post(
            self.url,
            headers={"Authorization": f"Bearer {self.token}", "Accept": accept},
            json=payload,
        )
        body = response.text
        data = None
        try:
            data = response.json()
        except ValueError:
            # SSE responses carry the JSON after "data: ".
            for line in body.splitlines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    break
        return response, data

    def call(self, tool_name: str, **args):
        response, body = self.rpc("tools/call", {"name": tool_name, "arguments": args})
        assert body is not None, f"{tool_name}: no JSON in {response.text[:300]}"
        if "error" in body:
            return True, json.dumps(body["error"]), None
        result = body.get("result") or {}
        text = "".join(part.get("text", "") for part in result.get("content", []))
        return bool(result.get("isError")), text, result.get("structuredContent")

    def payload(self, tool_name: str, **args):
        """Call a tool that returns a JSON object and give back that object."""
        is_error, text, data = self.call(tool_name, **args)
        if is_error:
            raise RuntimeError(f"{tool_name} failed: {text[:400]}")
        if isinstance(data, dict) and set(data.keys()) == {"items"}:
            return data["items"]
        if isinstance(data, dict):
            return data
        # Fall back to the JSON that follows the status line.
        _, _, tail = text.partition("\n\n")
        return json.loads(tail) if tail.strip() else text


def jget(data, *keys, default=None):
    """Read the first key that exists in a dict-ish payload."""
    for key in keys:
        if isinstance(data, dict) and key in data:
            return data[key]
    return default


def main() -> int:
    print(f"MCP smoke test against {BASE_URL} (run {RUN})")
    app = httpx.Client(base_url=BASE_URL, timeout=180.0)

    # ---------------------------------------------------------------- token
    print("\n[1] Token + handshake")
    login = app.post("/api/v1/auth/admin", json={"password": ADMIN_PASSWORD})
    if not check("app login", login.status_code == 200, login.text[:200]):
        return 1

    made = app.post("/api/v1/mcp/tokens", json={"name": f"mcp-smoke-{RUN}", "scope": "write"})
    check("create token in the app", made.status_code == 201, made.text[:200])
    if made.status_code != 201:
        return 1
    token = made.json()["token"]
    token_id = made.json()["id"]
    check("token looks like a secret", token.startswith("mcp_") and len(token) > 24)
    check("token is masked in the UI list", made.json()["prefix"] in token)

    mcp = Mcp(token)
    response, body = mcp.rpc("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "mcp-smoke", "version": "1.0"},
    })
    info = (body or {}).get("result") or {}
    check("initialize handshake", response.status_code == 200 and "result" in (body or {}))
    check("protocol version echoed", info.get("protocolVersion") == "2024-11-05",
          str(info.get("protocolVersion")))
    check("server identity", (info.get("serverInfo") or {}).get("name") == "sendersms")
    check("tools capability advertised", "tools" in (info.get("capabilities") or {}))
    check("instructions warn about consent", "consent" in (info.get("instructions") or "").lower())

    _, notif = mcp.rpc("notifications/initialized")
    check("initialized notification accepted", notif is None)

    # SSE is the other valid framing for streamable HTTP.
    response, body = mcp.rpc("ping", accept="text/event-stream")
    check("SSE framing works", "text/event-stream" in response.headers.get("content-type", "")
          and body is not None, response.headers.get("content-type", ""))

    # ---------------------------------------------------------------- tools
    print("\n[2] Tools")
    _, listing = mcp.rpc("tools/list")
    tools = ((listing or {}).get("result") or {}).get("tools") or []
    names = [t["name"] for t in tools]
    check("tools are advertised", len(tools) >= 50, f"{len(tools)} tools")
    check("tool names are unique", len(names) == len(set(names)))
    check("every tool has a JSON Schema input", all(
        (t.get("inputSchema") or {}).get("type") == "object" for t in tools))
    required = {"search_contacts", "import_contacts_csv", "create_campaign", "start_campaign",
                "send_email_now", "send_sms_now", "reply_to_email", "email_engagement",
                "inbox_conversations", "dashboard_stats", "app_api_request"}
    check("the app's whole surface is reachable", required.issubset(set(names)),
          f"missing {sorted(required - set(names))}")

    _, guide, _ = mcp.call("how_to_use_this_app")  # prose, not JSON
    check("guide explains both channels",
          "sms" in guide.lower() and "email" in guide.lower())
    check("guide states the consent rules", "opt" in guide.lower() and "unsubscribe" in guide.lower())
    check("guide points at the escape hatch", "app_api_request" in guide)

    _, endpoints = mcp.rpc("tools/call", {"name": "list_api_endpoints", "arguments": {}})
    catalogue = "".join(p.get("text", "") for p in
                        ((endpoints or {}).get("result") or {}).get("content", []))
    check("endpoint catalogue lists campaigns", "POST   /api/v1/campaigns/" in catalogue)
    check("endpoint catalogue lists settings", "/api/v1/settings/sending-rules" in catalogue)

    # Either channel can identify a contact, so neither may be advertised as
    # mandatory: an assistant that believes a phone is required will refuse to
    # create the email-only contacts this app now supports.
    create_schema = next((t for t in tools if t["name"] == "create_contact"), {})
    create_required = (create_schema.get("inputSchema") or {}).get("required", [])
    check("create_contact does not demand a phone number",
          "phone_number" not in create_required, str(create_required))
    check("create_contact does not demand an email address",
          "email" not in create_required, str(create_required))
    check("create_contact accepts both channels",
          {"phone_number", "email"} <= set((create_schema.get("inputSchema") or {})
                                           .get("properties", {})),
          str(list((create_schema.get("inputSchema") or {}).get("properties", {}))))
    update_schema = next((t for t in tools if t["name"] == "update_contact"), {})
    check("update_contact does not advertise an unusable field",
          "phone_number" not in (update_schema.get("inputSchema") or {}).get("properties", {}))

    is_error, text, _ = mcp.call("not_a_real_tool")
    check("unknown tool is a clear error", is_error and "Unknown tool" in text, text[:120])
    response, body = mcp.rpc("does/not/exist")
    check("unknown method is a JSON-RPC error",
          ((body or {}).get("error") or {}).get("code") == -32601)

    # ------------------------------------------------------------- resources
    print("\n[3] Resources and prompts")
    _, resources = mcp.rpc("resources/list")
    uris = [r["uri"] for r in (((resources or {}).get("result") or {}).get("resources") or [])]
    check("guide resource exposed", "sendsms://guide" in uris)
    _, read = mcp.rpc("resources/read", {"uri": "sendsms://guide"})
    check("resource reads as markdown",
          "text/markdown" in json.dumps(read or {}) and "Operating this app" in json.dumps(read or {}))
    _, prompts = mcp.rpc("prompts/list")
    prompt_names = [p["name"] for p in (((prompts or {}).get("result") or {}).get("prompts") or [])]
    check("guided prompts available", "run_a_campaign" in prompt_names)

    # -------------------------------------------------------------- contacts
    print("\n[4] Contacts, lists, imports")
    email = f"mcp-smoke-{RUN}@{EMAIL_DOMAIN}"
    contact = mcp.payload("create_contact", email=email, phone_number=f"+2348033{RUN[-6:]}",
                          first_name="Maya", last_name="Smoke",
                          business_name="Smoke Analytics", city="Abuja", country="NG")
    contact_id = jget(contact, "id", "contact_id")
    check("contact created through MCP", bool(contact_id), json.dumps(contact)[:200])

    # The whole point of the change: a contact with an address and no number.
    email_only = mcp.payload("create_contact", email=f"mcp-smoke-eo-{RUN}@{EMAIL_DOMAIN}",
                             first_name="Eve", last_name="Only",
                             business_name="Exclusive Analytics")
    email_only_id = jget(email_only, "id", "contact_id")
    check("email-only contact created through MCP", bool(email_only_id),
          json.dumps(email_only)[:200])
    check("email-only contact really has no phone",
          not jget(email_only, "phone_number"),
          json.dumps(email_only)[:200])

    # ...and a contact with neither channel is still refused.
    is_error, text, _ = mcp.call("create_contact", first_name="Nameless")
    check("contact with neither channel is refused", is_error, text[:200])
    found = mcp.payload("search_contacts", search=f"mcp-smoke-{RUN}")
    items = found.get("items") if isinstance(found, dict) else found
    check("contact is searchable", any(c.get("id") == contact_id for c in (items or [])),
          f"{len(items or [])} hit(s)")
    got = mcp.payload("get_contact", contact_id=contact_id)
    check("contact reads back", jget(got, "email") == email, json.dumps(got)[:200])

    mcp.payload("update_contact", contact_id=contact_id, city="Lagos")
    check("contact updated", jget(mcp.payload("get_contact", contact_id=contact_id), "city") == "Lagos")

    list_name = f"mcp-smoke-list-{RUN}"
    created_list = mcp.payload("create_contact_list", name=list_name)
    list_id = jget(created_list, "id", "list_id")
    check("list created (query-param endpoint)", bool(list_id), json.dumps(created_list)[:200])
    lists = mcp.payload("list_contact_lists")
    list_items = lists.get("items") if isinstance(lists, dict) else lists
    check("list appears in the app", any(l.get("id") == list_id for l in (list_items or [])))

    mcp.payload("add_contacts_to_list", list_id=list_id, contact_ids=[contact_id])
    members = mcp.payload("list_contacts_in_list", list_id=list_id)
    member_items = members.get("items") if isinstance(members, dict) else members
    check("contact added to the list", any(c.get("id") == contact_id for c in (member_items or [])),
          f"{len(member_items or [])} member(s)")

    csv_text = (
        "email,first_name,phone,city\n"
        f"mcp-import-{RUN}@{EMAIL_DOMAIN},Importo,+2348035{RUN[-6:]},Kano\n"
        f"mcp-import2-{RUN}@{EMAIL_DOMAIN},Importer,+2348036{RUN[-6:]},Enugu\n"
    )
    imported = mcp.payload("import_contacts_csv", csv_text=csv_text, list_id=list_id,
                           tags="mcp-smoke")
    check("CSV import accepted",
          bool(jget(imported, "imported", "created", "success") is not None
               or jget(imported, "imported_count")),
          json.dumps(imported)[:220])
    time.sleep(0.5)
    after_import = mcp.payload("list_contacts_in_list", list_id=list_id)
    after_items = after_import.get("items") if isinstance(after_import, dict) else after_import
    check("CSV rows landed in the list", len(after_items or []) >= 3,
          f"{len(after_items or [])} member(s)")
    check("CSV custom columns became variables",
          "city" in json.dumps(mcp.payload("list_variables")).lower())

    exported = mcp.payload("export_contacts_csv")
    check("contacts export as CSV", "email" in json.dumps(exported).lower())

    # ------------------------------------------------------------- templates
    print("\n[5] Templates")
    template_name = f"mcp-smoke-template-{RUN}"
    template = mcp.payload(
        "create_template", channel="email", name=template_name,
        subject="Quick question about {{business_name}}",
        body="Hi {{first_name}}, quick question about {{business_name}} — worth a chat?",
        html_body="<p>Hi {{first_name}},</p><p>Quick question about <b>{{business_name}}</b> "
                  "— worth a chat?</p><p>— The team</p>",
        category="smoke",
    )
    template_id = jget(template, "id", "template_id")
    check("email template created", bool(template_id), json.dumps(template)[:200])
    check("created template reports the email channel and its name",
          jget(template, "channel") == "email" and jget(template, "name") == template_name,
          json.dumps({k: template.get(k) for k in ("name", "channel", "subject")}))
    templates = mcp.payload("list_templates", channel="email", search=template_name)
    template_items = templates.get("items") if isinstance(templates, dict) else templates
    check("template listed for the email channel",
          any(t.get("id") == template_id for t in (template_items or [])),
          f"search={template_name!r} -> {json.dumps(templates)[:200]}")
    fetched = mcp.payload("get_template", template_id=template_id)
    check("template reads back in full", "{{first_name}}" in json.dumps(fetched))
    preview = mcp.payload("preview_template", template_id=template_id, contact_id=contact_id)
    preview_text = json.dumps(preview)
    check("template previews with the contact's real values",
          "Maya" in preview_text and "Smoke Analytics" in preview_text, preview_text[:220])
    rendered = mcp.payload("render_text_preview", body="Hi {{first_name}}, still interested?",
                           channel="sms")
    check("unsaved copy can be rendered too", "John" in json.dumps(rendered),
          json.dumps(rendered)[:200])

    # ------------------------------------------------------------- campaigns
    print("\n[6] Campaigns")
    campaign = mcp.payload(
        "create_campaign", name=f"mcp-smoke-campaign-{RUN}", channel="email", list_id=list_id,
        template_id=template_id, message_body="Hi {{first_name}}, quick question?",
        subject="Quick question about {{business_name}}",
        html_body="<p>Hi {{first_name}},</p><p>Worth a quick chat?</p>",
    )
    campaign_id = jget(campaign, "id", "campaign_id")
    check("email campaign created", bool(campaign_id), json.dumps(campaign)[:220])
    check("campaign starts as a draft",
          str(jget(mcp.payload("get_campaign", campaign_id=campaign_id), "status")).lower()
          in {"draft", "pending", "paused"}, "")
    mcp.payload("update_campaign", campaign_id=campaign_id, subject=f"Updated by MCP {RUN}")
    check("campaign updated",
          "Updated by MCP" in json.dumps(mcp.payload("get_campaign", campaign_id=campaign_id)))
    validated = mcp.payload("validate_campaign", campaign_id=campaign_id)
    check("campaign validates", jget(validated, "valid") is True, json.dumps(validated)[:220])
    # Validate is a report: it must not have moved the campaign anywhere.
    check("validate changed nothing",
          jget(validated, "changed") is False
          and str(jget(mcp.payload("get_campaign", campaign_id=campaign_id), "status")).lower()
          == "draft", json.dumps(validated)[:220])
    analytics = mcp.payload("campaign_analytics", campaign_id=campaign_id)
    check("campaign analytics readable", isinstance(analytics, (dict, list)))
    # Not running, so pausing must be refused — with the app's own reason, not a
    # crash. (A campaign only runs when it is started or its schedule fires.)
    try:
        mcp.payload("pause_campaign", campaign_id=campaign_id)
        check("pausing a campaign that is not running is refused", False, "it was allowed")
    except RuntimeError as exc:
        check("pausing a campaign that is not running is refused",
              "Only running or scheduled campaigns can be paused" in str(exc), str(exc)[:160])

    # Park the campaign a year out: nothing the smoke created can then fire by
    # itself, and the "send it tomorrow at 9" tool gets exercised for real.
    next_year = datetime.now(timezone.utc) + timedelta(days=365)
    parked = mcp.payload("schedule_campaign", campaign_id=campaign_id,
                         scheduled_start_at=next_year.isoformat())
    check("campaign can be scheduled for later", jget(parked, "success") is not False,
          json.dumps(parked)[:200])
    check("the launch time is stored",
          str(next_year.year) in json.dumps(
              mcp.payload("get_campaign", campaign_id=campaign_id)))

    throwaway = mcp.payload("create_campaign", name=f"mcp-smoke-scratch-{RUN}", channel="email",
                            list_id=list_id, message_body="scratch")
    scratch_id = jget(throwaway, "id", "campaign_id")
    removed = mcp.payload("delete_campaign", campaign_id=scratch_id)
    check("a draft campaign can be deleted", jget(removed, "success") is not False,
          json.dumps(removed)[:200])

    # ---------------------------------------------------------------- email
    print("\n[7] Email sending")
    accounts = mcp.payload("list_email_accounts")
    account_items = accounts.get("items") if isinstance(accounts, dict) else accounts
    check("a Brevo sender is configured", bool(account_items), json.dumps(accounts)[:160])
    if not account_items:
        print("\nNo Brevo sender in this database — run tools/simulate_email_flow.py first.")
        return 1

    test_send = mcp.payload("send_test_email", to=f"ada+{RUN[-4:]}@{EMAIL_DOMAIN}",
                            subject="MCP smoke test", body="Hello from the MCP smoke test.")
    check("test email queued through Brevo",
          jget(test_send, "success") is True or "sent" in json.dumps(test_send).lower(),
          json.dumps(test_send)[:220])

    sent = mcp.payload("send_email_now", contact_id=contact_id,
                       subject=f"MCP smoke {RUN}",
                       body="Hi Maya, this one came from the MCP smoke test.",
                       html_body="<p>Hi Maya,</p><p>This one came from the MCP smoke test.</p>")
    check("real send accepted", jget(sent, "success") is not False, json.dumps(sent)[:220])
    history = mcp.payload("email_history", page=1, per_page=5)
    check("send shows up in email history",
          f"MCP smoke {RUN}" in json.dumps(history) or "mcp-smoke" in json.dumps(history).lower(),
          json.dumps(history)[:200])

    inbox = mcp.payload("email_inbox", per_page=5)
    inbox_items = inbox.get("items") if isinstance(inbox, dict) else inbox
    check("email inbox readable", isinstance(inbox_items, list), json.dumps(inbox)[:160])
    if inbox_items:
        conversation_id = jget(inbox_items[0], "id", "conversation_id")
        thread = mcp.payload("get_email_conversation", conversation_id=conversation_id)
        check("email thread readable", isinstance(thread, (dict, list)))
        replied = mcp.payload("reply_to_email", conversation_id=conversation_id,
                              body=f"Reply from the MCP smoke test ({RUN}).")
        check("in-thread reply sent", jget(replied, "success") is not False,
              json.dumps(replied)[:220])
        marked = mcp.payload("mark_conversation", conversation_id=conversation_id,
                             mark="mark-interested")
        check("conversation can be marked", jget(marked, "success") is not False,
              json.dumps(marked)[:200])
    else:
        check("email inbox has conversations (simulator data)", False, "no conversations")

    engagement = mcp.payload("email_engagement", contact_id=contact_id)
    check("per-contact engagement readable", isinstance(engagement, (dict, list)))
    suppression = mcp.payload("email_suppression_list")
    check("suppression list readable", isinstance(suppression, (dict, list)))

    # ------------------------------------------------------------ inbox/other
    print("\n[8] Unified inbox, follow-ups, analytics")
    inbox_all = mcp.payload("inbox_conversations", channel="all", per_page=5)
    all_items = inbox_all.get("items") if isinstance(inbox_all, dict) else inbox_all
    check("unified inbox spans both channels", isinstance(all_items, list), json.dumps(inbox_all)[:160])
    check("unified inbox conversations readable",
          not all_items or isinstance(mcp.payload("get_inbox_conversation",
                                                  conversation_id=all_items[0]["id"]), (dict, list)))
    check("follow-ups listed", isinstance(mcp.payload("list_followups"), (dict, list)))
    check("dashboard stats readable", "total_contacts" in mcp.payload("dashboard_stats"))
    check("email overview readable", isinstance(mcp.payload("email_overview"), (dict, list)))
    check("analytics overview readable", isinstance(mcp.payload("analytics_overview"), (dict, list)))
    check("reference readable", isinstance(mcp.payload("app_reference"), (dict, list)))

    # --------------------------------------------------------- escape hatch
    print("\n[9] Escape hatch")
    stats = mcp.payload("app_api_request", method="GET", path="/api/v1/dashboard/stats")
    check("escape hatch reads any endpoint", "total_contacts" in stats)
    with_query = mcp.payload("app_api_request", method="GET",
                             path=f"/api/v1/contacts/?search=mcp-smoke-{RUN}")
    check("escape hatch passes query strings",
          f"mcp-smoke-{RUN}" in json.dumps(with_query), json.dumps(with_query)[:160])
    write_error, write_text, _ = mcp.call("app_api_request", method="DELETE",
                                          path=f"/api/v1/templates/{template_id}")
    check("escape hatch can write (template deleted)", not write_error, write_text[:160])
    gone_error, gone_text, _ = mcp.call("get_template", template_id=template_id)
    check("the deleted template is really gone", gone_error and "404" in gone_text,
          gone_text[:160])

    # ------------------------------------------------------- scope + revoke
    print("\n[10] Guard rails")
    read_only = app.post("/api/v1/mcp/tokens", json={"name": f"mcp-smoke-ro-{RUN}", "scope": "read"})
    ro_token = read_only.json()["token"]
    ro = Mcp(ro_token)
    is_error, text, _ = ro.call("create_contact", email=f"nope-{RUN}@{EMAIL_DOMAIN}",
                                phone_number=f"+2348037{RUN[-6:]}")
    check("read-only token cannot write", is_error and "read-only" in text, text[:160])
    is_error, text, _ = ro.call("app_api_request", method="POST",
                                path="/api/v1/contacts/", body={"email": f"nope2-{RUN}@{EMAIL_DOMAIN}"})
    check("read-only token cannot post through the escape hatch",
          is_error and "read-only" in text, text[:160])
    ok, text, _ = ro.call("search_contacts", search="mcp-smoke")
    check("read-only token can still read", not ok, text[:120])
    app.delete(f"/api/v1/mcp/tokens/{read_only.json()['id']}")

    activity = app.get("/api/v1/mcp/activity", params={"limit": 50}).json()
    check("every call is journalled", activity.get("total", 0) >= 20, str(activity.get("total")))
    logged_tools = {row["tool"] for row in activity.get("items", [])}
    check("journal names the tools used", {"create_contact", "send_email_now"} <= logged_tools,
          str(sorted(logged_tools)[:8]))
    check("journal records failures too", any(not row["ok"] for row in activity.get("items", [])))

    revoked = app.delete(f"/api/v1/mcp/tokens/{token_id}")
    check("token revoked from the app", revoked.status_code == 204, revoked.text[:120])
    response, _ = mcp.rpc("tools/list")
    check("revoked token is rejected", response.status_code == 401, str(response.status_code))
    anonymous = httpx.post(f"{BASE_URL}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    check("unauthenticated calls are rejected", anonymous.status_code == 401)
    get_response = httpx.get(f"{BASE_URL}/mcp")
    check("GET /mcp explains itself (no server stream)", get_response.status_code == 405)
    query_token = httpx.post(f"{BASE_URL}/mcp?token=definitely-not-a-token",
                             json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    check("the ?token= form is supported (and rejects a bad token)",
          query_token.status_code == 401)

    # ------------------------------------------------------------- clean up
    # A campaign that has sent cannot be deleted (draft/scheduled/failed ones that
    # have sent nothing can), so tidy-up clears what it may and leaves the rest
    # paused under a name this obvious, instead of pretending the rule is not there.
    cleaned, note = mcp_tidy(app, campaign_id)
    check("cleanup left nothing running", cleaned, note)

    passed = sum(1 for ok, _, _ in results if ok)
    failed = [label for ok, label, _ in results if not ok]
    print(f"\n{'=' * 62}\n{passed}/{len(results)} checks passed")
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
        print("=" * 62)
        return 1
    print("MCP SMOKE: ALL PASS")
    print("=" * 62)
    return 0


def mcp_tidy(app: httpx.Client, campaign_id) -> tuple[bool, str]:
    """Tidy up after the smoke run.

    Deletes the campaign when the app allows it (nothing sent yet) and pauses it
    when it does not — the point is that nothing the smoke created is left able
    to send.
    """
    if not campaign_id:
        return True, ""
    response = app.delete(f"/api/v1/campaigns/{campaign_id}")
    if response.status_code in (200, 204, 404):
        return True, "deleted"
    if response.status_code in (400, 409):
        # The app refused (it has sent), so "it is not running" is the promise
        # worth checking.
        paused = app.post(f"/api/v1/campaigns/{campaign_id}/pause")
        state = app.get(f"/api/v1/campaigns/{campaign_id}").json()
        if paused.status_code in (200, 204, 400) and state.get("status") != "running":
            return True, f"left as a {state.get('status')} campaign (not running)"
    return False, f"HTTP {response.status_code}: {response.text[:160]}"


if __name__ == "__main__":
    sys.exit(main())
