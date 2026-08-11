"""Tests for campaign gateway validation.

Regression cover for the blocker where every campaign failed validation with
"No SMS gateway selected", because gateway_setting_id was mandatory but no
code path ever created a GatewaySetting row.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base
from app.models.campaign import Campaign
from app.models.gateway import GatewaySetting
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


@pytest.fixture
def env_gateway(monkeypatch):
    """Credentials present in the environment (the normal deployment case)."""
    monkeypatch.setattr(settings, "SMSGATE_BASE_URL", "https://api.sms-gate.app/3rdparty/v1")
    monkeypatch.setattr(settings, "SMSGATE_USERNAME", "user")
    monkeypatch.setattr(settings, "SMSGATE_PASSWORD", "pass")


@pytest.fixture
def no_env_gateway(monkeypatch):
    monkeypatch.setattr(settings, "SMSGATE_USERNAME", None)
    monkeypatch.setattr(settings, "SMSGATE_PASSWORD", None)


class TestGatewayCheck:
    @pytest.mark.asyncio
    async def test_env_gateway_satisfies_validation(self, db, env_gateway):
        """The core fix: no gateway_setting_id needed when env is configured."""
        campaign = Campaign(name="C", status="draft")
        db.add(campaign)
        await db.flush()
        assert await CampaignService(db)._check_gateway(campaign) is None

    @pytest.mark.asyncio
    async def test_missing_gateway_reports_actionable_error(self, db, no_env_gateway):
        campaign = Campaign(name="C", status="draft")
        db.add(campaign)
        await db.flush()
        error = await CampaignService(db)._check_gateway(campaign)
        assert error is not None
        assert "SMSGATE_USERNAME" in error

    @pytest.mark.asyncio
    async def test_explicit_enabled_gateway_accepted(self, db, no_env_gateway):
        gw = GatewaySetting(name="Phone", is_enabled=True, username="u")
        db.add(gw)
        await db.flush()
        campaign = Campaign(name="C", status="draft", gateway_setting_id=gw.id)
        db.add(campaign)
        await db.flush()
        assert await CampaignService(db)._check_gateway(campaign) is None

    @pytest.mark.asyncio
    async def test_disabled_gateway_rejected(self, db, env_gateway):
        gw = GatewaySetting(name="Phone", is_enabled=False)
        db.add(gw)
        await db.flush()
        campaign = Campaign(name="C", status="draft", gateway_setting_id=gw.id)
        db.add(campaign)
        await db.flush()
        error = await CampaignService(db)._check_gateway(campaign)
        assert error is not None and "disabled" in error

    @pytest.mark.asyncio
    async def test_dangling_gateway_reference_rejected(self, db, env_gateway):
        campaign = Campaign(name="C", status="draft", gateway_setting_id=9999)
        db.add(campaign)
        await db.flush()
        error = await CampaignService(db)._check_gateway(campaign)
        assert error is not None and "no longer exists" in error


class TestValidateAndSchedule:
    @pytest.mark.asyncio
    async def test_campaign_with_contacts_reaches_scheduled(self, db, env_gateway):
        """End-to-end: a well-formed campaign can now actually be scheduled."""
        from app.models.contact import Contact
        from app.models.contact_list import ContactList, ContactListMember

        lst = ContactList(name="L")
        db.add(lst)
        await db.flush()

        contact = Contact(phone_number="+2348031234567")
        db.add(contact)
        await db.flush()

        db.add(ContactListMember(list_id=lst.id, contact_id=contact.id))
        campaign = Campaign(name="C", status="draft", list_id=lst.id)
        db.add(campaign)
        await db.flush()

        result = await CampaignService(db).validate_and_schedule(campaign.id)
        assert result.status == "scheduled"

    @pytest.mark.asyncio
    async def test_empty_list_still_rejected(self, db, env_gateway):
        from app.models.contact_list import ContactList

        lst = ContactList(name="L")
        db.add(lst)
        await db.flush()
        campaign = Campaign(name="C", status="draft", list_id=lst.id)
        db.add(campaign)
        await db.flush()

        with pytest.raises(ValueError, match="empty"):
            await CampaignService(db).validate_and_schedule(campaign.id)
