"""Route-level tests for removing contacts from a campaign audience.

Covers the audience GET endpoint's ``removable`` view and the
``scope="all"`` bulk-remove that backs "select all unsent & delete them from
the campaign" in the campaign Audience tab.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.ads import AdsAssignment, AdsCampaign, AdsSet
from app.models.contact import Contact
from app.models.user import User
from app.security.auth import get_current_user


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    from app.main import app

    async def _get_db():
        yield db

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1,
        username="tester",
        email="tester@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    app.dependency_overrides.clear()


async def _seed(db) -> int:
    """Two unsent + one sent audience row; returns the campaign id."""
    from datetime import datetime, timezone

    campaign = AdsCampaign(name="Lagos Lunch Blast", status="active")
    db.add(campaign)
    await db.flush()

    ads_set = AdsSet(campaign_id=campaign.id, name="Set A")
    db.add(ads_set)
    await db.flush()

    for i, (status, sent_at) in enumerate(
        [("pending", None), ("skipped", None), ("delivered", datetime.now(timezone.utc))]
    ):
        contact = Contact(
            phone_number=f"+23480312345{i:02d}",
            first_name=f"Person{i}",
            country="Nigeria",
        )
        db.add(contact)
        await db.flush()
        db.add(
            AdsAssignment(
                campaign_id=campaign.id,
                set_id=ads_set.id,
                creative_id=1,
                contact_id=contact.id,
                phone_number=contact.phone_number,
                send_status=status,
                sent_at=sent_at,
            )
        )
    await db.flush()
    return campaign.id


@pytest.mark.asyncio
async def test_audience_removable_view_counts_only_unsent(client, db):
    campaign_id = await _seed(db)

    response = await client.get(
        f"/api/v1/ads/campaigns/{campaign_id}/audience", params={"removable": True}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["removable"] == 2
    assert {item["send_status"] for item in body["items"]} == {"pending", "skipped"}

    # Without the flag the sent row shows up too.
    response = await client.get(f"/api/v1/ads/campaigns/{campaign_id}/audience")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["removable"] == 2  # the sent row is never counted as removable


@pytest.mark.asyncio
async def test_bulk_remove_scope_all_removes_every_unsent_row(client, db):
    campaign_id = await _seed(db)

    response = await client.post(
        f"/api/v1/ads/campaigns/{campaign_id}/audience/bulk-remove",
        json={"ids": [], "action": "remove", "scope": "all"},
    )
    assert response.status_code == 200
    assert response.json() == {"removed": 2, "skipped_sent": 0, "missing": 0}

    remaining = (await db.execute(select(AdsAssignment))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].send_status == "delivered"

    # A second remove-all is a no-op — nothing unsent is left.
    response = await client.post(
        f"/api/v1/ads/campaigns/{campaign_id}/audience/bulk-remove",
        json={"ids": [], "action": "remove", "scope": "all"},
    )
    assert response.status_code == 200
    assert response.json() == {"removed": 0, "skipped_sent": 0, "missing": 0}


@pytest.mark.asyncio
async def test_bulk_remove_default_scope_still_uses_ids(client, db):
    campaign_id = await _seed(db)

    rows = list((await db.execute(select(AdsAssignment))).scalars().all())
    removable = [row for row in rows if row.send_status != "delivered"]

    response = await client.post(
        f"/api/v1/ads/campaigns/{campaign_id}/audience/bulk-remove",
        json={"ids": [removable[0].id], "action": "remove"},
    )
    assert response.status_code == 200
    assert response.json() == {"removed": 1, "skipped_sent": 0, "missing": 0}


@pytest.mark.asyncio
async def test_bulk_remove_scope_all_respects_status_filter(client, db):
    campaign_id = await _seed(db)

    # "Remove all unsent" while the tab is filtered to skipped must only
    # remove skipped rows — never the pending ones from the other filter.
    response = await client.post(
        f"/api/v1/ads/campaigns/{campaign_id}/audience/bulk-remove",
        json={"ids": [], "action": "remove", "scope": "all", "status": "skipped"},
    )
    assert response.status_code == 200
    assert response.json() == {"removed": 1, "skipped_sent": 0, "missing": 0}

    remaining = list((await db.execute(select(AdsAssignment))).scalars().all())
    assert sorted(row.send_status for row in remaining) == ["delivered", "pending"]
