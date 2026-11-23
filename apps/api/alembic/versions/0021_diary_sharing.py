"""diary sharing: diaries.owned_since, diaries.handover_to_user_id, invitations index

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-22

owned_since is when the current owner got the diary (created_at until a hand-over);
the read-only order uses it so a received diary never displaces one the owner made.
handover_to_user_id names the person a deleting owner offered the diary to.

invitations.token now holds the SHA-256 hex digest of the link token, not the token.
Rows from before this release hold a raw token, so they are deleted.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("diaries", sa.Column("owned_since", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE diaries SET owned_since = created_at")
    op.alter_column(
        "diaries",
        "owned_since",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    op.add_column(
        "diaries",
        sa.Column("handover_to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_diaries_handover_to_user_id_users",
        "diaries",
        "users",
        ["handover_to_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute("DELETE FROM invitations")
    op.create_index("ix_invitations_diary_id", "invitations", ["diary_id"])


def downgrade() -> None:
    op.drop_index("ix_invitations_diary_id", table_name="invitations")
    op.drop_constraint("fk_diaries_handover_to_user_id_users", "diaries", type_="foreignkey")
    op.drop_column("diaries", "handover_to_user_id")
    op.drop_column("diaries", "owned_since")
