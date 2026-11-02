"""Add the meeting email-invite columns the calendar API reads.

The ``meetings`` model grew an email channel (invite + reminders) after the
table was already in production. ``create_all`` never adds a column to an
existing table, so any deployment whose table predates the model fails
GET /api/v1/calendar/upcoming with:

    column meetings.send_invite_email does not exist

Note that ``Meeting`` maps its 0/1 flags as ``sa.Integer`` (and queries compare
``Meeting.send_email_reminder == 1``), so these columns must be ``INTEGER``
with ``0``/``1`` defaults on both PostgreSQL and SQLite.

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


#: name -> (type, nullable, default). Integer flags use 0/1 to match Meeting.
NEW_COLUMNS: dict[str, tuple] = {
    "send_invite_email": (sa.Integer(), False, 0),
    "invite_subject": (sa.String(500), True, None),
    "email_account_id": (sa.Integer(), True, None),
    "invite_email_sent": (sa.Integer(), False, 0),
    "send_email_reminder": (sa.Integer(), False, 0),
    "reminder_subject": (sa.String(500), True, None),
    "send_sms_reminder": (sa.Integer(), False, 1),
    "reminder_minutes_json": (sa.Text(), True, None),
    "reminder_template_id": (sa.Integer(), True, None),
    "reminder_body": (sa.Text(), True, None),
    "reminders_sent_json": (sa.Text(), True, None),
    "send_invite_sms": (sa.Integer(), False, 1),
    "invite_template_id": (sa.Integer(), True, None),
    "invite_body": (sa.Text(), True, None),
    "invite_sent": (sa.Integer(), False, 0),
    "tags_json": (sa.Text(), True, None),
    "campaign_id": (sa.Integer(), True, None),
    "event_type": (sa.String(30), False, "meeting"),
    "all_day": (sa.Integer(), False, 0),
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


def _existing_columns(inspector, table: str) -> dict[str, dict]:
    try:
        return {col["name"]: col for col in inspector.get_columns(table)}
    except Exception:  # table does not exist -> create_all owns it
        return {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "meetings" not in inspector.get_table_names():
        return  # fresh database: create_all builds the full table
    have = _existing_columns(inspector, "meetings")
    for name, (col_type, nullable, default) in NEW_COLUMNS.items():
        if name in have:
            # Repair legacy boolean columns on PostgreSQL to integer so
            # ``Meeting.<flag> == 1`` does not fail with UndefinedFunctionError.
            live_type = str(have[name].get("type", "")).lower()
            if bind.dialect.name == "postgresql" and isinstance(col_type, sa.Integer) and "bool" in live_type:
                def_int = 1 if default else 0
                bind.execute(sa.text(f'ALTER TABLE "meetings" ALTER COLUMN "{name}" DROP DEFAULT'))
                bind.execute(
                    sa.text(
                        f'ALTER TABLE "meetings" ALTER COLUMN "{name}" TYPE INTEGER '
                        f'USING (CASE WHEN "{name}" IS NULL THEN NULL WHEN "{name}" THEN 1 ELSE 0 END)'
                    )
                )
                bind.execute(sa.text(f'ALTER TABLE "meetings" ALTER COLUMN "{name}" SET DEFAULT {def_int}'))
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
