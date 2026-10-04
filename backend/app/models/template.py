"""
SMS Template model.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    #: "sms" or "email". Every list is filtered by channel so the SMS and the
    #: email libraries never mix.
    channel: Mapped[str] = mapped_column(String(10), default="sms", nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # ---- Email-only fields -------------------------------------------
    #: Subject line, supports the same {{shortcode}} placeholders as `body`.
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Rich body. When set, email sends prefer it over `body`; `body` stays the
    #: plain-text fallback and the preview text in the UI.
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Optional sender override; NULL means "use the campaign's account".
    email_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Short preview line some inboxes show under the subject.
    preheader: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: JSON list of attachments copied onto every send that uses this template.
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Whether sends through this template carry the List-Unsubscribe headers.
    #: Bulk mail without them is filtered by Gmail/Yahoo since 2024.
    include_unsubscribe: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Integer, default=True, nullable=False)
    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<Template(id={self.id}, name={self.name})>"
