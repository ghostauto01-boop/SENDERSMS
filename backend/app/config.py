"""
Application configuration using pydantic-settings.
Loads from environment variables with sensible defaults.
"""

import os
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Application ---
    APP_NAME: str = "SMS SENDER"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DEFAULT_TIMEZONE: str = "Africa/Lagos"

    # --- Security ---
    SECRET_KEY: str = "change-me-to-a-random-secret-key-at-least-32-chars"
    CREDENTIAL_ENCRYPTION_KEY: str = "change-me-to-a-32-byte-base64-encoded-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://sendsms:sendsms@localhost:5432/sendsms"

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- CORS ---
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # --- Bootstrap Admin ---
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"

    # --- SMS Gateway (SMS-Gate.app) ---
    SMSGATE_BASE_URL: Optional[str] = "https://api.sms-gate.app/3rdparty/v1"
    SMSGATE_USERNAME: Optional[str] = "_O48UB"
    SMSGATE_PASSWORD: Optional[str] = "nw_e7wyhwjwubp"
    SMSGATE_WEBHOOK_SECRET: Optional[str] = None
    SMSGATE_TIMEOUT: int = 30
    SMSGATE_RETRY_COUNT: int = 3
    SMSGATE_POLL_INTERVAL: int = 60

    # --- OneSignal ---
    ONESIGNAL_APP_ID: Optional[str] = None
    ONESIGNAL_REST_API_KEY: Optional[str] = None

    # --- Pushover ---
    PUSHOVER_APP_TOKEN: Optional[str] = None
    PUSHOVER_USER_KEY: Optional[str] = None

    # --- Rate Limiting ---
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_API: str = "60/minute"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


settings = Settings()
