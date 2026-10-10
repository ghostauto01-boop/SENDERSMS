"""P0-2 — one campaign directory across both campaign systems.

The app has two campaign engines: the classic ``campaigns`` table and the Ads
Manager's ``ads_campaigns``. Their id spaces overlap (each has an id 1, an id 2,
...). The QA sweep found that

* ``list_campaigns`` / ``get_campaign`` only ever read the classic table, so the
  campaign that was actually *running* (an Ads Manager one) was invisible, and
  ``get_campaign(2)`` returned a *different*, unrelated classic campaign;
* ``dashboard_stats.active_campaigns`` counted classic campaigns only.

The contract these tests pin: every campaign id surfaced anywhere (overview,
inbox badges, the directory list) resolves through ``get_campaign`` when given
the ``kind`` it was surfaced with; an id that exists in both systems and is asked
for *without* a kind is reported as ambiguous instead of silently picking one.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.mcp import server
from app.mcp.registry import TOOLS_BY_NAME
from app.models.ads import AdsCampaign
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.conversation import Conversation

LEGACY_RUNNING = "Legacy SMS blast"
LEGACY_DRAFT_EMAIL = "Legacy email draft"
ADS_EMAIL_ONE = "Ads email warm-up"
ADS_UGC = "Trybe UGC Image Ads - Cold Outreach"
ADS_SMS_PAUSED = "Ads SMS paused"


@pytest_asyncio.fixture
async def seeded(api_db):
    """Two classic + three ads campaigns, so ids 1 and 2 exist in BOTH systems."""
    legacy = [
        Campaign(name=LEGACY_RUNNING, status="running", channel="sms"),
        Campaign(name=LEGACY_DRAFT_EMAIL, status="draft", channel="email"),
    ]
    ads = [
        AdsCampaign(name=ADS_EMAIL_ONE, status="active", channel="email"),
        AdsCampaign(name=ADS_UGC, status="active", channel="email"),
        AdsCampaign(name=ADS_SMS_PAUSED, status="paused", channel="sms"),
    ]
    api_db.add_all(legacy + ads)
    await api_db.flush()
    # The whole point of the bug: the same number names two different campaigns.
    assert legacy[1].id == ads[1].id == 2
    return {"legacy": legacy, "ads": ads}


async def _directory(client, **params):
    response = await client.get("/api/v1/overview/campaigns", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _names(payload) -> set[str]:
    return {row["name"] for row in payload["items"]}


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_campaigns_from_both_systems_with_a_kind(api_client, seeded):
    payload = await _directory(api_client)

    assert _names(payload) == {
        LEGACY_RUNNING, LEGACY_DRAFT_EMAIL, ADS_EMAIL_ONE, ADS_UGC, ADS_SMS_PAUSED,
    }
    for row in payload["items"]:
        assert row["kind"] in ("campaign", "ads")
        assert row["system"] == ("ads" if row["kind"] == "ads" else "legacy")
        assert row["channel"] in ("sms", "email")
    ids_by_kind = {(r["kind"], r["id"]) for r in payload["items"]}
    assert ("campaign", 2) in ids_by_kind and ("ads", 2) in ids_by_kind


@pytest.mark.asyncio
async def test_list_filters_by_kind_channel_status_and_search(api_client, seeded):
    ads = await _directory(api_client, kind="ads")
    assert _names(ads) == {ADS_EMAIL_ONE, ADS_UGC, ADS_SMS_PAUSED}

    for alias in ("legacy", "campaign"):
        legacy = await _directory(api_client, kind=alias)
        assert _names(legacy) == {LEGACY_RUNNING, LEGACY_DRAFT_EMAIL}, alias

    email = await _directory(api_client, channel="email")
    assert _names(email) == {LEGACY_DRAFT_EMAIL, ADS_EMAIL_ONE, ADS_UGC}

    active = await _directory(api_client, status="active")
    assert _names(active) == {ADS_EMAIL_ONE, ADS_UGC}

    found = await _directory(api_client, search="trybe")
    assert _names(found) == {ADS_UGC}


@pytest.mark.asyncio
async def test_list_rejects_an_unknown_kind_or_channel(api_client, seeded):
    assert (await api_client.get("/api/v1/overview/campaigns", params={"kind": "bogus"})
            ).status_code == 422
    assert (await api_client.get("/api/v1/overview/campaigns", params={"channel": "fax"})
            ).status_code == 422


@pytest.mark.asyncio
async def test_list_paginates_and_never_hides_how_many_there_are(api_client, seeded):
    first = await _directory(api_client, page=1, per_page=2)
    second = await _directory(api_client, page=2, per_page=2)
    third = await _directory(api_client, page=3, per_page=2)

    assert first["total"] == second["total"] == third["total"] == 5
    assert [len(p["items"]) for p in (first, second, third)] == [2, 2, 1]
    seen = [(r["kind"], r["id"]) for p in (first, second, third) for r in p["items"]]
    assert len(seen) == len(set(seen)) == 5, "pages must not overlap or drop rows"
    assert first["next_page"] == 2 and third["next_page"] is None


# --------------------------------------------------------------------------
# get
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_with_a_kind_returns_that_systems_campaign(api_client, seeded):
    ads = await api_client.get("/api/v1/overview/campaigns/2", params={"kind": "ads"})
    assert ads.status_code == 200, ads.text
    assert ads.json()["name"] == ADS_UGC
    assert ads.json()["kind"] == "ads"

    for alias in ("campaign", "legacy"):
        legacy = await api_client.get("/api/v1/overview/campaigns/2", params={"kind": alias})
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["name"] == LEGACY_DRAFT_EMAIL, alias
        assert legacy.json()["kind"] == "campaign"


@pytest.mark.asyncio
async def test_get_without_a_kind_is_ambiguous_when_both_systems_have_the_id(api_client, seeded):
    response = await api_client.get("/api/v1/overview/campaigns/2")

    assert response.status_code == 409, "must not silently pick one of two campaigns"
    body = response.json()
    assert "kind" in (body.get("detail") or body.get("message") or "").lower()
    candidates = body.get("candidates") or body.get("detail_data", {}).get("candidates")
    assert candidates is not None, body
    assert {(c["kind"], c["name"]) for c in candidates} == {
        ("campaign", LEGACY_DRAFT_EMAIL), ("ads", ADS_UGC),
    }


@pytest.mark.asyncio
async def test_get_without_a_kind_resolves_when_only_one_system_has_the_id(api_client, seeded):
    response = await api_client.get("/api/v1/overview/campaigns/3")  # only ads has a 3rd
    assert response.status_code == 200, response.text
    assert response.json()["name"] == ADS_SMS_PAUSED
    assert response.json()["kind"] == "ads"


@pytest.mark.asyncio
async def test_get_missing_campaign_is_404_and_wrong_kind_is_404(api_client, seeded):
    assert (await api_client.get("/api/v1/overview/campaigns/999")).status_code == 404
    assert (await api_client.get("/api/v1/overview/campaigns/3", params={"kind": "campaign"})
            ).status_code == 404
    assert (await api_client.get("/api/v1/overview/campaigns/2", params={"kind": "bogus"})
            ).status_code == 422


@pytest.mark.asyncio
async def test_get_returns_the_full_definition_not_just_a_summary(api_client, seeded):
    legacy = (await api_client.get("/api/v1/overview/campaigns/1", params={"kind": "campaign"})).json()
    ads = (await api_client.get("/api/v1/overview/campaigns/2", params={"kind": "ads"})).json()
    assert "message_body" in legacy and "list_id" in legacy
    assert "sets" in ads and "creatives" in ads


# --------------------------------------------------------------------------
# "any id surfaced anywhere resolves"
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_campaign_id_surfaced_anywhere_resolves_via_get_campaign(
    api_client, api_db, seeded
):
    contact_a = Contact(phone_number="+2348030000001", first_name="A")
    contact_b = Contact(phone_number="+2348030000002", first_name="B")
    api_db.add_all([contact_a, contact_b])
    await api_db.flush()
    # One lead sourced by the classic campaign 2, one by the ads campaign 2.
    api_db.add_all([
        Conversation(contact_id=contact_a.id, channel="sms", campaign_id=2),
        Conversation(contact_id=contact_b.id, channel="sms", ads_campaign_id=2),
    ])
    await api_db.flush()

    surfaced: dict[tuple[str, int], str] = {}

    # 1. the directory itself
    for row in (await _directory(api_client))["items"]:
        surfaced[(row["kind"], row["id"])] = row["name"]

    # 2. the inbox badges
    inbox = await api_client.get("/api/v1/inbox/conversations", params={"channel": "all"})
    assert inbox.status_code == 200, inbox.text
    for item in inbox.json()["items"]:
        for key in ("campaign", "last_campaign"):
            label = item.get(key)
            if label:
                surfaced[(label["kind"], label["id"])] = label["name"]

    assert ("campaign", 2) in surfaced and ("ads", 2) in surfaced

    # 3. every one of them resolves, to the same campaign it was surfaced as
    for (kind, campaign_id), name in surfaced.items():
        resolved = await api_client.get(
            f"/api/v1/overview/campaigns/{campaign_id}", params={"kind": kind}
        )
        assert resolved.status_code == 200, f"{kind} {campaign_id}: {resolved.text}"
        assert resolved.json()["name"] == name, f"{kind} {campaign_id} resolved to the wrong campaign"


@pytest.mark.asyncio
async def test_each_systems_own_endpoints_label_their_rows_with_a_kind(api_client, seeded):
    """An id read from /campaigns or /ads/campaigns is self-describing too."""
    classic = await api_client.get("/api/v1/campaigns/", params={"channel": "all"})
    assert {row["kind"] for row in classic.json()["items"]} == {"campaign"}
    assert (await api_client.get("/api/v1/campaigns/1")).json()["kind"] == "campaign"

    ads = await api_client.get("/api/v1/ads/campaigns", params={"channel": "all"})
    assert {row["kind"] for row in ads.json()["items"]} == {"ads"}
    assert (await api_client.get("/api/v1/ads/campaigns/1")).json()["kind"] == "ads"


# --------------------------------------------------------------------------
# dashboard_stats.active_campaigns
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_active_campaigns_counts_both_systems(api_client, seeded):
    response = await api_client.get("/api/v1/dashboard/stats")
    assert response.status_code == 200, response.text
    body = response.json()

    # classic: 1 running; ads: 2 active (the paused ads campaign is not active)
    assert body["active_campaigns"] == 3
    assert body["active_campaigns_by_kind"] == {"campaign": 1, "ads": 2}


@pytest.mark.asyncio
async def test_dashboard_active_campaigns_does_not_count_paused_or_draft(api_client, api_db):
    api_db.add_all([
        Campaign(name="d", status="draft"), Campaign(name="p", status="paused"),
        Campaign(name="s", status="scheduled"),
        AdsCampaign(name="ad", status="draft"), AdsCampaign(name="ap", status="paused"),
    ])
    await api_db.flush()
    body = (await api_client.get("/api/v1/dashboard/stats")).json()
    assert body["active_campaigns"] == 0


# --------------------------------------------------------------------------
# the MCP tools, end to end against the real routes
# --------------------------------------------------------------------------


class _Stub:
    def add(self, row):
        pass

    async def flush(self):
        return None


async def _call(tool: str, **args) -> dict:
    server.CURRENT_ADMIN_ID.set(1)
    outcome = await server.run_tool(
        _Stub(), server.TokenView(id=1, name="t", scope="write", prefix="mcp_t"),
        TOOLS_BY_NAME[tool], args,
    )
    result = outcome.result if hasattr(outcome, "result") else outcome
    text = result["content"][0]["text"]
    is_error = bool(result.get("isError", False))
    return {"is_error": is_error, "text": text, "json": None if is_error else _loads(text)}


def _loads(text: str):
    try:
        return json.loads(text)
    except ValueError:
        # Tool output is "HTTP 200\n{json}" or similar; find the JSON body.
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        return json.loads(text[start:]) if start >= 0 else None


@pytest.mark.asyncio
async def test_mcp_list_campaigns_sees_the_ads_campaigns_too(api_client, seeded):
    result = await _call("list_campaigns", channel="all")
    assert not result["is_error"], result["text"]
    names = {row["name"] for row in result["json"]["items"]}
    assert ADS_UGC in names and LEGACY_RUNNING in names


@pytest.mark.asyncio
async def test_mcp_get_campaign_distinguishes_the_two_id_spaces(api_client, seeded):
    ads = await _call("get_campaign", campaign_id=2, kind="ads")
    legacy = await _call("get_campaign", campaign_id=2, kind="campaign")
    assert ads["json"]["name"] == ADS_UGC
    assert legacy["json"]["name"] == LEGACY_DRAFT_EMAIL


@pytest.mark.asyncio
async def test_mcp_get_campaign_explains_the_ambiguity_instead_of_guessing(api_client, seeded):
    result = await _call("get_campaign", campaign_id=2)
    assert result["is_error"] is True
    assert "kind" in result["text"].lower()
    assert ADS_UGC in result["text"] and LEGACY_DRAFT_EMAIL in result["text"]
