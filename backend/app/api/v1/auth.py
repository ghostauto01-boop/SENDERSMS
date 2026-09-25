"""
Authentication API routes.
"""

import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.security.auth import (
    apply_session_cookie,
    create_access_token,
    ensure_admin,
    get_current_user,
    verify_password,
)
from app.config import settings
from app.security.rate_limit import limiter

router = APIRouter()


async def _password_matches(db: AsyncSession, plain: str) -> bool:
    """Check the password saved in Settings, or the env login password if none is saved."""
    from app.services.system_settings import get_site_password_hash

    candidate = (plain or "").strip()
    if not candidate:
        return False
    stored = await get_site_password_hash(db)
    if stored:
        return verify_password(candidate, stored)
    return hmac.compare_digest(candidate.encode(), settings.LOGIN_PASSWORD.encode())


@router.post("/login", response_model=LoginResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login(request: Request, data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Password-only login.

    The app has a single operator. Username is ignored: the password is
    checked against settings.LOGIN_PASSWORD (default "12345678") and the
    session is issued for the admin account, which is created on first use.
    Previously the admin row was only created once, so changing
    ADMIN_PASSWORD in the environment never updated the stored hash and the
    real password was rejected as "invalid".
    """
    if not await _password_matches(db, data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password",
        )

    user = await ensure_admin(db)
    if not user.is_active:
        user.is_active = True

    # Update last login
    user.last_login = datetime.now(timezone.utc)
    await db.flush()

    # Create token
    token = create_access_token(data={"sub": str(user.id), "role": user.role})

    response = LoginResponse(
        success=True,
        user_id=user.id,
        username=user.username,
        role=user.role,
        message="Login successful",
    )

    # Set HTTP-only cookie
    resp = Response(
        content=response.model_dump_json(),
        media_type="application/json",
    )
    apply_session_cookie(resp, token)

    # Return the Response object WITH the cookie set
    return resp


@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """Logout user by clearing session cookie."""
    resp = Response(content='{"success":true,"message":"Logged out"}', media_type="application/json")
    resp.delete_cookie(
        key="sendsms_session",
        path="/",
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
    )
    return resp


@router.get("/access")
async def access_status(db: AsyncSession = Depends(get_db)):
    """Public. Tells the UI whether the password wall is on.

    Defaults to off, so opening the site address is enough.
    """
    from app.services.system_settings import is_site_password_required

    return {"password_required": await is_site_password_required(db)}


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return current_user
