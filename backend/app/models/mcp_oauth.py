"""
OAuth 2.1 state for the MCP connectors.

WHY THIS EXISTS
---------------
ChatGPT and Claude do not accept an API key on a custom connector. Both walk the
OAuth 2.1 authorization-code flow described by the MCP authorization spec:
they read protected-resource metadata off this server, discover an
authorization server, register themselves as a client (RFC 7591 dynamic
registration, or a Client ID Metadata Document), then send the operator to
``/authorize`` and exchange the code at ``/token`` with PKCE (S256).

A bearer token created in Settings therefore could never connect ChatGPT or
Claude — the clients had no ``/.well-known`` documents to read and no
``/authorize`` to send the operator to, so they failed before the token was ever
offered. These tables are what makes that flow real:

* :class:`McpOAuthClient` — a registered client (one per connector, created by
  dynamic registration, by a Client ID Metadata Document, or by hand).
* :class:`McpOAuthCode` — a single-use authorization code, kept with the PKCE
  challenge it was issued against.
* :class:`McpOAuthToken` — refresh tokens, and the hash of every access token
  issued, so a connection can be revoked from the app even though the access
  token itself is a self-contained JWT.
* :class:`McpConnection` — per-connector bookkeeping for the UI: is OAuth on,
  when did it last work, what did the last test say.

Nothing here replaces :class:`app.models.mcp.McpToken`. Static tokens keep
working for the clients that can send a header (Claude Code, MCP Inspector,
curl, the Arena bridge); OAuth is added alongside them.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class McpOAuthClient(Base):
    """One registered OAuth client — i.e. one assistant's connector registration.

    ``client_id`` is opaque and generated here for dynamic registration, or is
    the CIMD URL itself when the client used a Client ID Metadata Document
    (ChatGPT sends ``https://chatgpt.com/oauth/client.json`` as its client id).

    A public client (``token_endpoint_auth_method == "none"``) has no secret:
    PKCE is what protects it. That is what ChatGPT and Claude use.
    """

    __tablename__ = "mcp_oauth_clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: The client_id this client must send. Unique.
    client_id: Mapped[str] = mapped_column(String(600), nullable=False, unique=True, index=True)
    #: SHA-256 of the client secret, or NULL for a public client.
    client_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: JSON array of exact redirect URIs the client may use.
    redirect_uris: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    #: JSON array, e.g. ["authorization_code", "refresh_token"].
    grant_types: Mapped[str] = mapped_column(Text, nullable=False, default='["authorization_code"]')
    #: JSON array, e.g. ["code"].
    response_types: Mapped[str] = mapped_column(Text, nullable=False, default='["code"]')
    #: none | client_secret_post | client_secret_basic | private_key_jwt
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        String(40), nullable=False, default="none"
    )
    #: Space-separated scopes this client is allowed to request.
    scope: Mapped[str] = mapped_column(String(200), nullable=False, default="read write")

    #: dcr | cimd | static — how the registration came about.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="dcr")
    #: The Client ID Metadata Document URL, when source == "cimd".
    cimd_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    #: Which connector profile registered this client (chatgpt / claude / arena / generic).
    connector: Mapped[str] = mapped_column(String(30), nullable=False, default="generic")

    #: Shown once at registration; lets a client read/update its own record.
    registration_access_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<McpOAuthClient(client_id={self.client_id[:40]!r}, connector={self.connector})>"


class McpOAuthCode(Base):
    """A single-use authorization code plus the PKCE challenge it was issued with.

    Short-lived (60 s) and deleted the moment it is redeemed, so a leaked
    redirect cannot be replayed. Only the hash of the code is stored: the row is
    useless without the code the client actually received.
    """

    __tablename__ = "mcp_oauth_codes"
    __table_args__ = (Index("ix_mcp_oauth_codes_expires", "expires_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    client_id: Mapped[str] = mapped_column(String(600), nullable=False)
    #: The exact redirect_uri the code was issued for; the token request must match it.
    redirect_uri: Mapped[str] = mapped_column(String(600), nullable=False)
    scope: Mapped[str] = mapped_column(String(200), nullable=False, default="read")
    #: RFC 8707 resource the token must be bound to.
    resource: Mapped[str | None] = mapped_column(String(600), nullable=True)

    code_challenge: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Only S256 is accepted; "plain" is refused (OAuth 2.1 removes it).
    code_challenge_method: Mapped[str] = mapped_column(String(10), nullable=False, default="S256")

    #: The operator who approved the connection — the authority tools run with.
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connector: Mapped[str] = mapped_column(String(30), nullable=False, default="generic")

    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class McpOAuthToken(Base):
    """Refresh tokens, and a record of every access token issued.

    Access tokens are JWTs, so they need no row to be *validated* — but they do
    need one to be *revoked*. The app's "Revoke" button sets ``revoked_at`` and
    the token stops working on the next call, which a pure-JWT design cannot do.
    """

    __tablename__ = "mcp_oauth_tokens"
    __table_args__ = (
        Index("ix_mcp_oauth_tokens_kind_hash", "kind", "token_hash"),
        Index("ix_mcp_oauth_tokens_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: access | refresh
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default="access")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: First characters of a refresh token, so the UI can name the connection.
    prefix: Mapped[str] = mapped_column(String(20), nullable=False, default="")

    client_id: Mapped[str] = mapped_column(String(600), nullable=False)
    admin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope: Mapped[str] = mapped_column(String(200), nullable=False, default="read")
    resource: Mapped[str | None] = mapped_column(String(600), nullable=True)
    connector: Mapped[str] = mapped_column(String(30), nullable=False, default="generic")

    #: Set when the operator revokes the connection, or when a refresh rotates it.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class McpConnection(Base):
    """Per-connector state, so the settings screen can say what actually works.

    One row per connector key (``chatgpt``, ``claude``, ``arena``, ``generic``).
    Holds the operator's switches (is OAuth enabled for this connector, which
    scope a new authorization may request) and the result of the last
    self-test the app ran against its own endpoint — the answer to "why is my
    connector not working".
    """

    __tablename__ = "mcp_connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    connector: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)

    #: False = this connector answers only with static bearer tokens.
    oauth_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: The scope an authorization on this connector may grant at most.
    default_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="write")
    #: Trim the tool list to the focused set (ChatGPT behaves better with fewer tools).
    toolset: Mapped[str] = mapped_column(String(20), nullable=False, default="full")

    #: unknown | ok | error — set by the built-in connector test.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: JSON: one entry per handshake step the test ran.
    last_test_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: Counters, so the operator can see a connector is actually being used.
    authorize_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<McpConnection(connector={self.connector}, status={self.status})>"
