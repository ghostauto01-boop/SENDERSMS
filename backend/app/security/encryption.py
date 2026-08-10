"""
Encryption utilities for securing credentials at rest.
Uses Fernet symmetric encryption.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import settings


def _get_fernet() -> Fernet:
    """Create a Fernet instance from the configured encryption key."""
    key = settings.CREDENTIAL_ENCRYPTION_KEY

    # If the key looks like a raw string (not base64 Fernet key), derive one
    try:
        decoded = base64.urlsafe_b64decode(key.encode() + b"===")
        if len(decoded) == 32:
            return Fernet(base64.urlsafe_b64encode(decoded))
    except Exception:
        pass

    # Derive a Fernet key from the raw key using PBKDF2
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"sendsms-cred-salt",
        iterations=480000,
    )
    derived = kdf.derive(key.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_value(value: str) -> str:
    """Encrypt a string value."""
    if not value:
        return ""
    f = _get_fernet()
    return f.encrypt(value.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    """Decrypt an encrypted string value."""
    if not encrypted:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(encrypted.encode()).decode()
    except Exception:
        return ""


def mask_value(value: str, show_chars: int = 4) -> str:
    """Mask a sensitive value for display, showing only the last few characters."""
    if not value:
        return ""
    if len(value) <= show_chars:
        return "*" * len(value)
    return "*" * (len(value) - show_chars) + value[-show_chars:]
