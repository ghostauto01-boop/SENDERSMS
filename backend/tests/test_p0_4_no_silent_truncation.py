"""P0-4 — nothing is cut without saying so, and every tool response is valid JSON.

The QA sweep found that

* ``export_contacts_csv`` returned a 4,000-character prefix: 29 of 1,297 rows, and
  nothing said so (the app's non-JSON fallback kept ``text[:4000]``);
* tool output was ``text[:6000]`` of the JSON, so a long list was cut mid-string
  and no longer parsed -- again with no flag;
* some list endpoints had no ``per_page`` ceiling at all.

The contract now: a response is always a complete JSON document; when a list or a
body had to be shortened the response says ``truncated: true`` and gives
``returned`` / ``total`` / ``next_cursor``; the contact export is cursor-paginated
so 1,297 contacts are 1,297 rows across pages; and a page size above its documented
maximum is a 422 rather than a silent clamp.
"""

from __future__ import annotations

import csv
import io
import json

import pytest
import pytest_asyncio

from app.mcp import response as mcp_response
from app.mcp import server
from app.mcp.registry import TOOLS_BY_NAME
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember


async def _add_contacts(db, n, *, start=0, custom=None, list_id=None):
    contacts = []
    for i in range(start, start + n):
        contact = Contact(
            first_name=f"Person{i}", last_name="Test", business_name=f"Business number {i} Ltd",
            email=f"p{i}@company{i}.io", phone_number=f"+23480312{i:05d}", city="Lagos",
            notes="note " * 12,
            custom_fields=json.dumps(custom(i)) if custom else None,
        )
        contacts.append(contact)
    db.add_all(contacts)
    await db.flush()
    if list_id is not None:
        db.add_all([ContactListMember(list_id=list_id, contact_id=c.id) for c in contacts])
        await db.flush()
    return contacts


@pytest_asyncio.fixture
async def contacts_1297(api_db):
    await _add_contacts(api_db, 1297)
    return 1297


@pytest_asyncio.fixture
async def contacts_5000(api_db):
    await _add_contacts(api_db, 5000)
    return 5000


async def _walk_export(client, *, limit=200, max_bytes=None, **filters):
    """Follow next_cursor to the end; return every page."""
    pages, cursor = [], None
    while True:
        params = {"limit": limit, **filters}
        if max_bytes:
            params["max_bytes"] = max_bytes
        if cursor:
            params["cursor"] = cursor
        response = await client.get("/api/v1/contacts/export", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        pages.append(page)
        cursor = page["next_cursor"]
        if not cursor:
            return pages
        assert len(pages) < 200, "the cursor never ends"


def _data_rows(pages) -> list[list[str]]:
    """Every data row across the pages (the header appears once, on the first)."""
    rows: list[list[str]] = []
    for index, page in enumerate(pages):
        parsed = list(csv.reader(io.StringIO(page["csv"])))
        if index == 0:
            assert parsed[0] == page["columns"], "the first page starts with the header row"
            parsed = parsed[1:]
        else:
            assert parsed[0] != page["columns"], "only the first page carries the header"
        rows.extend(parsed)
    return rows


# --------------------------------------------------------------------------
# the export is cursor-paginated and complete
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_of_1297_contacts_yields_1297_rows_across_pages(api_client, contacts_1297):
    # A large byte budget isolates row paging (the default 20 KB budget has its own test).
    pages = await _walk_export(api_client, limit=200, max_bytes=200_000)

    assert len(pages) == 7
    assert all(p["total"] == 1297 for p in pages)
    assert [p["returned"] for p in pages] == [200] * 6 + [97]
    assert [p["truncated"] for p in pages] == [True] * 6 + [False]
    assert pages[-1]["next_cursor"] is None
    rows = _data_rows(pages)
    assert len(rows) == 1297
    emails = {row[pages[0]["columns"].index("email")] for row in rows}
    assert len(emails) == 1297, "no contact twice, none missing"


@pytest.mark.asyncio
async def test_export_of_5000_contacts_is_complete(api_client, contacts_5000):
    pages = await _walk_export(api_client, limit=500, max_bytes=200_000)
    assert len(pages) == 10
    assert len(_data_rows(pages)) == 5000


@pytest.mark.asyncio
async def test_a_page_never_exceeds_its_byte_budget_and_the_cursor_still_covers_every_row(
    api_client, contacts_1297
):
    """The server trims by cursor (not by cutting text), so no row is ever skipped."""
    pages = await _walk_export(api_client, limit=500, max_bytes=8000)

    assert all(len(p["csv"]) <= 8000 + 600 for p in pages), "one over-budget row at most"
    assert len(pages) > 3, "a small byte budget forces more pages"
    assert len(_data_rows(pages)) == 1297


@pytest.mark.asyncio
async def test_export_columns_are_the_same_on_every_page(api_client, api_db):
    """Custom fields that only appear late must not change the columns mid-export."""
    await _add_contacts(api_db, 30, custom=lambda i: {"pain_point": f"p{i}"} if i < 5 else {})
    await _add_contacts(api_db, 30, start=30, custom=lambda i: {"account_tier": f"t{i}"})

    pages = await _walk_export(api_client, limit=10, max_bytes=200_000)

    assert len(pages) == 6
    assert {tuple(p["columns"]) for p in pages} == {tuple(pages[0]["columns"])}
    assert "pain_point" in pages[0]["columns"] and "account_tier" in pages[0]["columns"]
    last_rows = list(csv.reader(io.StringIO(pages[-1]["csv"])))
    assert all(len(row) == len(pages[0]["columns"]) for row in last_rows)
    tier = pages[0]["columns"].index("account_tier")
    assert last_rows[0][tier].startswith("t"), "a late page still carries its custom field"


@pytest.mark.asyncio
async def test_export_json_format_returns_rows_as_objects(api_client, api_db):
    await _add_contacts(api_db, 12)
    response = await api_client.get("/api/v1/contacts/export", params={"limit": 5, "format": "json"})
    page = response.json()
    assert page["returned"] == 5 and len(page["rows"]) == 5
    assert page["rows"][0]["email"].endswith(".io") and "csv" not in page
    assert page["truncated"] is True and page["next_cursor"]


@pytest.mark.asyncio
async def test_export_cursor_survives_contacts_changing_mid_export(api_client, api_db):
    originals = await _add_contacts(api_db, 50)
    first = (await api_client.get("/api/v1/contacts/export", params={"limit": 20})).json()

    # While the export is in flight: someone is added, someone already exported is removed.
    await _add_contacts(api_db, 3, start=900)
    await api_db.delete(originals[2])
    await api_db.flush()

    pages = [first]
    cursor = first["next_cursor"]
    while cursor:
        page = (await api_client.get(
            "/api/v1/contacts/export", params={"limit": 20, "cursor": cursor})).json()
        pages.append(page)
        cursor = page["next_cursor"]

    rows = _data_rows(pages)
    email_col = pages[0]["columns"].index("email")
    emails = [row[email_col] for row in rows]
    assert len(emails) == len(set(emails)), "a keyset cursor never repeats a row"
    # Everyone who was there to the end (not the one deleted) is present.
    expected = {c.email for c in originals[3:]}
    assert expected <= set(emails)


@pytest.mark.asyncio
async def test_export_respects_filters_and_the_list(api_client, api_db):
    lst = ContactList(name="Cold list")
    api_db.add(lst)
    await api_db.flush()
    await _add_contacts(api_db, 7, list_id=lst.id)
    await _add_contacts(api_db, 20, start=100)

    pages = await _walk_export(api_client, limit=5, list_id=lst.id)

    assert pages[0]["total"] == 7
    assert len(_data_rows(pages)) == 7


@pytest.mark.asyncio
async def test_export_limit_above_the_maximum_is_422_and_documented(api_client):
    too_big = await api_client.get("/api/v1/contacts/export", params={"limit": 501})
    assert too_big.status_code == 422
    assert (await api_client.get("/api/v1/contacts/export", params={"limit": 0})).status_code == 422

    spec = (await api_client.get("/openapi.json")).json()
    params = {p["name"]: p for p in spec["paths"]["/api/v1/contacts/export"]["get"]["parameters"]}
    assert params["limit"]["schema"]["maximum"] == 500
    assert "cursor" in params


@pytest.mark.asyncio
async def test_export_rejects_a_garbage_cursor_and_a_cursor_for_other_filters(
    api_client, api_db
):
    await _add_contacts(api_db, 30)
    assert (await api_client.get(
        "/api/v1/contacts/export", params={"cursor": "not-a-cursor"})).status_code == 422

    page = (await api_client.get(
        "/api/v1/contacts/export", params={"limit": 10, "search": "Person"})).json()
    other = await api_client.get(
        "/api/v1/contacts/export",
        params={"limit": 10, "search": "Business", "cursor": page["next_cursor"]},
    )
    assert other.status_code == 422, "a cursor belongs to the filters that produced it"


@pytest.mark.asyncio
async def test_the_streaming_download_still_returns_every_contact(api_client, contacts_1297):
    """§D: the browser's one-shot CSV download is unchanged."""
    response = await api_client.get("/api/v1/contacts/export/csv")
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text)))
    assert len(rows) - 1 == 1297


# --------------------------------------------------------------------------
# tool responses are always complete JSON documents
# --------------------------------------------------------------------------


class _Stub:
    def add(self, row):
        pass

    async def flush(self):
        return None


async def _call(tool, **args):
    server.CURRENT_ADMIN_ID.set(1)
    outcome = await server.run_tool(
        _Stub(), server.TokenView(id=1, name="t", scope="write", prefix="mcp_t"),
        TOOLS_BY_NAME[tool], args,
    )
    return outcome.result


def _text(result) -> str:
    return result["content"][0]["text"]


@pytest.mark.asyncio
async def test_every_tool_response_is_valid_json_with_5000_contacts(api_client, contacts_5000):
    calls = [
        ("search_contacts", {"per_page": 100}),
        ("search_contacts", {}),
        ("search_contacts", {"search": "Person1"}),
        ("export_contacts_csv", {}),
        ("export_contacts_csv", {"limit": 500}),
        ("list_contact_lists", {}),
        ("dashboard_stats", {}),
        ("list_campaigns", {}),
        ("how_to_use_this_app", {}),
        ("app_api_request", {"method": "GET", "path": "/api/v1/contacts/?per_page=100"}),
        ("app_api_request", {"method": "GET", "path": "/api/v1/contacts/export/csv"}),
        ("app_api_request", {"method": "GET", "path": "/api/v1/does/not/exist"}),
        ("get_contact", {"contact_id": 99999999}),
    ]
    for tool, args in calls:
        if tool not in TOOLS_BY_NAME:
            continue
        result = await _call(tool, **args)
        text = _text(result)
        try:
            document = json.loads(text)
        except ValueError as exc:  # pragma: no cover — the failure message is the point
            raise AssertionError(f"{tool}({args}) did not return valid JSON: {exc}; {text[:120]!r}")
        assert isinstance(document, dict), f"{tool}: the response is a JSON object"
        assert "ok" in document, f"{tool}: every response says whether it worked"


@pytest.mark.asyncio
async def test_a_page_that_had_to_be_shortened_says_so_and_says_how_to_continue(
    api_client, contacts_5000
):
    result = await _call("search_contacts", per_page=100)
    envelope = json.loads(_text(result))

    items = envelope["data"]["items"]
    assert envelope["truncated"] is True
    assert envelope["returned"] == len(items) < 100
    assert envelope["total"] == 5000
    cut = envelope["cut"]
    assert cut["requested"] == 100 and cut["kept"] == envelope["returned"], (
        "the response says it was shortened, from what and to what"
    )
    cursor = envelope["next_cursor"]
    assert cursor and cursor["per_page"] == envelope["returned"], (
        "the continuation uses a page size that fits"
    )
    assert 100 % cursor["per_page"] == 0, "aligned with the original page boundaries: no gap"
    assert envelope["note"]


@pytest.mark.asyncio
async def test_following_the_continuation_never_skips_or_repeats_a_contact(
    api_client, contacts_5000
):
    seen: list[str] = []
    result = await _call("search_contacts", per_page=100)
    envelope = json.loads(_text(result))
    seen += [c["email"] for c in envelope["data"]["items"]]
    cursor = envelope["next_cursor"]
    guard = 0
    while cursor and guard < 400:
        guard += 1
        envelope = json.loads(_text(await _call("search_contacts", **cursor)))
        seen += [c["email"] for c in envelope["data"]["items"]]
        cursor = envelope["next_cursor"]
        if len(seen) >= 600:
            break
    assert len(seen) == len(set(seen)), "no contact repeated"
    # The default ordering is newest-first; what matters is the walk is gap-free:
    ordered = await api_client.get("/api/v1/contacts/", params={"per_page": 100})
    first_100 = [c["email"] for c in ordered.json()["items"]]
    assert seen[:100] == first_100, "the continuation picks up exactly where the cut was"


@pytest.mark.asyncio
async def test_an_ordinary_page_is_paging_not_a_cut(api_client, contacts_5000):
    envelope = json.loads(_text(await _call("search_contacts", per_page=5)))
    assert envelope["cut"] is None, "the tool layer shortened nothing"
    assert envelope["returned"] == len(envelope["data"]["items"]) == 5
    assert envelope["total"] == 5000
    assert envelope["truncated"] is True, "5 of 5000 is not everything"
    assert envelope["has_more"] is True
    assert envelope["next_cursor"] == {"page": 2, "per_page": 5}


@pytest.mark.asyncio
async def test_the_last_page_of_a_listing_is_not_truncated(api_client, contacts_5000):
    """Truncated means more exists *beyond this response*; on the final page nothing does,
    even though returned < total. A client must not be told to keep going."""
    envelope = json.loads(_text(await _call("search_contacts", per_page=10, page=500)))
    assert envelope["returned"] == len(envelope["data"]["items"]) == 10
    assert envelope["total"] == 5000
    assert envelope["truncated"] is False and envelope["has_more"] is False
    assert envelope["next_cursor"] is None and envelope["cut"] is None


@pytest.mark.asyncio
async def test_the_last_export_page_is_not_truncated(api_client, contacts_1297):
    pages = await _walk_export(api_client, limit=200, max_bytes=200_000)
    last = pages[-1]
    assert last["returned"] == 1297 - 6 * 200 and last["total"] == 1297
    assert last["truncated"] is False and last["next_cursor"] is None


@pytest.mark.asyncio
async def test_a_result_that_is_complete_says_so(api_client, contacts_5000):
    envelope = json.loads(_text(await _call("search_contacts", search="Person4999")))
    assert envelope["total"] == envelope["returned"] == 1
    assert envelope["truncated"] is False and envelope["has_more"] is False
    assert envelope["next_cursor"] is None and envelope["cut"] is None


@pytest.mark.asyncio
async def test_nothing_is_shortened_without_the_flag(api_client, contacts_5000):
    """For every page size: either the whole page arrived, or the response says it was cut."""
    for per_page in (1, 5, 25, 50, 80, 100):
        envelope = json.loads(_text(await _call("search_contacts", per_page=per_page)))
        items = envelope["data"]["items"]
        assert envelope["returned"] == len(items)
        if len(items) < per_page:
            assert envelope["cut"] and envelope["cut"]["kept"] == len(items), (
                f"per_page={per_page}: {len(items)} arrived and nothing said they were cut"
            )
        else:
            assert envelope["cut"] is None


@pytest.mark.asyncio
async def test_following_the_export_cursor_through_the_tool_yields_every_row(
    api_client, contacts_1297
):
    pages, cursor = [], None
    for _ in range(100):
        args = {"limit": 500} if cursor is None else {"limit": 500, "cursor": cursor}
        envelope = json.loads(_text(await _call("export_contacts_csv", **args)))
        assert envelope["ok"] is True
        pages.append(envelope["data"])
        assert envelope["cut"] is None, (
            "an export page that the tool layer had to cut would silently drop rows"
        )
        assert envelope["next_cursor"] == envelope["data"]["next_cursor"], (
            "the continuation is surfaced at the top level too"
        )
        cursor = envelope["data"]["next_cursor"]
        if not cursor:
            break
    assert len(_data_rows(pages)) == 1297


@pytest.mark.asyncio
async def test_a_non_json_body_is_reported_not_silently_cut(api_client, contacts_5000):
    result = await _call("app_api_request", method="GET", path="/api/v1/contacts/export/csv")
    envelope = json.loads(_text(result))

    assert envelope["ok"] is True
    assert envelope["truncated"] is True and envelope["cut"]
    data = envelope["data"]
    assert data["chars"] > 500_000, "the app returned the whole export; only the tool response is shortened"
    assert len(data["raw"]) < data["chars"]
    assert data["raw"].endswith("\n"), "cut at a line boundary: no half rows"
    assert envelope["returned"] > 0 and envelope["total"] == 5001
    assert envelope["returned"] == data["raw"].count("\n")
    assert "export" in envelope["note"].lower()


@pytest.mark.asyncio
async def test_the_structured_content_is_bounded_too_and_carries_the_flag(
    api_client, contacts_5000
):
    result = await _call("search_contacts", per_page=100)
    structured = result["structuredContent"]
    assert len(json.dumps(structured)) < mcp_response.DEFAULT_BUDGET
    assert structured["_truncated"]["returned"] == len(structured["items"])
    assert structured["_truncated"]["total"] == 5000
    assert structured["_truncated"]["requested"] == 100


@pytest.mark.asyncio
async def test_the_export_tool_reports_its_counts_at_the_top_level(api_client, api_db):
    """returned / total / truncated / next_cursor are where a client looks: the envelope."""
    await _add_contacts(api_db, 130)
    server.CURRENT_ADMIN_ID.set(1)
    result = await _call("export_contacts_csv", limit=50, max_bytes=200_000)
    envelope = json.loads(result["content"][0]["text"])
    assert envelope["returned"] == 50
    assert envelope["total"] == 130
    assert envelope["truncated"] is True
    assert envelope["next_cursor"]


@pytest.mark.asyncio
async def test_resource_reads_are_complete_json(api_client, api_db, contacts_5000):
    server.CURRENT_ADMIN_ID.set(1)
    for uri in ("sendsms://reference", "sendsms://inbox/recent"):
        text = await server._read_resource(uri)
        json.loads(text)


# --------------------------------------------------------------------------
# the envelope builder on its own
# --------------------------------------------------------------------------


def test_small_payloads_pass_through_untouched():
    envelope, structured = mcp_response.build_envelope(
        "GET", "/api/v1/x", 200, {"items": [1, 2, 3], "total": 3}, query={"page": 1, "per_page": 25}
    )
    assert envelope["truncated"] is False and envelope["cut"] is None
    assert envelope["data"] == {"items": [1, 2, 3], "total": 3}
    assert envelope["returned"] == 3 and envelope["total"] == 3
    assert envelope["next_cursor"] is None
    assert structured == {"items": [1, 2, 3], "total": 3}, "unchanged shape when nothing was cut"


def test_a_big_list_is_cut_by_whole_items_and_stays_valid_json():
    items = [{"id": i, "blob": "x" * 200} for i in range(400)]
    envelope, _ = mcp_response.build_envelope(
        "GET", "/api/v1/x", 200, {"items": items, "total": 400},
        query={"page": 1, "per_page": 400}, budget=5000,
    )
    text = json.dumps(envelope)
    assert len(text) <= 5000
    kept = envelope["data"]["items"]
    assert envelope["truncated"] and envelope["returned"] == len(kept) < 400
    assert envelope["cut"] == {
        "requested": 400, "kept": len(kept), "unit": "items", "reason": envelope["cut"]["reason"],
    }
    assert kept == items[: len(kept)], "a prefix of whole items, never half an item"
    assert envelope["total"] == 400
    assert 400 % envelope["next_cursor"]["per_page"] == 0


def test_a_bare_list_payload_is_handled():
    items = [{"id": i, "blob": "y" * 100} for i in range(300)]
    envelope, structured = mcp_response.build_envelope("GET", "/api/v1/x", 200, items, budget=4000)
    assert envelope["truncated"] and envelope["returned"] == len(envelope["data"])
    assert envelope["total"] == 300
    assert structured["_truncated"]["total"] == 300


def test_a_dict_with_several_lists_shrinks_the_biggest_one():
    payload = {"summary": "ok", "small": [1, 2], "recent": [{"n": "z" * 300} for _ in range(100)]}
    envelope, _ = mcp_response.build_envelope("GET", "/api/v1/x", 200, payload, budget=4000)
    assert envelope["truncated"]
    assert envelope["data"]["small"] == [1, 2] and envelope["data"]["summary"] == "ok"
    assert len(envelope["data"]["recent"]) < 100


def test_an_unshrinkable_payload_gets_a_preview_not_a_cut():
    envelope, _ = mcp_response.build_envelope(
        "GET", "/api/v1/x", 200, {"blob": "z" * 50_000}, budget=3000)
    text = json.dumps(envelope)
    assert len(text) <= 3000
    assert envelope["truncated"] is True
    assert envelope["data"]["preview"] and envelope["data"]["chars"] > 50_000


def test_an_error_response_is_json_too():
    envelope, _ = mcp_response.build_envelope(
        "POST", "/api/v1/x", 409, {"detail": "nope", "code": "CONFLICT"})
    assert envelope["ok"] is False and envelope["http_status"] == 409
    assert envelope["error"] == "nope"


# --------------------------------------------------------------------------
# page sizes have documented ceilings
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,param,maximum",
    [
        ("/api/v1/contacts/", "per_page", 100),
        ("/api/v1/inbox/conversations", "per_page", 500),
        ("/api/v1/webhooks/logs", "per_page", 200),
    ],
)
async def test_page_size_above_the_documented_maximum_is_422(api_client, path, param, maximum):
    over = await api_client.get(path, params={param: maximum + 1})
    assert over.status_code == 422, f"{path}?{param}={maximum + 1} -> {over.status_code}"
    ok = await api_client.get(path, params={param: maximum})
    assert ok.status_code == 200, f"{path}?{param}={maximum} -> {ok.status_code} {ok.text[:100]}"
    spec = (await api_client.get("/openapi.json")).json()
    declared = {p["name"]: p for p in spec["paths"][path]["get"]["parameters"]}
    assert declared[param]["schema"]["maximum"] == maximum, "the ceiling is in the OpenAPI"


@pytest.mark.asyncio
async def test_contact_list_reports_its_own_paging(api_client, contacts_5000):
    body = (await api_client.get("/api/v1/contacts/", params={"per_page": 50})).json()
    assert body["total"] == 5000 and body["page"] == 1 and body["per_page"] == 50
    assert body["next_page"] == 2
    last = (await api_client.get("/api/v1/contacts/", params={"per_page": 100, "page": 50})).json()
    assert last["next_page"] is None


def test_the_tool_schemas_document_the_same_ceilings_as_the_endpoints():
    """Drift guard: a tool must not advertise a bigger page than its endpoint allows."""
    from app.main import app

    spec = app.openapi()
    checked = 0
    for tool in TOOLS_BY_NAME.values():
        if tool.method != "GET":
            continue
        operations = spec["paths"].get(tool.path)
        if not operations:
            continue
        declared = {p["name"]: p for p in operations["get"].get("parameters", [])}
        for param in tool.params:
            if param.name not in ("per_page", "limit") or param.where != "query":
                continue
            schema = (declared.get(param.name) or {}).get("schema", {})
            real = schema.get("maximum")
            for option in schema.get("anyOf", []):  # Optional[int] is anyOf [int, null]
                real = real if real is not None else option.get("maximum")
            if real is None:
                continue
            assert param.maximum == real, (
                f"{tool.name}.{param.name}: tool says {param.maximum}, endpoint says {real}"
            )
            checked += 1
    assert checked >= 3
