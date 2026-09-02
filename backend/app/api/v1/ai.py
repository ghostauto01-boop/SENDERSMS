"""Keyless AI endpoints.

Exposes the built-in sentiment/intent engine so the UI can show a live
"what would the AI say about this reply?" tester, and templates can reference
the classification without any external API.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.security.auth import get_current_user

router = APIRouter()


class ClassifyRequest(BaseModel):
    text: str


@router.post("/classify")
async def classify_text(
    payload: ClassifyRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Classify arbitrary text with the built-in, keyless AI engine."""
    from app.services.ai_classifier import classify
    return classify(payload.text)
