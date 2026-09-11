"""Browser push subscriptions (inbuilt notifications, no third party).

Each row is one browser/device that tapped "Enable notifications" and granted
the Notification permission. Pushes are delivered with VAPID Web Push, which
is free forever and works even when the app is closed (installed PWA).
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Push service endpoint (FCM/Mozilla/Apple) — globally unique per device.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    # Encryption keys from the browser's PushSubscription.toJSON().
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)

    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Consecutive delivery failures; the subscription is pruned after 5 or
    # immediately on 404/410 (browser uninstalled / permission revoked).
    fail_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self):
        return f"<PushSubscription(id={self.id}, endpoint={self.endpoint[:60]}…)>"
