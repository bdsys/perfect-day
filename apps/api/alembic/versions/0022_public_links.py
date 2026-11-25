"""public_links: link-only public pages (wish lists first)

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-24

token_hash is the SHA-256 hex of the link token (the lookup key); token_encrypted is
the token under the AES-GCM key so the owner can copy the link again. A database dump
alone gives no working link. target_id has no foreign key: the target is polymorphic
and deleting it removes its links.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "diary_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("diaries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('wish_list')", name="ck_public_links_kind"),
    )
    # One active link per target (closes the concurrent create_link race). It also serves
    # the active-link lookup, so there is no plain (kind, target_id) index.
    op.create_index(
        "uq_public_links_active_target",
        "public_links",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_public_links_active_target", table_name="public_links")
    op.drop_table("public_links")
