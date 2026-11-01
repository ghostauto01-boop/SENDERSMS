"""P0/P1 additive columns: bot-event flags, poll bookkeeping, plain-email mode.

All columns are nullable or defaulted, so the migration is backwards
compatible with every existing row and safe to run twice (each add is guarded
by an inspector check).

* ``email_events.is_bot`` / ``bot_reason`` / ``occurred_at`` — engagement
  events split into human vs bot (security-scanner opens/clicks, proxy opens)
  with the provider's own event time for the "click within 10s of the open"
  rule.
* ``messages.status_polled_at`` — when the Brevo event-API fallback last
  polled this message, so a message stuck in ``sent`` is re-polled politely.
* ``campaigns.plain_personal_email`` / ``ads_campaigns.plain_personal_email``
  — the "Plain personal email" format switch (text part + minimal HTML, no
  card/pixel/wrapping, tiny gray unsubscribe link).
* ``campaigns.timezone_name`` / ``allowed_weekdays`` / ``sender_daily_limits``
  and ``ads_campaigns.sender_daily_limits`` — send-window and per-sender cap
  controls.
* ``email_accounts.last_webhook_event_at`` — webhook health per sender.

Revision ID: 0002_p0_p1_additive_columns
Revises: 0001_meetings_email_columns
Create Date: 2026-11-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_p0_p1_additive_columns"
down_revision = "0001_meetings_email_columns"
branch_labels = None
depends_on = None


#: table -> {name: (type, nullable, default)} with Python scalar defaults.
NEW_COLUMNS: dict[str, dict[str, tuple]] = {
    "email_events": {
        "is_bot": (sa.Boolean(), False, False),
        "bot_reason": (sa.String(120), True, None),
        "occurred_at": (sa.DateTime(timezone=True), True, None),
    },
    "messages": {
        "status_polled_at": (sa.DateTime(timezone=True), True, None),
    },
    "campaigns": {
        "plain_personal_email": (sa.Boolean(), False, False),
        "timezone_name": (sa.String(60), True, None),
        "allowed_weekdays": (sa.String(30), True, None),
        "sender_daily_limits": (sa.Text(), True, None),
    },
    "ads_campaigns": {
        "plain_personal_email": (sa.Boolean(), False, True),
        "sender_daily_limits": (sa.Text(), True, None),
    },
    "email_accounts": {
        "last_webhook_event_at": (sa.DateTime(timezone=True), True, None),
    },
}


def _default_literal(dialect_name: str, value) -> sa.text | None:
    if value is None:
        return None
    if isinstance(value, bool):
        if dialect_name == "postgresql":
            return sa.text("TRUE" if value else "FALSE")
        return sa.text("1" if value else "0")
    if isinstance(value, int):
        return sa.text(str(value))
    escaped = str(value).replace("'", "''")
    return sa.text(f"'{escaped}'")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    for table, columns in NEW_COLUMNS.items():
        if table not in tables:
            continue  # fresh database: create_all owns it
        have = {col["name"] for col in inspector.get_columns(table)}
        for name, (col_type, nullable, default) in columns.items():
            if name in have:
                continue
            op.add_column(
                table,
                sa.Column(
                    name,
                    col_type,
                    nullable=nullable,
                    server_default=_default_literal(bind.dialect.name, default),
                ),
            )


def downgrade() -> None:
    # Additive-only by policy; deliberately a no-op.
    pass
