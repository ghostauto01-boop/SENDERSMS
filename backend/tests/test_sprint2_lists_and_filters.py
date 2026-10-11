"""Sprint 2 — C8 (the rest): duplicate list names are a 409, a blank name and a bogus
``channel`` filter are a 422.

Reproduced first: three lists named "Leads"/"Leads"/"leads " all came back ``201``, so a
campaign pointed at "the Leads list" had three candidates and nothing said which; and
``/inbox/conversations?channel=whatsapp`` answered ``200`` with an empty page, so a typo
looked like "no conversations".
"""

from __future__ import annotations

import pytest


async def _create(client, name):
    return await client.post("/api/v1/lists/", params={"name": name})


@pytest.mark.asyncio
async def test_a_second_list_with_the_same_name_is_a_409(api_client):
    first = await _create(api_client, "Leads")
    second = await _create(api_client, "Leads")

    assert first.status_code == 201
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["code"] == "LIST_NAME_EXISTS" and body["existing_id"] == first.json()["id"]
    assert "Leads" in body["message"] and str(first.json()["id"]) in body["message"]
    lists = (await api_client.get("/api/v1/lists/")).json()
    assert [row["name"] for row in lists["items"]] == ["Leads"], "the duplicate was not created"


@pytest.mark.asyncio
@pytest.mark.parametrize("again", ["leads", "LEADS", "  Leads  ", "leads "])
async def test_the_comparison_ignores_case_and_surrounding_spaces(api_client, again):
    await _create(api_client, "Leads")

    assert (await _create(api_client, again)).status_code == 409


@pytest.mark.asyncio
async def test_different_names_are_fine(api_client):
    assert (await _create(api_client, "Leads")).status_code == 201
    assert (await _create(api_client, "Leads 2026")).status_code == 201


@pytest.mark.asyncio
async def test_a_blank_name_is_a_422_not_an_empty_list(api_client):
    response = await _create(api_client, "   ")

    assert response.status_code == 422
    assert (await api_client.get("/api/v1/lists/")).json()["total"] == 0


@pytest.mark.asyncio
async def test_renaming_a_list_to_another_lists_name_is_a_409(api_client):
    a = (await _create(api_client, "Alpha")).json()["id"]
    await _create(api_client, "Beta")

    clash = await api_client.put(f"/api/v1/lists/{a}", params={"name": "beta"})
    same_list_new_case = await api_client.put(f"/api/v1/lists/{a}", params={"name": "ALPHA"})
    fine = await api_client.put(f"/api/v1/lists/{a}", params={"name": "Gamma"})

    assert clash.status_code == 409 and clash.json()["code"] == "LIST_NAME_EXISTS"
    assert same_list_new_case.status_code == 200, "a list may be renamed to its own name in another case"
    assert fine.status_code == 200


@pytest.mark.asyncio
async def test_an_import_that_names_a_new_list_that_already_exists_is_a_409(api_client):
    existing = (await _create(api_client, "Leads")).json()["id"]
    csv = b"phone_number,first_name\n+2348031200001,Ada\n"

    clash = await api_client.post(
        "/api/v1/contacts/import/jobs", files={"file": ("c.csv", csv, "text/csv")},
        data={"new_list_name": "leads"})
    fresh = await api_client.post(
        "/api/v1/contacts/import/jobs", files={"file": ("c.csv", csv, "text/csv")},
        data={"new_list_name": "Brand new"})

    assert clash.status_code == 409, clash.text
    assert clash.json()["existing_id"] == existing
    assert fresh.status_code == 202, fresh.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/inbox/conversations", "/api/v1/inbox/campaign-filters"])
async def test_a_bogus_channel_filter_is_a_422_not_an_empty_page(api_client, path):
    response = await api_client.get(path, params={"channel": "whatsapp"})

    assert response.status_code == 422, response.text
    assert "channel" in response.json()["message"].lower()
    assert "whatsapp" in response.json()["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["sms", "email", "all", "EMAIL"])
async def test_the_real_channels_still_work(api_client, channel):
    assert (await api_client.get("/api/v1/inbox/conversations", params={"channel": channel})).status_code == 200
    assert (await api_client.get("/api/v1/inbox/campaign-filters", params={"channel": channel})).status_code == 200
