"""
Contact List and ContactListMember models.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ContactList(Base):
    __tablename__ = "contact_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    members: Mapped[list["ContactListMember"]] = relationship(
        "ContactListMember", back_populates="list", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<ContactList(id={self.id}, name={self.name})>"


class ContactListMember(Base):
    __tablename__ = "contact_list_members"
    __table_args__ = (UniqueConstraint("list_id", "contact_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    list_id: Mapped[int] = mapped_column(Integer, ForeignKey("contact_lists.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[int] = mapped_column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    list: Mapped["ContactList"] = relationship("ContactList", back_populates="members")
    contact: Mapped["Contact"] = relationship("Contact", back_populates="list_memberships")
