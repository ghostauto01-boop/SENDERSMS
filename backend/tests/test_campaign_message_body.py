"""Campaigns can carry their own message text instead of a saved template.

Also pins the precedence rules, because getting these wrong means sending the
wrong text to real phone numbers.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.campaign import Campaign
from app.models.template import Template
from app.services.campaign_service import CampaignService


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class TestResolveBody:
    @pytest.mark.asyncio
    async def test_inline_message_is_used(self, db):
        campaign = Campaign(name="C", status="draft", message_body="Hi {{first_name}}")
        db.add(campaign)
        await db.flush()

        assert await CampaignService(db).resolve_body(campaign) == "Hi {{first_name}}"

    @pytest.mark.asyncio
    async def test_template_is_used_when_no_inline_message(self, db):
        tpl = Template(name="T", body="From template")
        db.add(tpl)
        await db.flush()
        campaign = Campaign(name="C", status="draft", template_id=tpl.id)
        db.add(campaign)
        await db.flush()

        assert await CampaignService(db).resolve_body(campaign) == "From template"

    @pytest.mark.asyncio
    async def test_inline_message_beats_template(self, db):
        """If both are somehow set, the text the user typed wins."""
        tpl = Template(name="T", body="From template")
        db.add(tpl)
        await db.flush()
        campaign = Campaign(
            name="C", status="draft", template_id=tpl.id, message_body="Inline wins"
        )
        db.add(campaign)
        await db.flush()

        assert await CampaignService(db).resolve_body(campaign) == "Inline wins"

    @pytest.mark.asyncio
    async def test_sequence_step_template_beats_both(self, db):
        """An explicit step template overrides the campaign-level message."""
        step_tpl = Template(name="Step", body="Step body")
        db.add(step_tpl)
        await db.flush()
        campaign = Campaign(name="C", status="draft", message_body="Campaign body")
        db.add(campaign)
        await db.flush()

        body = await CampaignService(db).resolve_body(campaign, template_id=step_tpl.id)
        assert body == "Step body"

    @pytest.mark.asyncio
    async def test_no_message_returns_none(self, db):
        campaign = Campaign(name="C", status="draft")
        db.add(campaign)
        await db.flush()

        assert await CampaignService(db).resolve_body(campaign) is None

    @pytest.mark.asyncio
    async def test_whitespace_only_message_is_not_a_message(self, db):
        campaign = Campaign(name="C", status="draft", message_body="   \n  ")
        db.add(campaign)
        await db.flush()

        assert await CampaignService(db).resolve_body(campaign) is None

    @pytest.mark.asyncio
    async def test_empty_template_body_falls_back_to_none(self, db):
        tpl = Template(name="T", body="")
        db.add(tpl)
        await db.flush()
        campaign = Campaign(name="C", status="draft", template_id=tpl.id)
        db.add(campaign)
        await db.flush()

        assert await CampaignService(db).resolve_body(campaign) is None

    @pytest.mark.asyncio
    async def test_deleted_template_does_not_crash(self, db):
        campaign = Campaign(name="C", status="draft", template_id=999999)
        db.add(campaign)
        await db.flush()

        assert await CampaignService(db).resolve_body(campaign) is None


class TestCreateCampaignPersistsMessage:
    @pytest.mark.asyncio
    async def test_create_stores_inline_message(self, db):
        campaign = await CampaignService(db).create_campaign(
            {"name": "C", "message_body": "Hello there"}
        )
        assert campaign.message_body == "Hello there"

    @pytest.mark.asyncio
    async def test_blank_message_stored_as_null(self, db):
        """'' and '   ' must not count as a message, or validation would pass."""
        campaign = await CampaignService(db).create_campaign(
            {"name": "C", "message_body": "   "}
        )
        assert campaign.message_body is None
