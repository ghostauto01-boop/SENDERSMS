"""
Application configuration using pydantic-settings.
Loads from environment variables with sensible defaults.
"""

import logging
import os
from typing import Optional

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

#: Repository root — ``backend/app/config.py`` -> up three levels. Used so the
#: project's ``.env`` is found no matter which directory the server is started
#: from (uvicorn from ``backend/`` is the common local case).
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)

# Placeholder values that must never survive into a production deployment.
INSECURE_SECRET_KEY = "change-me-to-a-random-secret-key-at-least-32-chars"
INSECURE_ENCRYPTION_KEY = "change-me-to-a-32-byte-base64-encoded-key"
#: The operator password. The app ships with password sign-in enabled and this
#: default; the setup guide's first step is changing it.
INSECURE_ADMIN_PASSWORD = "12345678"
#: Kept as an alias: older code and tests refer to the placeholder by this name.
DEFAULT_ADMIN_PASSWORD = INSECURE_ADMIN_PASSWORD


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

    # Browser origins used by hosted MCP clients. Kept separate from the app-wide
    # CORS allowlist so providers can preflight only MCP routes, not every CRM API.
    MCP_CORS_ORIGINS: str = (
        "https://chatgpt.com,https://chat.openai.com,https://claude.ai"
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def mcp_cors_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.MCP_CORS_ORIGINS.split(",") if o.strip()]

    # --- Public URL (used to register the gateway webhook) ---
    # Must point at THIS deployment, e.g. https://your-app.onrender.com
    PUBLIC_BASE_URL: Optional[str] = None

    # --- Bootstrap Admin ---
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = INSECURE_ADMIN_PASSWORD
    # Password-only login (username optional). Defaults to the same password so
    # the one field on the login screen works out of the box.
    LOGIN_PASSWORD: str = INSECURE_ADMIN_PASSWORD

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
    # When nobody has used the app for INLINE_POLL_ACTIVE_WINDOW seconds and
    # nothing is due, the poller sleeps up to INLINE_IDLE_POLL_INTERVAL
    # seconds between passes (or exactly until the next scheduled job). This
    # lets a Neon free-plan database scale to zero instead of being queried
    # every 30 s around the clock and exhausting its monthly compute quota.
    INLINE_IDLE_POLL_INTERVAL: int = 15 * 60
    INLINE_POLL_ACTIVE_WINDOW: int = 10 * 60
    SMSGATE_TIMEOUT: int = 30
    SMSGATE_RETRY_COUNT: int = 3
    SMSGATE_POLL_INTERVAL: int = 60

    # --- Call Gateway (CallGate.app — Android call-control companion to SMS-Gate) ---
    # Local-server REST API on the handset: http://<device-ip>:8084/api/v1
    # Install on the SAME phone as SMS-Gate. Credentials can also be saved
    # from the app UI (Settings -> Calls), which takes precedence over env.
    CALLGATE_BASE_URL: Optional[str] = None
    CALLGATE_USERNAME: Optional[str] = None
    CALLGATE_PASSWORD: Optional[str] = None
    CALLGATE_WEBHOOK_SECRET: Optional[str] = None
    CALLGATE_TIMEOUT: int = 15
    # When the backend cannot reach the handset (same Wi-Fi only), the UI
    # falls back to direct tel: dialling on the user's own phone.
    CALLGATE_ALLOW_DIRECT_DIAL_FALLBACK: bool = True

    # --- OneSignal ---
    ONESIGNAL_APP_ID: Optional[str] = None
    ONESIGNAL_REST_API_KEY: Optional[str] = None

    # --- Pushover ---
    PUSHOVER_APP_TOKEN: Optional[str] = None
    PUSHOVER_USER_KEY: Optional[str] = None

    # --- Inbuilt browser notifications (VAPID Web Push, free forever) ---
    # Leave blank and the server generates + persists its own keypair on first
    # use. Set these only to keep the SAME keys across rebuilds started with
    # a fresh database (changing keys silently unsubscribes every device).
    VAPID_PUBLIC_KEY: Optional[str] = None
    VAPID_PRIVATE_KEY: Optional[str] = None
    VAPID_CLAIM_EMAIL: str = "admin@example.com"

    # --- MCP connectors (ChatGPT / Claude / Arena / any MCP client) ---
    # OAuth 2.1 is what ChatGPT and Claude custom connectors require; turning
    # this off leaves only the static bearer tokens (Claude Code, curl, the
    # Arena bridge) working.
    MCP_OAUTH_ENABLED: bool = True
    # True = the connector's authorization page requires the operator password.
    # False = it offers the same one-tap sign-in the app's own login wall does.
    # Turn it on for a deployment that is reachable by people other than you.
    # This app now has a password wall, so the connector's authorization page
    # asks for the same password by default. Set it to False only on a
    # deployment you are happy to have anyone sign into.
    MCP_OAUTH_REQUIRE_LOGIN: bool = True
    MCP_OAUTH_ACCESS_TOKEN_TTL_MINUTES: int = 60
    MCP_OAUTH_REFRESH_TOKEN_TTL_DAYS: int = 30

    # --- Inbound email: pulling replies out of Gmail ---
    # OAuth (a Google Cloud project) is used when configured; otherwise the
    # app signs in to IMAP with an app password. Both need a public HTTPS
    # redirect only for OAuth.
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    # How often the poller checks a connected mailbox for replies, in seconds.
    GMAIL_POLL_INTERVAL: int = 60
    # Move a reply that Gmail put in Spam back into the Inbox when it matches a
    # contact or a thread this app sent. This is the fix for "the prospect
    # replied and it landed in my spam".
    GMAIL_RESCUE_FROM_SPAM: bool = True
    # Send a reply through Gmail itself (aligned DMARC, appears in Gmail's Sent)
    # instead of Brevo, when the mailbox is connected and the addresses match.
    GMAIL_SEND_REPLIES: bool = True

    # --- Email enrichment (free tiers — every one of these is optional) ------
    # Enrichment runs in four stages, cheapest first. Only the last two need a
    # provider; the first two are pure Python / DNS and always available:
    #
    #   1. clean      — syntax, lower-casing, CSV junk stripped          free
    #   2. diagnose   — MX records, typo correction, role/disposable flags free
    #   3. find       — look up a missing address on the company's domain (Hunter)
    #   4. verify     — prove the mailbox exists (ZeroBounce / NeverBounce / Hunter)
    #
    # A guessed (pattern-inferred) address is NEVER marked verified, and the
    # send paths filter unverified addresses out, so a guess cannot quietly
    # bounce and damage sender reputation.
    EMAIL_ENRICHMENT_ENABLED: bool = True
    # Master switch for the network calls. With it off, import still cleans and
    # diagnoses addresses, but nothing is looked up or verified over the wire.
    EMAIL_ENRICHMENT_USE_PROVIDERS: bool = True
    # Pattern guessing (first.last@domain). Off by default: the results are
    # stored but unverified, and a guess costs a bounce if it is ever sent to.
    EMAIL_ENRICHMENT_ALLOW_INFERRED: bool = False
    # Add an address found during enrichment as an extra reply-capable alias on
    # the existing contact instead of overwriting the primary address.
    EMAIL_ENRICHMENT_KEEP_PRIMARY: bool = True
    # Email a contact whose address was pattern-GUESSED (source "inferred").
    # Off by default: a guess is unverified by construction, and one bounce is
    # charged against the sender reputation of every address the app sends to.
    # An operator who has reviewed the guesses can opt in for a campaign.
    EMAIL_SEND_INFERRED: bool = False
    # Daily sends allowed PER MAILBOX when an account has no daily limit of its own.
    # A new sending mailbox has no reputation; the usual guidance is 20-50 a day
    # per mailbox, rising slowly. This is the deployment default for the
    # ``email_daily_per_mailbox`` sending rule (Settings -> Sending Rules can
    # override it); an account's own daily_limit can only lower it. 0 = no
    # per-mailbox default.
    EMAIL_DEFAULT_DAILY_LIMIT: int = 30
    # Per-domain DNS cache TTL, in seconds. MX answers change rarely; caching
    # them keeps a 5,000-row import from making 5,000 DNS round-trips.
    EMAIL_ENRICHMENT_DNS_TTL: int = 3600
    EMAIL_ENRICHMENT_TIMEOUT: int = 8

    #: Hunter.io free plan — ~25 searches and ~50 verifications per month.
    HUNTER_API_KEY: Optional[str] = None
    #: ZeroBounce free plan — ~100 credits.
    ZEROBOUNCE_API_KEY: Optional[str] = None
    #: NeverBounce free plan — ~1,000 one-off credits.
    NEVERBOUNCE_API_KEY: Optional[str] = None

    # --- Reacher (open-source email validator, https://reacher.email) --------
    # Point REACHER_API_URL at a Reacher instance to delegate mailbox checks
    # to it (self-hosted: `docker run -p 8080:8080 reacherhq/check-if-email-
    # exists`, then REACHER_API_URL=http://localhost:8080/v2/check_email; or
    # use the hosted endpoint with REACHER_API_KEY). When it is empty — or
    # unreachable — the app runs built-in checks itself (syntax, MX,
    # disposable/role flags, SMTP RCPT probe with catch-all detection), so the
    # validator needs no API key. Blocked probes are reported as unknown.
    REACHER_API_URL: Optional[str] = None
    REACHER_API_KEY: Optional[str] = None
    #: Let the built-in validator perform the live SMTP mailbox probe when no
    # Reacher instance answers. Off = syntax/MX/disposable checks only.
    EMAIL_VALIDATOR_SMTP: bool = True

    # --- Rate Limiting ---
    #: Brute-force protection on the password endpoints. The wall is back on,
    #: so this is the limit that matters — a human types the password once.
    RATE_LIMIT_LOGIN: str = "10/minute"
    #: Global ceiling for API traffic, per client IP. This has to clear one
    #: person using the app normally: a single page load is 5-10 requests, the
    #: bell polls every 30 s, and several people may share one office/NAT
    #: address. At the previous 60/minute a real user could hit a 429 while
    #: simply navigating, which reads as "the app is broken".
    RATE_LIMIT_API: str = "600/minute"

    # ``.env`` is looked up next to this file's project root AND in the current
    # working directory. Running uvicorn from ``backend/`` used to silently skip
    # a repo-root ``.env``, which is exactly how a local run ends up pointed at
    # a Postgres server that is not there.
    model_config = {
        "env_file": (
            os.path.join(_PROJECT_ROOT, ".env"),
            os.path.join(os.getcwd(), ".env"),
        ),
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

    @property
    def callgate_configured(self) -> bool:
        """True when the CallGate gateway has usable credentials (env-level)."""
        return bool(
            self.CALLGATE_BASE_URL and self.CALLGATE_USERNAME and self.CALLGATE_PASSWORD
        )

    @property
    def email_enrichment_providers(self) -> dict:
        """Configured enrichment providers, in the order they are tried.

        Returns an empty dict when nothing is configured — the enrichment
        service then runs its free local stages only (clean + diagnose) and
        says so, instead of failing every row.
        """
        providers: dict = {}
        if self.REACHER_API_URL:
            # Open-source Reacher instance (https://reacher.email) — free when
            # self-hosted, so it is tried before the paid verifiers.
            providers["reacher"] = self.REACHER_API_URL
        if self.HUNTER_API_KEY:
            providers["hunter"] = self.HUNTER_API_KEY
        if self.ZEROBOUNCE_API_KEY:
            providers["zerobounce"] = self.ZEROBOUNCE_API_KEY
        if self.NEVERBOUNCE_API_KEY:
            providers["neverbounce"] = self.NEVERBOUNCE_API_KEY
        return providers

    @property
    def email_enrichment_active(self) -> bool:
        """True when enrichment can do more than clean what is already there."""
        return bool(
            self.EMAIL_ENRICHMENT_ENABLED
            and self.EMAIL_ENRICHMENT_USE_PROVIDERS
            and self.email_enrichment_providers
        )

    def uses_default_admin_password(self) -> bool:
        """Is the operator password still the shipped default?

        Deliberately NOT part of :meth:`insecure_defaults`: refusing to boot a
        deployment over a weak password would take a working app offline. The
        Setup Guide raises it as a to-do instead, which is where an operator
        actually sees it.
        """
        return (self.ADMIN_PASSWORD or "").strip() == INSECURE_ADMIN_PASSWORD

    def insecure_defaults(self) -> list[str]:
        """Names of settings still holding an unsafe placeholder value."""
        problems: list[str] = []
        if self.SECRET_KEY == INSECURE_SECRET_KEY:
            problems.append("SECRET_KEY")
        if self.CREDENTIAL_ENCRYPTION_KEY == INSECURE_ENCRYPTION_KEY:
            problems.append("CREDENTIAL_ENCRYPTION_KEY")
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
