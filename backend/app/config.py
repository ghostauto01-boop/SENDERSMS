"""
Application configuration using pydantic-settings.
Loads from environment variables with sensible defaults.
"""

import logging
import os
from typing import Optional

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# Placeholder values that must never survive into a production deployment.
INSECURE_SECRET_KEY = "change-me-to-a-random-secret-key-at-least-32-chars"
INSECURE_ENCRYPTION_KEY = "change-me-to-a-32-byte-base64-encoded-key"
INSECURE_ADMIN_PASSWORD = "admin"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # --- Application ---
    APP_NAME: str = "SMS SENDER"
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    DEFAULT_TIMEZONE: str = "Africa/Lagos"

    # --- Security ---
    SECRET_KEY: str = INSECURE_SECRET_KEY
    CREDENTIAL_ENCRYPTION_KEY: str = INSECURE_ENCRYPTION_KEY
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

    # --- Public URL (used to register the gateway webhook) ---
    # Must point at THIS deployment, e.g. https://your-app.onrender.com
    PUBLIC_BASE_URL: Optional[str] = None

    # --- Bootstrap Admin ---
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = INSECURE_ADMIN_PASSWORD

    # --- SMS Gateway (SMS-Gate.app) ---
    # Credentials MUST come from the environment — never hardcode them here.
    SMSGATE_BASE_URL: Optional[str] = "https://api.sms-gate.app/3rdparty/v1"
    SMSGATE_USERNAME: Optional[str] = None
    SMSGATE_PASSWORD: Optional[str] = None
    SMSGATE_WEBHOOK_SECRET: Optional[str] = None
    # When no signing secret is configured the webhook rejects all traffic.
    # Set to True only for local debugging against an unsigned sender.
    SMSGATE_WEBHOOK_ALLOW_UNSIGNED: bool = False
    # Poll delivery statuses / send due scheduled messages from the web
    # process. Useful when no Celery worker is running; disable it if the
    # worker + beat handle this, to avoid duplicate work.
    ENABLE_INLINE_POLLER: bool = True
    INLINE_POLL_INTERVAL: int = 30
    SMSGATE_TIMEOUT: int = 30
    SMSGATE_RETRY_COUNT: int = 3
    SMSGATE_POLL_INTERVAL: int = 60

    # --- SMS Gateway 2 (Dmobili.com — Pace Bulk SMS platform) ---
    # Optional second gateway. When DMOBILI_USERNAME/PASSWORD (or API token)
    # are set and the active gateway is switched to "dmobili" in Settings,
    # all outbound SMS goes through Dmobili's HTTP API instead of SMS-Gate.
    # Their API spec is issued privately by support (no public docs), so the
    # endpoint paths are configurable to match whatever they send you.
    DMOBILI_BASE_URL: Optional[str] = "https://dmobili.com"
    DMOBILI_USERNAME: Optional[str] = None
    DMOBILI_PASSWORD: Optional[str] = None
    # Some Pace-platform accounts authenticate with a standalone API token
    # instead of username/password. Used as an `apikey` parameter when set.
    DMOBILI_API_TOKEN: Optional[str] = None
    # Sender ID registered on the Dmobili account (required for their routes).
    DMOBILI_SENDER_ID: Optional[str] = None
    # Send endpoint relative to DMOBILI_BASE_URL. Default matches the common
    # Pace Bulk SMS HTTP API layout; adjust to the docs Dmobili issues.
    DMOBILI_SEND_PATH: str = "api/sms/index.php"
    # Optional balance endpoint (e.g. "api/sms/balance.php"). When set, the
    # connection test verifies credentials against it; without it the test
    # only checks that the gateway is reachable.
    DMOBILI_BALANCE_PATH: Optional[str] = None
    # Optional delivery-report polling endpoint. Without it, delivery states
    # come exclusively from DLR callbacks (webhooks).
    DMOBILI_REPORT_PATH: Optional[str] = None
    # Optional route name (their Alpha / Premium / OTP routes), passed as-is.
    DMOBILI_ROUTE: Optional[str] = None
    # Shared secret protecting the inbound/DLR callback endpoint. Dmobili
    # posts it back as `secret` (or X-Dmobili-Secret header). Required in
    # production; mirrors the smsgate signing-secret posture.
    DMOBILI_WEBHOOK_SECRET: Optional[str] = None
    DMOBILI_WEBHOOK_ALLOW_UNSIGNED: bool = False
    DMOBILI_TIMEOUT: int = 30

    @property
    def dmobili_configured(self) -> bool:
        """True when Dmobili has usable credentials."""
        return bool(
            self.DMOBILI_BASE_URL
            and self.DMOBILI_USERNAME
            and self.DMOBILI_PASSWORD
        ) or bool(self.DMOBILI_BASE_URL and self.DMOBILI_API_TOKEN)

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

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in ("production", "prod")

    @property
    def smsgate_configured(self) -> bool:
        """True when the SMS gateway has usable credentials."""
        return bool(
            self.SMSGATE_BASE_URL and self.SMSGATE_USERNAME and self.SMSGATE_PASSWORD
        )

    def insecure_defaults(self) -> list[str]:
        """Names of settings still holding an unsafe placeholder value."""
        problems: list[str] = []
        if self.SECRET_KEY == INSECURE_SECRET_KEY:
            problems.append("SECRET_KEY")
        if self.CREDENTIAL_ENCRYPTION_KEY == INSECURE_ENCRYPTION_KEY:
            problems.append("CREDENTIAL_ENCRYPTION_KEY")
        if self.ADMIN_PASSWORD == INSECURE_ADMIN_PASSWORD:
            problems.append("ADMIN_PASSWORD")
        return problems

    def validate_runtime(self) -> None:
        """Refuse to boot in production with placeholder secrets."""
        problems = self.insecure_defaults()
        if not problems:
            return
        joined = ", ".join(problems)
        if self.is_production:
            raise RuntimeError(
                f"Refusing to start in production with default values for: {joined}. "
                "Set them to strong, unique values in the environment."
            )
        logger.warning(
            "INSECURE DEFAULTS in use for: %s. This is tolerated because APP_ENV=%s, "
            "but must be fixed before deploying.",
            joined,
            self.APP_ENV,
        )


settings = Settings()
settings.validate_runtime()
