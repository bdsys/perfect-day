"""ai_draft_jobs: the "AI is working on this draft" indicator

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-17

One row per queued or running AI draft. Rows are deleted when the draft
finishes, and cascade with their entry.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_draft_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ai_draft_jobs_entry_id", "ai_draft_jobs", ["entry_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_draft_jobs_entry_id", table_name="ai_draft_jobs")
    op.drop_table("ai_draft_jobs")
