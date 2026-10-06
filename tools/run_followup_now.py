"""Send one pending follow-up inline, the way the worker would.

The simulator has no Redis, so ``POST /followups/{id}/send-now`` correctly
refuses (503) rather than queueing into nothing. This runs the same function the
worker task calls, with the web-mode ``send_inline`` fallback, so an email
follow-up is really delivered and can be checked on the wire.

Usage: python tools/run_followup_now.py <followup_id>
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

import app.models  # noqa: F401,E402
from app.tasks.campaign_tasks import process_followup_async  # noqa: E402


async def main(followup_id: int) -> None:
    sent = await process_followup_async(followup_id, send_inline=True)
    print(f"follow-up {followup_id} sent: {sent}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
