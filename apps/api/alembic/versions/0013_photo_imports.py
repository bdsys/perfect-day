"""Photo display copies, per-user Google duplicates, Google photo imports

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-13

PoC item 14. A photo gains a display-size copy. The duplicate index for
imported photos becomes per user (two people may pick the same Google item)
and ignores soft-deleted rows. A new photo_imports table tracks one Google
Photos Picker import into an entry.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("photos", sa.Column("display_s3_key", sa.Text(), nullable=True))

    op.drop_index("ix_photos_source_external_id", table_name="photos")
    op.create_index(
        "ux_photos_user_source_external_id",
        "photos",
        ["user_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        "photo_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("google_session_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="waiting"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("added_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_videos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "failed_names",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("dismissed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('waiting','copying','done','failed','cancelled','expired',"
            "'needs_reconnect')",
            name="ck_photo_imports_status",
        ),
    )
    op.execute("""
        CREATE TRIGGER trg_photo_imports_updated_at
        BEFORE UPDATE ON photo_imports
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)


def downgrade() -> None:
    op.drop_table("photo_imports")
    op.drop_index("ux_photos_user_source_external_id", table_name="photos")
    op.create_index(
        "ix_photos_source_external_id",
        "photos",
        ["source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.drop_column("photos", "display_s3_key")
