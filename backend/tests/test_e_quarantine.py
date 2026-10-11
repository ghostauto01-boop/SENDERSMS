"""§E — quarantine the pattern-generated addresses (tag them, never delete them).

~70% of the production list was reported to be generated role mailboxes (info@, sales@ ...) and 184 of
the addresses had already bounced. The send gate (P0-5) already refuses an address nobody verified, and
a guessed one (``email_source = "inferred"``) is held back even after that. This makes the same fact
VISIBLE and filterable: every inferred address gets the tags ``pattern_generated`` and ``sendable:false``.
Nothing is deleted, and a dry run (the default) changes nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.contact import Contact, ContactTag, Tag
from tests.mcp_helpers import call_tool


async def _contacts(db):
    rows = {
        "guess1": Contact(first_name="G1", email="info@guessco.io", email_source="inferred"),
        "guess2": Contact(first_name="G2", email="sales@guessco.io", email_source="inferred"),
        "guess_verified": Contact(first_name="G3", email="hello@guessco.io", email_source="inferred",
                                  email_verified=True, email_verdict="deliverable"),
        "typed": Contact(first_name="T", email="ada@realco.io", email_source="manual"),
        "csv": Contact(first_name="C", email="info@csvco.io", email_source="csv"),
        "phone_only": Contact(first_name="P", phone_number="+2348031200001"),
    }
    db.add_all(rows.values())
    await db.flush()
    return rows


async def _tags_of(db, contact):
    return sorted((await db.execute(
        select(Tag.name).join(ContactTag, ContactTag.tag_id == Tag.id).where(ContactTag.contact_id == contact.id)
    )).scalars().all())


@pytest.mark.asyncio
async def test_a_dry_run_counts_and_changes_nothing(api_client, api_db):
    rows = await _contacts(api_db)

    response = await api_client.post("/api/v1/contacts/quarantine/generated")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] is True and body["matched"] == 3 and body["tagged"] == 0
    assert (await api_db.execute(select(func.count(ContactTag.id)))).scalar_one() == 0
    assert {c.email for c in (await api_db.execute(select(Contact))).scalars().all()} >= {rows["guess1"].email}


@pytest.mark.asyncio
async def test_applying_tags_every_guessed_address_and_only_those(api_client, api_db):
    rows = await _contacts(api_db)

    body = (await api_client.post("/api/v1/contacts/quarantine/generated", params={"dry_run": "false"})).json()

    assert body["dry_run"] is False and body["matched"] == 3 and body["tagged"] == 3
    for key in ("guess1", "guess2", "guess_verified"):
        assert await _tags_of(api_db, rows[key]) == ["pattern_generated", "sendable:false"], key
    for key in ("typed", "csv", "phone_only"):
        assert await _tags_of(api_db, rows[key]) == [], f"{key} was not generated and is left alone"


@pytest.mark.asyncio
async def test_it_is_idempotent_and_never_deletes(api_client, api_db):
    await _contacts(api_db)
    before = (await api_db.execute(select(func.count(Contact.id)))).scalar_one()

    first = (await api_client.post("/api/v1/contacts/quarantine/generated", params={"dry_run": "false"})).json()
    second = (await api_client.post("/api/v1/contacts/quarantine/generated", params={"dry_run": "false"})).json()

    assert first["tagged"] == 3 and second["tagged"] == 0 and second["already_tagged"] == 3
    assert (await api_db.execute(select(func.count(Contact.id)))).scalar_one() == before, "nothing is deleted"
    assert (await api_db.execute(select(func.count(Tag.id)))).scalar_one() == 2, "the two tags exist once"


@pytest.mark.asyncio
async def test_legacy_tags_are_filterable_but_do_not_block_sending(api_client, api_db):
    from app.config import settings
    from app.services import email_service

    rows = await _contacts(api_db)
    await api_client.post("/api/v1/contacts/quarantine/generated", params={"dry_run": "false"})

    listing = (await api_client.get("/api/v1/contacts/", params={"tag": "pattern_generated", "per_page": 50})).json()

    assert {c["email"] for c in listing["items"]} == {"info@guessco.io", "sales@guessco.io", "hello@guessco.io"}
    # Legacy labels are informational, not suppression.
    assert settings.EMAIL_SEND_INFERRED is True
    assert await email_service.contact_email_problem(api_db, rows["guess_verified"], outreach=True) is None


@pytest.mark.asyncio
async def test_the_report_counts_unverified_role_mailboxes_without_tagging_them(api_client, api_db):
    """A role address that someone TYPED or imported is a real address, not a guess: reported, not tagged."""
    await _contacts(api_db)

    body = (await api_client.post("/api/v1/contacts/quarantine/generated")).json()

    assert body["role_mailboxes_unverified"] == 3   # info@guessco, sales@guessco, info@csvco (hello@ is verified)
    assert body["examples"] and len(body["examples"]) <= 5


@pytest.mark.asyncio
async def test_the_assistant_tool_defaults_to_a_dry_run():
    import app.mcp.server as server

    seen = []

    async def fake(method, path, **kw):
        seen.append(kw.get("query"))
        return 200, {"dry_run": True, "matched": 0}

    original = server.call_app
    server.call_app = fake
    try:
        out = await call_tool("quarantine_generated_addresses")
    finally:
        server.call_app = original

    assert out["ok"] is True
    assert seen[0] == {"dry_run": True}, "an assistant that forgets the flag must not change data"
