"""AI Agent models — in-app LLM assistant with sessions, memory, skills, and file context."""

from datetime import datetime, timezone
from sqlalchemy import DateTime, Integer, String, Text, ForeignKey, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class AIAgentSession(Base):
    __tablename__ = "ai_agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="New Session")
    #: Which provider the session was created against. "openrouter" now; older
    #: rows say "groq"/"gemini" and resolve to OpenRouter at read time
    #: (``ai_providers.get_default_provider``), so no migration is needed.
    model: Mapped[str] = mapped_column(String(30), nullable=False, default="openrouter")
    # Accumulated memory summary for the session (compressed history)
    memory: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Active skills for this session (JSON list of skill names)
    skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # System prompt override
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    messages: Mapped[list["AIAgentMessage"]] = relationship("AIAgentMessage", back_populates="session", cascade="all, delete-orphan", order_by="AIAgentMessage.created_at")


class AIAgentMessage(Base):
    __tablename__ = "ai_agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("ai_agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system | tool
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Tool calls made by assistant in this message (JSON)
    tool_calls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Tool call result (if role == tool)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True)

    session: Mapped["AIAgentSession"] = relationship("AIAgentSession", back_populates="messages")


class AIAgentSkill(Base):
    __tablename__ = "ai_agent_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)  # Skill file content / instructions
    # Whether this skill is active by default
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class AIAgentFile(Base):
    __tablename__ = "ai_agent_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("ai_agent_sessions.id", ondelete="CASCADE"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Stored content (CSV text, etc.) - for larger files store truncated preview
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Parsed rows preview
    preview: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class AIAgentAction(Base):
    """One tool the agent ran, or asked permission to run.

    This table is three things at once, which is why it exists rather than
    three more tables:

    * the **confirmation queue** — a high-impact tool (start a campaign, bulk
      delete, change the gateway) is stored here as ``status="pending"`` with
      the exact arguments, and only executed when the operator presses
      Approve;
    * the **agent activity log** — every executed tool leaves a row with its
      parameters, result, duration and status, shown on the Agent activity page;
    * the **undo record** — the row keeps enough of the result (ids, counts) for
      a reversible action to be rolled back from the same screen.
    """

    __tablename__ = "ai_agent_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ai_agent_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    #: One human sentence: "Imported 53 contacts into list X".
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: pending | running | done | failed | cancelled | rejected
    status: Mapped[str] = mapped_column(String(20), default="done", nullable=False, index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Set when the action can be undone (see services/agent_undo.py).
    undoable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
