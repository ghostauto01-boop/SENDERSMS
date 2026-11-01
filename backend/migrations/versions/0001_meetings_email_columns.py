"""Add the meeting email-invite columns the calendar API reads.

The ``meetings`` model grew an email channel (invite + reminders) after the
table was already in production. ``create_all`` never adds a column to an
existing table, so any deployment whose table predates the model fails
GET /api/v1/calendar/upcoming with:

    column meetings.send_invite_email does not exist

This migration adds every column the Meeting model defines that a legacy
``meetings`` table can be missing — the six email columns plus the other
newer bookkeeping ones — as nullable-or-defaulted additions, so it is
backwards compatible and safe on a populated table.

Revision ID: 0001_meetings_email_columns
Revises:
Create Date: 2026-11-01
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_meetings_email_columns"
down_revision = None
branch_labels = None
depends_on = None


#: name -> (type, nullable, default). ``default`` values are Python scalars
#: (bool / int / str) rendered per dialect so existing rows behave exactly
#: like new ones. None = nullable column with no default.
NEW_COLUMNS: dict[str, tuple] = {
    "send_invite_email": (sa.Boolean(), False, False),
    "invite_subject": (sa.String(500), True, None),
    "email_account_id": (sa.Integer(), True, None),
    "invite_email_sent": (sa.Boolean(), False, False),
    "send_email_reminder": (sa.Boolean(), False, False),
    "reminder_subject": (sa.String(500), True, None),
    "send_sms_reminder": (sa.Boolean(), False, True),
    "reminder_minutes_json": (sa.Text(), True, None),
    "reminder_template_id": (sa.Integer(), True, None),
    "reminder_body": (sa.Text(), True, None),
    "reminders_sent_json": (sa.Text(), True, None),
    "send_invite_sms": (sa.Boolean(), False, True),
    "invite_template_id": (sa.Integer(), True, None),
    "invite_body": (sa.Text(), True, None),
    "invite_sent": (sa.Boolean(), False, False),
    "tags_json": (sa.Text(), True, None),
    "campaign_id": (sa.Integer(), True, None),
    "event_type": (sa.String(30), False, "meeting"),
    "all_day": (sa.Boolean(), False, False),
    "meeting_link": (sa.String(1000), True, None),
    "outcome_notes": (sa.Text(), True, None),
    "created_by": (sa.String(150), True, None),
    "conversation_id": (sa.Integer(), True, None),
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


def _existing_columns(inspector, table: str) -> set[str]:
    try:
        return {col["name"] for col in inspector.get_columns(table)}
    except Exception:  # table does not exist -> create_all owns it
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "meetings" not in inspector.get_table_names():
        return  # fresh database: create_all builds the full table
    have = _existing_columns(inspector, "meetings")
    for name, (col_type, nullable, default) in NEW_COLUMNS.items():
        if name in have:
            continue
        op.add_column(
            "meetings",
            sa.Column(
                name,
                col_type,
                nullable=nullable,
                server_default=_default_literal(bind.dialect.name, default),
            ),
        )


def downgrade() -> None:
    # Additive-only by policy: dropping columns would destroy operator data,
    # so this is deliberately a no-op.
    pass
