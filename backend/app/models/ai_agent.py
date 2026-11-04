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
    model: Mapped[str] = mapped_column(String(30), nullable=False, default="groq")  # groq | gemini
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
