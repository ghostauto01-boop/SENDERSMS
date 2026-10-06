"""Setup guide API — the in-app tutorial.

``GET /api/v1/guide`` returns the whole checklist with live status read from the
database and the environment; ``GET /api/v1/guide/summary`` returns just the
progress numbers for the sidebar badge.

One side effect, deliberately: building the guide creates a sender's Brevo
webhook token if it does not have one yet, because the URL is useless without it
and the point of the step is to hand the operator something to paste. It is
idempotent, and it is the same token the Senders screen uses.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.security.auth import get_current_user
from app.services import setup_guide

router = APIRouter()


@router.get("")
@router.get("/")
async def guide(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Every setup step, grouped, with what the app can currently see."""
    return await setup_guide.build_guide(db)


@router.get("/summary")
async def guide_summary(
    db: AsyncSession = Depends(get_db),
    cu: User = Depends(get_current_user),
):
    """Progress only — cheap enough to fetch on every page load for the badge."""
    return await setup_guide.summary(db)
