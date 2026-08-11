"""Regression cover for the campaign-start race condition.

Bug: POST /campaigns/{id}/start enqueued the Celery task while the request's
transaction was still open. The worker is a *separate process* on its own
connection, so it could read the campaign before the CampaignContact rows and
the "running" status were committed. It then found nothing to do and exited
successfully, leaving the campaign stuck at 0 sent -- with no error anywhere.
Reproduced live: 4/4 contacts stayed "pending" until the task was re-triggered
by hand.

Fix: commit before enqueuing.

These tests use a *file-backed* sqlite DB on purpose. An in-memory sqlite with
StaticPool shares a single connection, which would make uncommitted rows
visible to the "worker" session and silently pass a broken implementation.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models.campaign import Campaign, CampaignContact
from app.models.contact import Contact
from app.models.contact_list import ContactList, ContactListMember
from app.models.template import Template
from app.models.user import User
from app.security.auth import get_current_user


@pytest_asyncio.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/race.db")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def seeded(factory):
    """A scheduled campaign with one list of three contacts."""
    async with factory() as db:
        tmpl = Template(name="T", body="Hi {{first_name}}")
        clist = ContactList(name="L")
        db.add_all([tmpl, clist])
        await db.flush()
        for i in range(3):
            c = Contact(
                phone_number=f"+23480311122{i}{i}",
                first_name=f"C{i}",
                country="NG",
                lead_status="new",
                consent_status="unknown",
                has_consented=True,
                is_opted_out=False,
            )
            db.add(c)
            await db.flush()
            db.add(ContactListMember(list_id=clist.id, contact_id=c.id))
        camp = Campaign(
            name="Race",
            status="scheduled",
            list_id=clist.id,
            template_id=tmpl.id,
        )
        db.add(camp)
        await db.commit()
        return camp.id


@pytest_asyncio.fixture
async def client(factory, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "SMSGATE_BASE_URL", "https://api.sms-gate.app/3rdparty/v1")
    monkeypatch.setattr(app_settings, "SMSGATE_USERNAME", "u")
    monkeypatch.setattr(app_settings, "SMSGATE_PASSWORD", "p")

    async def _get_db():
        async with factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = lambda: User(
        id=1, username="t", email="t@e.com", password_hash="x", role="admin", is_active=True
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


class TestStartCommitsBeforeEnqueue:
    @pytest.mark.asyncio
    async def test_worker_sees_contacts_when_task_is_dispatched(
        self, client, factory, seeded, monkeypatch
    ):
        """The heart of the bug: read the DB the way the worker does.

        The stub runs at the exact moment the task is dispatched and uses a
        brand-new session/connection, so it only observes committed state.
        """
        seen = {}

        def fake_enqueue(task, *args, **kwargs):
            import asyncio

            async def _peek():
                async with factory() as db:
                    rows = (
                        await db.execute(
                            select(CampaignContact).where(
                                CampaignContact.campaign_id == seeded
                            )
                        )
                    ).scalars().all()
                    camp = (
                        await db.execute(select(Campaign).where(Campaign.id == seeded))
                    ).scalar_one()
                    seen["contacts"] = len(rows)
                    seen["status"] = camp.status

            # We are inside a running loop (ASGI); use a worker thread so the
            # nested await can complete on its own loop.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                pool.submit(lambda: asyncio.run(_peek())).result()

        monkeypatch.setattr("app.tasks.queue.enqueue", fake_enqueue)

        r = await client.post(f"/api/v1/campaigns/{seeded}/start")
        assert r.status_code == 200, r.text

        assert seen["contacts"] == 3, (
            "worker would see %r committed contacts at dispatch time -- "
            "the campaign would silently send nothing" % seen.get("contacts")
        )
        assert seen["status"] == "running"

    @pytest.mark.asyncio
    async def test_broker_failure_persists_the_revert(
        self, client, factory, seeded, monkeypatch
    ):
        """A 503 must leave the campaign durably back at 'scheduled'.

        Since start now commits first, the revert has to be committed too --
        otherwise the campaign stays 'running' on disk with nothing to drive it.
        """
        from app.tasks.queue import QueueUnavailable

        def boom(*a, **k):
            raise QueueUnavailable("broker down")

        monkeypatch.setattr("app.tasks.queue.enqueue", boom)

        r = await client.post(f"/api/v1/campaigns/{seeded}/start")
        assert r.status_code == 503

        async with factory() as db:
            camp = (
                await db.execute(select(Campaign).where(Campaign.id == seeded))
            ).scalar_one()
            assert camp.status == "scheduled"
            assert camp.started_at is None
