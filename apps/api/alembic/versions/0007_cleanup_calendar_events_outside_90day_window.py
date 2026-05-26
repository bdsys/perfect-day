"""Clean up calendar events outside ±90 day window

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-25

Deletes google_calendar events with occurred_at outside the ±90 day window
from today. This ensures the event table stays lean and only contains recent
calendar data. This cleanup is safe because:
1. The scan worker only pulls future calendar events anyway (90 days forward)
2. The calendar picker only surfaces recent events
3. Published entries retain their event data via the LLM generation record
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from datetime import datetime, timedelta, timezone

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = datetime.now(tz=timezone.utc)
    lower = now - timedelta(days=90)
    upper = now + timedelta(days=90)

    op.execute(
        sa.text(
            """
            DELETE FROM events
            WHERE source = 'google_calendar'
              AND (occurred_at < :lower OR occurred_at > :upper)
            """
        ).bindparams(lower=lower, upper=upper)
    )


def downgrade() -> None:
    pass  # Data deletion is intentional and irreversible
