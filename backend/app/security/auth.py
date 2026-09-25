"""
Authentication and authorization utilities.
Uses Argon2id for password hashing, JWT for sessions.
"""

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

# Argon2id hasher with secure defaults
ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a password using Argon2id."""
    return ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its Argon2id hash."""
    try:
        return ph.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError):
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def apply_session_cookie(response: Response, token: str) -> None:
    """Attach the HTTP-only session cookie used by the login wall."""
    response.set_cookie(
        key="sendsms_session",
        value=token,
        httponly=True,
        secure=settings.APP_ENV == "production",
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )


async def ensure_admin(db: AsyncSession) -> User:
    """Return the operator account, creating it on first use."""
    result = await db.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
    admin = result.scalar_one_or_none()
    if admin is None:
        admin = User(
            username=settings.ADMIN_USERNAME,
            password_hash=hash_password(settings.ADMIN_PASSWORD),
            display_name="Administrator",
            role="admin",
            is_active=True,
        )
        db.add(admin)
        try:
            await db.flush()
        except IntegrityError:
            # Parallel first requests can race on the unique username.
            await db.rollback()
            result = await db.execute(select(User).where(User.username == settings.ADMIN_USERNAME))
            admin = result.scalar_one_or_none()
            if admin is None:
                raise
    if admin is not None and not admin.is_active:
        admin.is_active = True
        await db.flush()
    return admin


async def _user_from_session(request: Request, db: AsyncSession) -> Optional[User]:
    token = request.cookies.get("sendsms_session")
    if not token:
        return None
    payload = decode_access_token(token)
    if payload is None:
        return None
    user_id = payload.get("sub")
    if user_id is None:
        return None
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Current operator.

    A valid session cookie always wins. When the site password wall is off
    (the default — turn it on later in Settings → Site access), anyone who
    can reach the URL is treated as the operator. No cookie, no password.
    """
    user = await _user_from_session(request, db)
    if user is not None:
        return user

    # Imported here to avoid a cycle with system_settings → hash_password.
    from app.services.system_settings import is_site_password_required

    if not await is_site_password_required(db):
        return await ensure_admin(db)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


def generate_csrf_token(secret: str) -> str:
    """Generate a simple CSRF token."""
    timestamp = str(int(time.time()))
    msg = f"{timestamp}:csrf"
    signature = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}:{signature}"


def verify_csrf_token(token: str, secret: str) -> bool:
    """Verify a CSRF token (valid for 1 hour)."""
    try:
        parts = token.split(":")
        if len(parts) != 2:
            return False
        ts = int(parts[0])
        if abs(time.time() - ts) > 3600:
            return False
        msg = f"{ts}:csrf"
        expected = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(parts[1], expected)
    except (ValueError, IndexError):
        return False
