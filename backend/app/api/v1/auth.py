"""
Authentication API routes.

There is no login wall: the screen is a single "Log in as admin" button and
``POST /api/v1/auth/admin`` hands out a session for the operator account with
no credentials at all. ``POST /api/v1/auth/login`` is kept for the older
clients and also treats an empty password as the same one-tap sign-in.
"""

import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserOut
from app.security.auth import (
    apply_session_cookie,
    create_access_token,
    ensure_admin,
    get_current_user,
    hash_password,
    verify_password,
)
from app.config import settings
from app.security.rate_limit import limiter

router = APIRouter()


async def _authenticate(db: AsyncSession, username: str | None, password: str | None):
    """Return the User for a successful login, or None.

    Accepts, in order:
    0. No password at all — the one-tap "Log in as admin" button. The operator
       account is signed in directly; nothing is verified because the login
       wall was removed on purpose.
    1. A named account (usually ``admin``). The admin row's stored hash is
       kept in sync with the ADMIN_PASSWORD environment variable, so the
       credentials set on Render always work even after they are changed.
    2. The optional site password saved in Settings → Site access (any
       username is accepted with it).
    3. The legacy LOGIN_PASSWORD environment fallback.

    ``username`` may be empty for the interim password-only clients; it then
    defaults to the admin account.
    """
    candidate = (password or "").strip()
    if not candidate:
        # 0) One-tap sign-in: no password means the operator account.
        return await ensure_admin(db)

    uname = (username or "").strip() or settings.ADMIN_USERNAME

    # The operator account is created on first use and kept in sync with the
    # ADMIN_PASSWORD environment variable (see ensure_admin).
    if uname == settings.ADMIN_USERNAME:
        user = await ensure_admin(db)
    else:
        result = await db.execute(select(User).where(User.username == uname))
        user = result.scalar_one_or_none()

    # 1) Named account (admin first — the operator account).
    if user is not None and user.is_active:
        if verify_password(candidate, user.password_hash):
            return user
        if (
            user.username == settings.ADMIN_USERNAME
            and hmac.compare_digest(
                candidate.encode(), settings.ADMIN_PASSWORD.encode()
            )
        ):
            # The environment password changed after the row was created —
            # the environment wins, so store the new hash and sign in.
            user.password_hash = hash_password(candidate)
            await db.flush()
            return user

    # 2) Optional site password (Settings → Site access).
    from app.services.system_settings import get_site_password_hash

    stored = await get_site_password_hash(db)
    if stored and verify_password(candidate, stored):
        return await ensure_admin(db)

    # 3) Legacy LOGIN_PASSWORD fallback (password-only interim login).
    if hmac.compare_digest(candidate.encode(), settings.LOGIN_PASSWORD.encode()):
        return await ensure_admin(db)

    return None


async def _sign_in(db: AsyncSession, user: User) -> Response:
    """Stamp the session cookie and build the login response for ``user``."""
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

    # Return the Response object WITH the cookie set
    resp = Response(
        content=response.model_dump_json(),
        media_type="application/json",
    )
    apply_session_cookie(resp, token)
    return resp


@router.post("/admin", response_model=LoginResponse)
async def login_as_admin(db: AsyncSession = Depends(get_db)):
    """One-tap sign-in. No username, no password — just the button.

    Creates (or reuses) the operator account and hands back its session
    cookie. The account and every row it owns already exist, so this changes
    nothing in the database except ``last_login``.
    """
    user = await ensure_admin(db)
    return await _sign_in(db, user)


@router.post("/login", response_model=LoginResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login(request: Request, data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Sign in. An empty body is the one-tap "Log in as admin" path.

    A password, when one is still sent by an older client, is checked against
    ADMIN_PASSWORD (environment), the optional site password from
    Settings → Site access, or the legacy LOGIN_PASSWORD — in that order.
    """
    user = await _authenticate(db, data.username, data.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    return await _sign_in(db, user)


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
    """Public. Tells the UI whether a password is needed to get in.

    It never is: the login wall was removed, so this always reports the door
    as open. ``password_set`` still reflects the optional extra password from
    Settings → Site access, which remains a second way in but gates nothing.
    """
    from app.services.system_settings import get_site_password_hash

    return {
        "password_required": False,
        "password_set": bool(await get_site_password_hash(db)),
    }


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return current_user
