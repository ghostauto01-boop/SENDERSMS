"""Run one campaign batch inline, exactly as the web-mode fallback does.

The simulator has no Redis/Celery broker, so ``POST /campaigns/{id}/start``
correctly refuses (503) rather than stranding the campaign in "running". This
script performs the same two steps the running worker performs — start the
campaign, then process one batch through ``process_campaign_batch_async`` with
``send_inline=True`` — so the email a campaign would really deliver is exercised.

Usage: python tools/run_campaign_now.py <campaign_id>
"""
import asyncio
import os
import sys

# Load the app's own .env *first*: this process shares the database with the
# server under test, so it has to share its CREDENTIAL_ENCRYPTION_KEY too.
# Otherwise the API keys the server encrypted are unreadable here, and every
# send fails with a message about a missing Brevo key.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/sim.db")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("SECRET_KEY", "sim-secret-key-not-for-production")
os.environ.setdefault(
    "CREDENTIAL_ENCRYPTION_KEY", "JXfG0R5AHCO_Ib0mVG-1NxBwUJzZKrM4LBS9hCVvKmg="
)
os.environ.setdefault("BREVO_API_BASE", "http://127.0.0.1:8799/v3")
os.environ.setdefault("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import app.models  # noqa: F401,E402  (register every table)
from app.database import async_session_factory  # noqa: E402
from app.services.campaign_service import CampaignService  # noqa: E402
from app.tasks.campaign_tasks import process_campaign_batch_async  # noqa: E402


async def main(campaign_id: int) -> None:
    async with async_session_factory() as db:
        campaign = await CampaignService(db).start_campaign(campaign_id)
        current = campaign.status
        await db.commit()
    print(f"campaign {campaign_id} is now {current}")

    processed = await process_campaign_batch_async(campaign_id, send_inline=True)
    print(f"processed {processed} contact(s) inline")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
