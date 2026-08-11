"""
Authentication API routes.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.security.auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
)
from app.config import settings
from app.security.rate_limit import limiter

router = APIRouter()


async def _bootstrap_admin(db: AsyncSession):
    """Create admin user from environment variables if it doesn't exist."""
    result = await db.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
    admin = result.scalar_one_or_none()

    if not admin:
        admin = User(
            username=settings.ADMIN_USERNAME,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            display_name="Administrator",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        await db.flush()

    return admin


@router.post("/login", response_model=LoginResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login(request: Request, data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate user and set session cookie."""
    # Bootstrap admin if needed
    await _bootstrap_admin(db)

    result = await db.execute(select(User).where(User.username == data.username))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

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
    resp.set_cookie(
        key="sendsms_session",
        value=token,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

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


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return current_user
