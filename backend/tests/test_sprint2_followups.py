"""Sprint 2 — C4: the follow-up list was always empty, and a follow-up could not be removed.

The ``list_followups`` tool had no parameters, so the endpoint's own defaults applied:
``view=due_today`` (pending AND scheduled for today) and ``channel=sms``. A follow-up scheduled
for next week, or any email follow-up -- which is what an assistant creates -- never showed up.
There was no DELETE at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.followup import FollowUp
from tests.mcp_helpers import call_tool


def _when(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


@pytest_asyncio.fixture
async def ada(api_db):
    contact = Contact(first_name="Ada", phone_number="+2348031200001", email="ada@leadco.io",
                      email_verified=True)
    api_db.add(contact)
    await api_db.flush()
    return contact


async def _create(client, contact, *, channel="sms", days=7, **extra):
    body = {"contact_id": contact.id, "scheduled_at": _when(days=days), "message_text": "Checking in",
            "channel": channel, **extra}
    if channel == "email":
        body.setdefault("subject", "Following up")
    response = await client.post("/api/v1/followups/", json=body)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_the_list_tool_shows_a_follow_up_scheduled_for_next_week(api_client, ada):
    created = await _create(api_client, ada, days=7)

    out = await call_tool("list_followups")

    assert out["ok"] is True, out
    assert [item["id"] for item in out["data"]["items"]] == [created["id"]]
    assert out["total"] == 1 and out["truncated"] is False


@pytest.mark.asyncio
async def test_the_list_tool_shows_email_follow_ups_too(api_client, ada):
    sms = await _create(api_client, ada, channel="sms")
    mail = await _create(api_client, ada, channel="email")

    everything = await call_tool("list_followups")
    only_email = await call_tool("list_followups", channel="email")
    only_sms = await call_tool("list_followups", channel="sms")

    assert {i["id"] for i in everything["data"]["items"]} == {sms["id"], mail["id"]}
    assert [i["id"] for i in only_email["data"]["items"]] == [mail["id"]]
    assert [i["id"] for i in only_sms["data"]["items"]] == [sms["id"]]


@pytest.mark.asyncio
async def test_pending_means_every_pending_follow_up_whatever_day_it_is_due(api_client, api_db, ada):
    upcoming = await _create(api_client, ada, days=3)
    overdue = FollowUp(contact_id=ada.id, status="pending", channel="sms", message_text="late",
                       scheduled_at=datetime.now(timezone.utc) - timedelta(days=2))
    sent = FollowUp(contact_id=ada.id, status="sent", channel="sms", message_text="done",
                    scheduled_at=datetime.now(timezone.utc) - timedelta(days=1))
    api_db.add_all([overdue, sent])
    await api_db.flush()

    response = await api_client.get("/api/v1/followups/", params={"view": "pending", "channel": "all"})

    ids = {i["id"] for i in response.json()["items"]}
    assert ids == {upcoming["id"], overdue.id}, "pending = not yet sent, whenever it is due"


@pytest.mark.asyncio
async def test_the_dashboards_default_view_is_unchanged(api_client, ada):
    """The Follow-ups page still opens on 'due today' for SMS: only the assistant's defaults changed."""
    await _create(api_client, ada, days=7)

    response = await api_client.get("/api/v1/followups/")

    assert response.status_code == 200 and response.json()["items"] == []


@pytest.mark.asyncio
async def test_the_list_says_how_to_get_the_next_page(api_client, ada):
    for _ in range(3):
        await _create(api_client, ada)

    first = (await api_client.get("/api/v1/followups/", params={
        "view": "pending", "channel": "all", "per_page": 2})).json()
    last = (await api_client.get("/api/v1/followups/", params={
        "view": "pending", "channel": "all", "per_page": 2, "page": 2})).json()

    assert (first["page"], first["per_page"], first["next_page"], first["total"]) == (1, 2, 2, 3)
    assert (last["page"], last["next_page"], len(last["items"])) == (2, None, 1)


@pytest.mark.asyncio
async def test_a_bogus_view_or_channel_is_a_422(api_client):
    assert (await api_client.get("/api/v1/followups/", params={"view": "someday"})).status_code == 422
    assert (await api_client.get("/api/v1/followups/", params={"channel": "whatsapp"})).status_code == 422


@pytest.mark.asyncio
async def test_a_pending_follow_up_can_be_deleted(api_client, api_db, ada):
    created = await _create(api_client, ada)

    deleted = await api_client.delete(f"/api/v1/followups/{created['id']}")

    assert deleted.status_code == 204, deleted.text
    assert (await api_db.execute(select(FollowUp))).scalars().all() == []
    assert (await api_client.delete(f"/api/v1/followups/{created['id']}")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["sent", "delivered"])
async def test_a_follow_up_that_went_out_stays_in_the_history(api_client, api_db, ada, status):
    done = FollowUp(contact_id=ada.id, status=status, channel="sms", message_text="done",
                    scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1))
    api_db.add(done)
    await api_db.flush()

    response = await api_client.delete(f"/api/v1/followups/{done.id}")

    assert response.status_code == 409
    assert "already" in response.json()["message"].lower()
    assert (await api_db.execute(select(FollowUp))).scalars().all() == [done]


@pytest.mark.asyncio
async def test_deleting_a_campaign_follow_up_ends_that_contacts_automation(api_client, api_db, ada):
    """Same effect as skipping it: the campaign must not keep waiting on a follow-up that is gone."""
    campaign = Campaign(name="C", status="running", channel="sms")
    api_db.add(campaign)
    await api_db.flush()
    cc = CampaignContact(campaign_id=campaign.id, contact_id=ada.id, status="queued",
                         next_action_at=datetime.now(timezone.utc) + timedelta(days=1))
    api_db.add(cc)
    await api_db.flush()
    followup = FollowUp(contact_id=ada.id, campaign_id=campaign.id, campaign_contact_id=cc.id,
                        status="pending", channel="sms", message_text="x",
                        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
    api_db.add(followup)
    await api_db.flush()

    assert (await api_client.delete(f"/api/v1/followups/{followup.id}")).status_code == 204

    await api_db.refresh(cc)
    assert cc.status == "completed" and cc.next_action_at is None


@pytest.mark.asyncio
async def test_the_assistant_can_delete_a_follow_up(api_client, api_db, ada):
    created = await _create(api_client, ada)

    out = await call_tool("delete_followup", followup_id=created["id"])

    assert out["ok"] is True, out
    assert (await api_db.execute(select(FollowUp))).scalars().all() == []
