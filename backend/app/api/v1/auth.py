"""
Authentication API routes.

The operator password wall is back. The login screen asks for one password and
``POST /api/v1/auth/admin`` checks it against, in order:

1. ``ADMIN_PASSWORD`` (default ``12345678``) — the operator password, and the
   one the setup guide tells you to change;
2. the optional site password saved in Settings → Site access;
3. the legacy ``LOGIN_PASSWORD`` environment value.

``POST /api/v1/auth/login`` does the same with an optional username, so older
clients keep working. A request that presents *no* password is rejected with a
401 the UI can explain — it is only accepted while every one of those is unset,
which is the deliberate "this deployment wants no password" case.
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


async def site_password_hash(db: AsyncSession) -> str | None:
    """The optional extra password from Settings → Site access, if set."""
    from app.services.system_settings import get_site_password_hash

    return await get_site_password_hash(db)


def _explicitly_set(name: str) -> bool:
    """Was this setting given in the environment/.env, rather than defaulted?

    This is what keeps ``LOGIN_PASSWORD`` from quietly undermining a rotated
    ``ADMIN_PASSWORD``: both default to ``12345678``, so if the legacy value is
    accepted just because it exists, changing ADMIN_PASSWORD would leave the
    shipped default working forever.
    """
    return name in getattr(settings, "model_fields_set", set())


async def configured_passwords(db: AsyncSession) -> list[str]:
    """Every password this deployment will accept, in priority order.

    ``ADMIN_PASSWORD`` is the operator password — it is always accepted, and
    the environment is its source of truth, so rotating it in the deployment
    (Render, Docker, .env) takes effect on the next sign-in instead of being
    frozen into the row that was created first.

    ``LOGIN_PASSWORD`` is the older password-only knob. It is only consulted
    when ``ADMIN_PASSWORD`` is empty, because both default to the same string:
    accepting it unconditionally would leave the shipped default working
    forever on a deployment that rotated ADMIN_PASSWORD — the exact hole that
    makes a password wall worthless.
    """
    admin = (settings.ADMIN_PASSWORD or "").strip()
    if admin:
        return [admin]
    return [(settings.LOGIN_PASSWORD or "").strip()] if (settings.LOGIN_PASSWORD or "").strip() else []


async def password_required(db: AsyncSession) -> bool:
    """True when this deployment wants a password at all.

    The UI reads this to decide whether to show the password field. It is False
    only when the operator has deliberately emptied both the environment
    passwords *and* the Settings → Site access password.
    """
    return bool(await configured_passwords(db)) or bool(await site_password_hash(db))


def check_password(candidate: str, accepted: list[str]) -> bool:
    """Constant-time comparison against a list of accepted passwords."""
    ok = False
    for expected in accepted:
        # ``compare_digest`` does not short-circuit, so every entry is compared
        # and a wrong password reveals nothing about which entry was closest.
        if hmac.compare_digest(candidate.encode(), expected.encode()):
            ok = True
    return ok


async def _authenticate(db: AsyncSession, username: str | None, password: str | None):
    """Return the User for a successful login, or None.

    Accepts, in order:
    1. ``ADMIN_PASSWORD`` / ``LOGIN_PASSWORD`` (the environment's operator
       password, default ``12345678``).
    2. The optional site password saved in Settings → Site access (any username
       is accepted with it).
    3. ``None`` when nothing is configured and no password was sent — the
       deliberate "no password on this deployment" case.

    ``username`` may be empty for password-only clients; it then means the
    admin account.
    """
    candidate = (password or "").strip()
    required = await password_required(db)

    if not candidate:
        # No password presented: only acceptable when nothing is configured.
        return None if required else await ensure_admin(db)

    uname = (username or "").strip() or settings.ADMIN_USERNAME

    # 1) The operator account (created on first use, hash kept in sync with the
    #    ADMIN_PASSWORD environment variable — see ensure_admin).
    if uname == settings.ADMIN_USERNAME:
        user = await ensure_admin(db)
    else:
        result = await db.execute(select(User).where(User.username == uname))
        user = result.scalar_one_or_none()

    if check_password(candidate, await configured_passwords(db)):
        if user is not None and user.is_active:
            if user.username == settings.ADMIN_USERNAME and not verify_password(
                candidate, user.password_hash
            ):
                # The environment password changed after the row was created —
                # the environment wins, so store the new hash and sign in.
                user.password_hash = hash_password(candidate)
                await db.flush()
            return user
        return await ensure_admin(db)

    # 2) A named account's own password (extra users, if any exist).
    if user is not None and user.is_active and verify_password(candidate, user.password_hash):
        return user

    # 3) Optional site password (Settings → Site access).
    stored = await site_password_hash(db)
    if stored and verify_password(candidate, stored):
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
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login_as_admin(
    request: Request,
    data: LoginRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Sign the operator in with the app password (default ``12345678``).

    The body may be omitted only when this deployment has no password at all;
    otherwise a missing/incorrect password is a 401 whose ``detail`` the login
    screen shows verbatim.
    """
    user = await _authenticate(db, settings.ADMIN_USERNAME, (data.password if data else None))
    if user is None:
        if not await password_required(db):
            user = await ensure_admin(db)
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Wrong password. Use the app password (default 12345678).",
            )
    return await _sign_in(db, user)


@router.post("/login", response_model=LoginResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
async def login(request: Request, data: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Sign in with a password (and, optionally, a username).

    Checked against ADMIN_PASSWORD (environment), the optional site password
    from Settings → Site access, or the legacy LOGIN_PASSWORD — in that order.
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
    """Public. Tells the login screen whether to draw the password field.

    ``password_required`` is true whenever the app has a password to check —
    the default deployment does (``ADMIN_PASSWORD``, default ``12345678``).

    What it must **never** do is return the password, a hash of it, or a phrase
    that gives it away: this endpoint is unauthenticated, so "the password is
    still the default" would be the same as publishing it. ``source`` names
    which password is accepted (so the screen can say what to type) and the
    shipped-default warning lives in the Setup Guide, behind the login.
    """
    site = await site_password_hash(db)
    env_password = (settings.ADMIN_PASSWORD or "").strip()
    legacy = not env_password and bool((settings.LOGIN_PASSWORD or "").strip())
    source = "admin" if env_password else ("site" if site else ("legacy" if legacy else "none"))
    return {
        "password_required": bool(env_password or legacy or site),
        "password_set": bool(site),
        "source": source,
        "hint": (
            "Enter the admin password for this deployment."
            if source == "admin"
            else (
                "Enter the password saved in Settings → Site access."
                if source == "site"
                else ("Enter this deployment's login password." if source == "legacy" else "")
            )
        ),
    }


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Get current authenticated user."""
    return current_user
