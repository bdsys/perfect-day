"""Add google_email and google_name to oauth_tokens

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("oauth_tokens", sa.Column("google_email", sa.String(320), nullable=True))
    op.add_column("oauth_tokens", sa.Column("google_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("oauth_tokens", "google_name")
    op.drop_column("oauth_tokens", "google_email")
