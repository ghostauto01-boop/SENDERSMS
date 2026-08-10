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
from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
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


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Dependency that extracts the current user from the session cookie."""
    token = request.cookies.get("sendsms_session")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


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
