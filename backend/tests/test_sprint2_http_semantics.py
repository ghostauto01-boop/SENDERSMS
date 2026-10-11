"""Sprint 2 — C11: the HTTP verbs and status codes a client (or an assistant using the generic
`app_api_request` tool) has every right to expect.

* ``DELETE`` of a campaign that does not exist is a 404 (it was a 400; fixed with P0-1, pinned here).
* ``PATCH /campaigns/{id}`` was a 405: a REST client's natural way to change one field failed, while
  ``PUT`` -- which already treats the body as a partial update -- worked.
* An invalid ``mark`` (``/conversations/{id}/mark-bogus``) was a 405 "Method Not Allowed" -- the
  wrong problem entirely -- and the assistant tool forwarded the bad value instead of refusing it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests.campaign_factory import make_campaign
from tests.mcp_helpers import call_tool

MARKS = ["mark-interested", "mark-not-interested", "mark-close", "mark-unread"]


@pytest.mark.asyncio
async def test_deleting_a_campaign_that_does_not_exist_is_a_404(api_client):
    response = await api_client.delete("/api/v1/campaigns/9999")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_changes_only_the_fields_it_is_given(api_client, api_db):
    campaign = await make_campaign(api_db, status="draft", description="keep me")

    response = await api_client.patch(f"/api/v1/campaigns/{campaign.id}", json={"name": "Renamed"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["description"] == "keep me", "a field that was not sent is not touched"
    assert body["status"] == "draft"


@pytest.mark.asyncio
async def test_patch_and_put_behave_the_same(api_client, api_db):
    campaign = await make_campaign(api_db, status="draft")

    via_put = await api_client.put(f"/api/v1/campaigns/{campaign.id}", json={"name": "A"})
    via_patch = await api_client.patch(f"/api/v1/campaigns/{campaign.id}", json={"name": "B"})

    assert via_put.status_code == via_patch.status_code == 200
    assert via_patch.json()["name"] == "B"


@pytest.mark.asyncio
async def test_patching_a_missing_campaign_is_a_404(api_client):
    assert (await api_client.patch("/api/v1/campaigns/9999", json={"name": "x"})).status_code == 404


@pytest.mark.asyncio
async def test_patch_is_refused_for_a_campaign_that_is_sending_exactly_like_put(api_client, api_db):
    campaign = await make_campaign(api_db, status="running")

    via_put = await api_client.put(f"/api/v1/campaigns/{campaign.id}", json={"name": "A"})
    via_patch = await api_client.patch(f"/api/v1/campaigns/{campaign.id}", json={"name": "A"})

    assert via_patch.status_code == via_put.status_code
    assert via_patch.status_code in (400, 409), via_patch.text


@pytest_asyncio.fixture
async def conversation(api_db):
    from app.models.contact import Contact
    from app.models.conversation import Conversation

    contact = Contact(first_name="Ada", phone_number="+2348031200001")
    api_db.add(contact)
    await api_db.flush()
    conv = Conversation(contact_id=contact.id, channel="sms", status="active")
    api_db.add(conv)
    await api_db.flush()
    return conv


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["mark-bogus", "bogus", "mark-", "reply-all"])
async def test_an_invalid_mark_is_a_422_that_lists_the_valid_ones(api_client, conversation, bad):
    response = await api_client.post(f"/api/v1/inbox/conversations/{conversation.id}/{bad}")

    assert response.status_code == 422, response.text
    message = response.json()["message"]
    assert bad in message
    assert all(mark in message for mark in MARKS)


@pytest.mark.asyncio
@pytest.mark.parametrize("mark", MARKS)
async def test_the_real_marks_still_work(api_client, conversation, mark):
    assert (await api_client.post(f"/api/v1/inbox/conversations/{conversation.id}/{mark}")).status_code == 200


@pytest.mark.asyncio
async def test_a_valid_mark_on_a_missing_conversation_is_still_a_404(api_client):
    assert (await api_client.post("/api/v1/inbox/conversations/9999/mark-interested")).status_code == 404


@pytest.mark.asyncio
async def test_the_tool_refuses_an_invalid_mark_without_calling_the_app(monkeypatch):
    import app.mcp.server as server

    calls = []

    async def fake(method, path, **kw):
        calls.append(path)
        return 200, {}

    monkeypatch.setattr(server, "call_app", fake)

    out = await call_tool("mark_conversation", conversation_id=1, mark="mark-bogus")

    assert out["ok"] is False
    assert "mark-bogus" in out["message"] and "mark-close" in out["message"]
    assert calls == [], "a value the schema forbids never reaches the endpoint"


@pytest.mark.asyncio
async def test_an_enum_value_in_another_case_is_accepted_in_its_canonical_form(monkeypatch):
    import app.mcp.server as server

    calls = []

    async def fake(method, path, **kw):
        calls.append(path)
        return 200, {"success": True}

    monkeypatch.setattr(server, "call_app", fake)

    out = await call_tool("mark_conversation", conversation_id=1, mark="MARK-CLOSE")

    assert out["ok"] is True
    assert calls == ["/api/v1/inbox/conversations/1/mark-close"]
