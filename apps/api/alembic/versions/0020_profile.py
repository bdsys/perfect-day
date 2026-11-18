"""profile: users.temperature_unit and avatar columns

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-17

temperature_unit is 'F' or 'C' (default 'F'). A user's avatar is either one of
16 preset icons or an uploaded photo (encrypted like entry photos), never both;
with neither, the UI shows the initial.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# Copied literally from app.models.AVATAR_ICONS: migrations must not import app code.
AVATAR_ICONS = (
    "cat",
    "dog",
    "bird",
    "fish",
    "rabbit",
    "squirrel",
    "turtle",
    "panda",
    "snail",
    "flower",
    "sun",
    "moon",
    "rocket",
    "rainbow",
    "cake",
    "paw-print",
)


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("temperature_unit", sa.CHAR(1), nullable=False, server_default="F"),
    )
    op.add_column("users", sa.Column("avatar_icon", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("avatar_s3_key", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("avatar_dek_ciphertext", sa.LargeBinary(), nullable=True))
    op.create_check_constraint(
        "ck_users_temperature_unit", "users", "temperature_unit IN ('F','C')"
    )
    icons = ",".join(f"'{n}'" for n in AVATAR_ICONS)
    op.create_check_constraint("ck_users_avatar_icon", "users", f"avatar_icon IN ({icons})")
    op.create_check_constraint(
        "ck_users_avatar_exclusive",
        "users",
        "NOT (avatar_icon IS NOT NULL AND avatar_s3_key IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_avatar_exclusive", "users", type_="check")
    op.drop_constraint("ck_users_avatar_icon", "users", type_="check")
    op.drop_constraint("ck_users_temperature_unit", "users", type_="check")
    op.drop_column("users", "avatar_dek_ciphertext")
    op.drop_column("users", "avatar_s3_key")
    op.drop_column("users", "avatar_icon")
    op.drop_column("users", "temperature_unit")
