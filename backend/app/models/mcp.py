"""
MCP tokens and the audit trail of what an AI assistant did with them.

An MCP client (ChatGPT, Claude, any agent framework) authenticates with a
bearer token created inside the app. Tokens are stored hashed — the plaintext is
shown exactly once, at creation — and every tool call is journalled so the
operator can see, in the app, what the assistant actually did.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class McpToken(Base):
    """One AI assistant's access key.

    ``scope`` is deliberately coarse: ``read`` tokens can look at anything but
    change nothing, ``write`` tokens can operate the whole app. That is the
    switch an operator flips when they want an assistant to actually send.
    """

    __tablename__ = "mcp_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: SHA-256 of the token. The token itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    #: First characters, so the UI can tell two tokens apart.
    prefix: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    scope: Mapped[str] = mapped_column(String(10), nullable=False, default="write")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class McpCall(Base):
    """One tool call an assistant made — the audit trail.

    Kept short and boring on purpose: which tool, what it touched, whether it
    worked, how long it took. The arguments and result are trimmed so a long
    campaign body cannot balloon the table.
    """

    __tablename__ = "mcp_calls"
    __table_args__ = (Index("ix_mcp_calls_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    token_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    #: The HTTP call the tool made inside the app, e.g. "POST /api/v1/contacts".
    request: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    arguments: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
