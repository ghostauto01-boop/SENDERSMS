"""P0-1 — campaign lifecycle: ``validate`` must report, never mutate.

The QA sweep found, live, that the legacy campaign API had a one-way trap:

* ``POST /campaigns/{id}/validate`` is described as a check, but it moved the
  campaign to ``scheduled`` (and ``launch_due_campaigns`` auto-launches any
  ``scheduled`` campaign whose launch time has passed - so *validating* could
  arm a send);
* once ``scheduled``, a campaign could be neither paused nor deleted;
* ``schedule`` with ``null`` returned "Schedule cleared" but left the status
  ``scheduled``.

Every test here drives the real HTTP API. The state-machine tests derive the
transition graph *empirically* from the endpoints rather than trusting the
``TRANSITIONS`` table, then check the table agrees with reality.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.campaign import Campaign
from app.services.campaign_service import CampaignService
from tests.campaign_factory import in_hours, make_campaign

pytestmark = pytest.mark.usefixtures("sms_gateway_configured", "stub_queue")


async def _get(client, campaign_id):
    response = await client.get(f"/api/v1/campaigns/{campaign_id}")
    return response


async def _status(client, campaign_id):
    response = await _get(client, campaign_id)
    assert response.status_code == 200, response.text
    return response.json()["status"]


@pytest.fixture
def launcher(api_db, monkeypatch):
    """``launch_due_campaigns_async`` bound to the test session."""
    import app.tasks.campaign_tasks as ct

    enqueued: list[int] = []

    @asynccontextmanager
    async def _factory():
        yield api_db

    monkeypatch.setattr(ct, "async_session_factory", _factory)
    monkeypatch.setattr(ct, "try_enqueue", lambda task, *a, **k: enqueued.append(a[0]) or True)

    async def _run():
        return await ct.launch_due_campaigns_async()

    _run.enqueued = enqueued
    return _run


# --------------------------------------------------------------------------
# validate is report-only
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_twice_leaves_status_unchanged(api_client, api_db):
    campaign = await make_campaign(api_db)
    for _ in range(2):
        response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["valid"] is True
        assert body["status"] == "draft"
        assert body["changed"] is False
        assert await _status(api_client, campaign.id) == "draft"
    await api_db.refresh(campaign)
    assert campaign.scheduled_at is None, "validate must not stamp a scheduled time"


@pytest.mark.asyncio
async def test_validate_does_not_arm_an_automatic_launch(api_client, api_db, launcher):
    """The production chain: a launch time in the past + validate = a live send."""
    campaign = await make_campaign(
        api_db, scheduled_start_at=datetime.now(timezone.utc) - timedelta(minutes=5)
    )
    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")
    assert response.status_code == 200, response.text

    launched = await launcher()

    assert launched == [], "validating must never cause a campaign to launch"
    assert launcher.enqueued == []
    assert await _status(api_client, campaign.id) == "draft"


@pytest.mark.asyncio
async def test_validate_reports_problems_without_changing_anything(api_client, api_db):
    campaign = Campaign(name="No audience", status="draft", message_body="hi")
    api_db.add(campaign)
    await api_db.flush()

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/validate")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is False
    assert any("contact list" in e.lower() for e in body["errors"])
    assert body["status"] == "draft"
    assert await _status(api_client, campaign.id) == "draft"


@pytest.mark.asyncio
async def test_validate_unknown_campaign_is_404(api_client):
    response = await api_client.post("/api/v1/campaigns/987654/validate")
    assert response.status_code == 404


# --------------------------------------------------------------------------
# schedule: set, clear, and honesty about no-ops
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_with_a_time_arms_the_campaign(api_client, api_db):
    campaign = await make_campaign(api_db)
    when = in_hours(3)
    response = await api_client.post(
        f"/api/v1/campaigns/{campaign.id}/schedule",
        json={"scheduled_start_at": when.isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "scheduled"
    assert response.json()["changed"] is True
    assert await _status(api_client, campaign.id) == "scheduled"


@pytest.mark.asyncio
async def test_schedule_null_returns_campaign_to_draft(api_client, api_db):
    campaign = await make_campaign(api_db, status="scheduled", scheduled_start_at=in_hours(2))

    response = await api_client.post(
        f"/api/v1/campaigns/{campaign.id}/schedule", json={"scheduled_start_at": None}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "draft"
    assert body["changed"] is True
    assert body["scheduled_start_at"] is None
    got = (await _get(api_client, campaign.id)).json()
    assert got["status"] == "draft"
    assert got["scheduled_start_at"] is None


@pytest.mark.asyncio
async def test_schedule_null_on_an_unscheduled_draft_admits_it_did_nothing(api_client, api_db):
    campaign = await make_campaign(api_db)
    response = await api_client.post(
        f"/api/v1/campaigns/{campaign.id}/schedule", json={"scheduled_start_at": None}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is False
    assert "cleared" not in body["message"].lower()
    assert await _status(api_client, campaign.id) == "draft"


# --------------------------------------------------------------------------
# scheduled has real exits: pause and delete
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_campaign_can_be_paused_and_remembers_where_from(api_client, api_db):
    campaign = await make_campaign(api_db, status="scheduled", scheduled_start_at=in_hours(4))

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/pause")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "paused"
    got = (await _get(api_client, campaign.id)).json()
    assert got["status"] == "paused"
    assert got["paused_from"] == "scheduled"


@pytest.mark.asyncio
async def test_resume_of_a_paused_schedule_returns_to_scheduled_not_running(
    api_client, api_db, stub_queue
):
    when = in_hours(4)
    campaign = await make_campaign(api_db, status="scheduled", scheduled_start_at=when)
    await api_client.post(f"/api/v1/campaigns/{campaign.id}/pause")

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/resume")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "scheduled"
    got = (await _get(api_client, campaign.id)).json()
    assert got["status"] == "scheduled"
    assert got["paused_from"] is None
    assert got["scheduled_start_at"] is not None
    assert stub_queue == [], "resuming a paused *schedule* must not start sending"


@pytest.mark.asyncio
async def test_resume_after_the_launch_time_passed_does_not_auto_send(
    api_client, api_db, launcher, stub_queue
):
    campaign = await make_campaign(api_db, status="scheduled", scheduled_start_at=in_hours(1))
    await api_client.post(f"/api/v1/campaigns/{campaign.id}/pause")
    # Time passes while it is paused.
    await api_db.refresh(campaign)
    campaign.scheduled_start_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await api_db.flush()

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/resume")

    assert response.status_code == 200, response.text
    assert await launcher() == [], "a stale launch time must not fire on resume"
    assert await _status(api_client, campaign.id) == "scheduled"
    assert "start" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_a_paused_schedule_is_not_launched_by_the_poller(api_client, api_db, launcher):
    campaign = await make_campaign(api_db, status="scheduled", scheduled_start_at=in_hours(1))
    await api_client.post(f"/api/v1/campaigns/{campaign.id}/pause")
    await api_db.refresh(campaign)
    campaign.scheduled_start_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await api_db.flush()

    assert await launcher() == []
    assert await _status(api_client, campaign.id) == "paused"


@pytest.mark.asyncio
async def test_scheduled_campaign_with_no_sends_can_be_deleted(api_client, api_db):
    campaign = await make_campaign(api_db, status="scheduled", scheduled_start_at=in_hours(2))

    response = await api_client.delete(f"/api/v1/campaigns/{campaign.id}")

    assert response.status_code == 204, response.text
    assert (await _get(api_client, campaign.id)).status_code == 404


@pytest.mark.asyncio
async def test_scheduled_campaign_that_has_sent_cannot_be_deleted(api_client, api_db):
    campaign = await make_campaign(
        api_db, status="scheduled", scheduled_start_at=in_hours(2), messages_sent=7
    )

    response = await api_client.delete(f"/api/v1/campaigns/{campaign.id}")

    assert response.status_code == 409, response.text
    assert (await _get(api_client, campaign.id)).status_code == 200


@pytest.mark.asyncio
async def test_start_runs_a_draft_by_validating_inline(api_client, api_db, stub_queue):
    campaign = await make_campaign(api_db)

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert response.status_code == 200, response.text
    assert await _status(api_client, campaign.id) == "running"
    assert stub_queue and stub_queue[0][1] == (campaign.id,)


@pytest.mark.asyncio
async def test_start_refuses_an_invalid_draft_and_says_why(api_client, api_db, stub_queue):
    campaign = Campaign(name="No list", status="draft", message_body="hi")
    api_db.add(campaign)
    await api_db.flush()

    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/start")

    assert response.status_code == 400
    assert "contact list" in response.json()["detail"].lower()
    assert await _status(api_client, campaign.id) == "draft"
    assert stub_queue == []


# --------------------------------------------------------------------------
# The state machine, derived from the endpoints
# --------------------------------------------------------------------------

TERMINAL = {"completed", "stopped"}

ACTIONS = {
    "start": lambda c, i: c.post(f"/api/v1/campaigns/{i}/start"),
    "pause": lambda c, i: c.post(f"/api/v1/campaigns/{i}/pause"),
    "resume": lambda c, i: c.post(f"/api/v1/campaigns/{i}/resume"),
    "stop": lambda c, i: c.post(f"/api/v1/campaigns/{i}/stop"),
    "schedule": lambda c, i: c.post(
        f"/api/v1/campaigns/{i}/schedule",
        json={"scheduled_start_at": in_hours(6).isoformat()},
    ),
    "unschedule": lambda c, i: c.post(
        f"/api/v1/campaigns/{i}/schedule", json={"scheduled_start_at": None}
    ),
    "edit": lambda c, i: c.put(f"/api/v1/campaigns/{i}", json={"name": "Renamed"}),
    "delete": lambda c, i: c.delete(f"/api/v1/campaigns/{i}"),
    "validate": lambda c, i: c.post(f"/api/v1/campaigns/{i}/validate"),
}


def _fingerprint(resource: dict | None) -> dict | None:
    """The campaign as the API shows it, minus the always-moving timestamp."""
    if resource is None:
        return None
    return {k: v for k, v in resource.items() if k != "updated_at"}


async def _observe(client, db, status: str, action: str):
    """Seed a fresh campaign in ``status``, run ``action``, report what happened."""
    campaign = await make_campaign(db, status=status)
    if status == "scheduled":
        campaign.scheduled_start_at = in_hours(5)
        await db.flush()
    campaign_id = campaign.id
    before = (await _get(client, campaign_id)).json()
    response = await ACTIONS[action](client, campaign_id)
    after = await _get(client, campaign_id)
    exists = after.status_code == 200
    after_json = after.json() if exists else None
    return {
        "ok": 200 <= response.status_code < 300,
        "code": response.status_code,
        "body": response.json() if response.content and response.status_code != 204 else {},
        "exists": exists,
        "after": after_json["status"] if exists else None,
        "unchanged": exists and _fingerprint(before) == _fingerprint(after_json),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(CampaignService.VALID_STATUSES))
async def test_every_non_terminal_status_has_an_exit(api_client, api_db, status):
    exits = {}
    for action in ACTIONS:
        seen = await _observe(api_client, api_db, status, action)
        if seen["ok"] and (not seen["exists"] or seen["after"] != status):
            exits[action] = seen["after"] or "deleted"

    if status in TERMINAL:
        assert exits == {}, f"{status} is terminal by design but left via {exits}"
        return
    assert exits, f"{status} is a dead end: no endpoint moves a campaign out of it"


@pytest.mark.asyncio
async def test_scheduled_has_at_least_two_distinct_exits_including_pause_and_delete(
    api_client, api_db
):
    exits = {}
    for action in ACTIONS:
        seen = await _observe(api_client, api_db, "scheduled", action)
        if seen["ok"] and (not seen["exists"] or seen["after"] != "scheduled"):
            exits[action] = seen["after"] or "deleted"

    assert exits.get("pause") == "paused"
    assert exits.get("delete") == "deleted"
    assert exits.get("unschedule") == "draft"
    assert len(set(exits.values())) >= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(CampaignService.VALID_STATUSES))
async def test_observed_transitions_match_the_declared_table(api_client, api_db, status):
    """TRANSITIONS used to be decoration; it must describe what the API does."""
    for action in ACTIONS:
        seen = await _observe(api_client, api_db, status, action)
        if seen["ok"] and seen["exists"] and seen["after"] != status:
            allowed = CampaignService.TRANSITIONS[status]
            assert seen["after"] in allowed, (
                f"{action} moved {status} -> {seen['after']}, "
                f"which TRANSITIONS does not allow ({sorted(allowed)})"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(CampaignService.VALID_STATUSES))
async def test_no_endpoint_claims_success_while_changing_nothing(api_client, api_db, status):
    """A 2xx that left the resource as it was must say so (``changed: false``)."""
    for action in ACTIONS:
        seen = await _observe(api_client, api_db, status, action)
        if not seen["ok"]:
            continue
        if seen["unchanged"]:
            assert seen["body"].get("changed") is False, (
                f"{action} on a {status} campaign returned {seen['code']} "
                f"{seen['body']} but nothing changed"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(TERMINAL))
async def test_terminal_campaigns_can_still_be_duplicated(api_client, api_db, status):
    campaign = await make_campaign(api_db, status=status)
    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/duplicate")
    assert response.status_code in (200, 201), response.text
    assert response.json()["status"] == "draft"


# --------------------------------------------------------------------------
# C11 (shipped with P0-1 because it is the same error plumbing)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix",
    [("post", "/start"), ("post", "/pause"), ("post", "/resume"), ("post", "/stop"),
     ("post", "/validate"), ("delete", "")],
)
async def test_actions_on_a_missing_campaign_are_404_not_400(api_client, method, suffix):
    response = await getattr(api_client, method)(f"/api/v1/campaigns/424242{suffix}")
    assert response.status_code == 404, f"{method} {suffix or '/'} -> {response.status_code}"


@pytest.mark.asyncio
async def test_stop_is_recorded_and_pending_work_cancelled(api_client, api_db):
    campaign = await make_campaign(api_db, status="scheduled", scheduled_start_at=in_hours(2))
    response = await api_client.post(f"/api/v1/campaigns/{campaign.id}/stop")
    assert response.status_code == 200, response.text
    row = (await api_db.execute(select(Campaign).where(Campaign.id == campaign.id))).scalar_one()
    assert row.status == "stopped"
