"""Creatives can be bound to a saved template (the composer's "sync" anchor).

The binding is a pointer only: a creative's text and versions stay the source
of truth for what is sent, but remembering which template a creative started
from lets the UI detect later template edits and offer a one-click re-sync.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.ads import AdsCampaign, AdsCreative, AdsSet
from app.models.template import Template
from app.models.user import User
from app.security.auth import get_current_user


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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


async def _scaffold(db) -> tuple[AdsSet, Template]:
    campaign = AdsCampaign(name="Outreach", objective="replies", status="active", test_mode=True)
    db.add(campaign)
    await db.flush()
    ads_set = AdsSet(campaign_id=campaign.id, name="Set A", status="active")
    db.add(ads_set)
    await db.flush()
    template = Template(name="Welcome", body="Hi {{first_name}}, welcome!")
    db.add(template)
    await db.flush()
    return ads_set, template


@pytest.mark.asyncio
async def test_create_creative_from_template_keeps_binding(client, db):
    ads_set, template = await _scaffold(db)

    response = await client.post(
        f"/api/v1/ads/sets/{ads_set.id}/creatives",
        json={
            "name": "A/B text",
            "body": template.body,
            "template_id": template.id,
            "status": "active",
        },
    )
    assert response.status_code == 201, response.text
    creative = response.json()
    assert creative["template_id"] == template.id
    assert creative["body"] == template.body

    # Serialization everywhere carries the binding (list + detail use it).
    listing = await client.get(f"/api/v1/ads/sets/{ads_set.id}/creatives")
    assert {c["id"]: c["template_id"] for c in listing.json()["items"]} == {creative["id"]: template.id}


@pytest.mark.asyncio
async def test_create_creative_rejects_unknown_template(client, db):
    ads_set, _ = await _scaffold(db)
    response = await client.post(
        f"/api/v1/ads/sets/{ads_set.id}/creatives",
        json={"name": "X", "body": "hello", "template_id": 999999},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_can_bind_unbind_and_rebind_template(client, db):
    ads_set, template = await _scaffold(db)
    response = await client.post(
        f"/api/v1/ads/sets/{ads_set.id}/creatives",
        json={"name": "Plain", "body": "typed by hand"},
    )
    creative_id = response.json()["id"]

    # Bind it to the template.
    bound = await client.patch(
        f"/api/v1/ads/creatives/{creative_id}", json={"template_id": template.id}
    )
    assert bound.status_code == 200
    assert bound.json()["template_id"] == template.id

    # Editing the text keeps the binding (the composer still knows the source).
    edited = await client.patch(
        f"/api/v1/ads/creatives/{creative_id}",
        json={"body": "Hi {{first_name}}, edited copy"},
    )
    assert edited.json()["template_id"] == template.id
    assert edited.json()["body"] == "Hi {{first_name}}, edited copy"
    assert edited.json()["current_version"] == 2

    # Reject a template id that does not exist.
    bad = await client.patch(f"/api/v1/ads/creatives/{creative_id}", json={"template_id": 424242})
    assert bad.status_code == 404

    # Unbind explicitly.
    unbound = await client.patch(f"/api/v1/ads/creatives/{creative_id}", json={"template_id": None})
    assert unbound.status_code == 200
    assert unbound.json()["template_id"] is None


@pytest.mark.asyncio
async def test_duplicate_and_set_clone_carry_template_binding(client, db):
    ads_set, template = await _scaffold(db)
    created = await client.post(
        f"/api/v1/ads/sets/{ads_set.id}/creatives",
        json={"name": "Original", "body": template.body, "template_id": template.id},
    )
    creative_id = created.json()["id"]

    duplicate = await client.post(f"/api/v1/ads/creatives/{creative_id}/duplicate")
    assert duplicate.status_code == 201
    assert duplicate.json()["template_id"] == template.id

    # A duplicated set clones its creatives (the original plus the copy above,
    # both bound to the same template).
    copied = await client.post(f"/api/v1/ads/sets/{ads_set.id}/duplicate")
    assert copied.status_code == 201
    cloned = (
        await db.execute(select(AdsCreative).where(AdsCreative.set_id == copied.json()["id"]))
    ).scalars().all()
    assert len(cloned) == 2
    assert all(creative.template_id == template.id for creative in cloned)
