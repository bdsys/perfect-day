"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("password_hash", sa.Text()),
        sa.Column("display_name", sa.Text()),
        sa.Column("subscription_tier", sa.String(20), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.Text()),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("hard_delete_after", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("subscription_tier IN ('free','tier1','tier2')", name="ck_users_subscription_tier"),
    )
    # Use citext for case-insensitive email uniqueness
    op.execute("ALTER TABLE users ALTER COLUMN email TYPE citext")
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # updated_at trigger (reusable function)
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    for tbl in ["users"]:
        op.execute(f"""
            CREATE TRIGGER trg_{tbl}_updated_at
            BEFORE UPDATE ON {tbl}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """)

    # ── social_identities ──────────────────────────────────────────────────
    op.create_table(
        "social_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_user_id", sa.Text(), nullable=False),
        sa.Column("relay_email", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_social_identities_provider_sub"),
        sa.CheckConstraint("provider IN ('google','facebook','apple')", name="ck_social_identities_provider"),
    )
    op.execute("ALTER TABLE social_identities ALTER COLUMN relay_email TYPE citext")
    op.execute("""
        CREATE TRIGGER trg_social_identities_updated_at
        BEFORE UPDATE ON social_identities
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── oauth_tokens ───────────────────────────────────────────────────────
    op.create_table(
        "oauth_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("access_token_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_ciphertext", sa.LargeBinary()),
        sa.Column("scopes_granted", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "provider", name="uq_oauth_tokens_user_provider"),
        sa.CheckConstraint("provider IN ('google','spotify')", name="ck_oauth_tokens_provider"),
    )
    op.execute("""
        CREATE TRIGGER trg_oauth_tokens_updated_at
        BEFORE UPDATE ON oauth_tokens
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── photos (created before diaries so diaries.cover_photo_id can FK it)
    op.create_table(
        "photos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("s3_key", sa.Text(), unique=True, nullable=False),
        sa.Column("mime_type", sa.Text()),
        sa.Column("bytes", sa.BigInteger()),
        sa.Column("taken_at", sa.DateTime(timezone=True)),
        sa.Column("lat", sa.Numeric(9, 6)),
        sa.Column("lon", sa.Numeric(9, 6)),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.Text()),
        sa.Column("thumbnail_s3_key", sa.Text()),
        sa.Column("ai_description", sa.Text()),
        sa.Column("dek_ciphertext", sa.LargeBinary()),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('google_photos','upload')", name="ck_photos_source"),
    )
    op.create_index(
        "ix_photos_source_external_id",
        "photos",
        ["source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.execute("""
        CREATE TRIGGER trg_photos_updated_at
        BEFORE UPDATE ON photos
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── diaries ────────────────────────────────────────────────────────────
    op.create_table(
        "diaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("subject_name", sa.Text()),
        sa.Column("subject_relation", sa.String(20), nullable=False, server_default="self"),
        sa.Column("voice_override", sa.String(20)),
        sa.Column("tone_hint", sa.Text(), nullable=False, server_default="warm, narrative"),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("scan_interval_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("scan_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("cover_photo_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("photos.id", ondelete="SET NULL")),
        sa.Column("notifications_muted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("photos_backfill_days_max", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("hard_delete_after", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("owner_user_id", "slug", name="uq_diaries_owner_slug"),
        sa.CheckConstraint("subject_relation IN ('self','child','family','other_person')", name="ck_diaries_subject_relation"),
        sa.CheckConstraint(
            "voice_override IS NULL OR voice_override IN ('first_singular','first_plural','second','third')",
            name="ck_diaries_voice_override",
        ),
    )
    op.create_index("ix_diaries_owner_user_id", "diaries", ["owner_user_id"])
    op.execute("""
        CREATE TRIGGER trg_diaries_updated_at
        BEFORE UPDATE ON diaries
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── diary_permissions ──────────────────────────────────────────────────
    op.create_table(
        "diary_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diary_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("notifications_muted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("diary_id", "user_id", name="uq_diary_permissions_diary_user"),
        sa.CheckConstraint("role IN ('viewer','editor')", name="ck_diary_permissions_role"),
    )
    op.execute("""
        CREATE TRIGGER trg_diary_permissions_updated_at
        BEFORE UPDATE ON diary_permissions
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── invitations ────────────────────────────────────────────────────────
    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diary_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invited_email", sa.Text(), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("token", sa.Text(), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('viewer','editor')", name="ck_invitations_role"),
    )
    op.execute("ALTER TABLE invitations ALTER COLUMN invited_email TYPE citext")
    op.execute("""
        CREATE TRIGGER trg_invitations_updated_at
        BEFORE UPDATE ON invitations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── entries ────────────────────────────────────────────────────────────
    op.create_table(
        "entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diary_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry_end_date", sa.Date()),
        sa.Column("parent_entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entries.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text()),
        sa.Column("body_markdown", sa.Text()),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.String(10), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('draft','published')", name="ck_entries_status"),
        sa.CheckConstraint("created_by IN ('auto','manual')", name="ck_entries_created_by"),
    )
    op.create_index("ix_entries_diary_entry_date", "entries", ["diary_id", "entry_date"])
    op.execute("""
        CREATE TRIGGER trg_entries_updated_at
        BEFORE UPDATE ON entries
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── events ─────────────────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("external_id", sa.Text()),
        sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "source IN ('google_calendar','google_photos','manual','spotify')",
            name="ck_events_source",
        ),
    )
    op.create_index(
        "ix_events_source_external_id",
        "events",
        ["source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.execute("""
        CREATE TRIGGER trg_events_updated_at
        BEFORE UPDATE ON events
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── diary_photos ───────────────────────────────────────────────────────
    op.create_table(
        "diary_photos",
        sa.Column("diary_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("diaries.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("photo_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True),
    )

    # ── entry_photos ───────────────────────────────────────────────────────
    op.create_table(
        "entry_photos",
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entries.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("photo_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Integer()),
    )

    # ── enrichments ────────────────────────────────────────────────────────
    op.create_table(
        "enrichments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.Text()),
        sa.Column("captured_for_at", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("entry_id", "kind", name="uq_enrichments_entry_kind"),
    )
    op.execute("""
        CREATE TRIGGER trg_enrichments_updated_at
        BEFORE UPDATE ON enrichments
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── llm_generations ────────────────────────────────────────────────────
    op.create_table(
        "llm_generations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_hash", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('success','failed')", name="ck_llm_generations_status"),
    )
    op.execute("""
        CREATE TRIGGER trg_llm_generations_updated_at
        BEFORE UPDATE ON llm_generations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── entry_edit_diffs ───────────────────────────────────────────────────
    op.create_table(
        "entry_edit_diffs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("entries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("llm_generation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("llm_generations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body_before_markdown", sa.Text(), nullable=False),
        sa.Column("body_after_markdown", sa.Text(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── scan_jobs ──────────────────────────────────────────────────────────
    op.create_table(
        "scan_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diary_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("diaries.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("last_scan_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_scan_completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_scan_status", sa.String(10)),
        sa.Column("last_calendar_cursor", sa.Text()),
        sa.Column("last_photos_cursor", sa.Text()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_scan_after", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "last_scan_status IS NULL OR last_scan_status IN ('success','partial','failed')",
            name="ck_scan_jobs_last_status",
        ),
    )
    op.execute("""
        CREATE TRIGGER trg_scan_jobs_updated_at
        BEFORE UPDATE ON scan_jobs
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── scan_runs ──────────────────────────────────────────────────────────
    op.create_table(
        "scan_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diary_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("triggered_by", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("events_calendar", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("events_photos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entries_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entries_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_calls_made", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("triggered_by IN ('beat','manual','backfill','admin')", name="ck_scan_runs_triggered_by"),
        sa.CheckConstraint("status IN ('running','success','partial','failed')", name="ck_scan_runs_status"),
    )
    op.execute("""
        CREATE TRIGGER trg_scan_runs_updated_at
        BEFORE UPDATE ON scan_runs
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── backfill_runs ──────────────────────────────────────────────────────
    op.create_table(
        "backfill_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diary_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("to_date", sa.Date(), nullable=False),
        sa.Column("sources", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column("events_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entries_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_backfill_runs_status",
        ),
    )
    op.execute("""
        CREATE TRIGGER trg_backfill_runs_updated_at
        BEFORE UPDATE ON backfill_runs
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── diary_calendar_filters ─────────────────────────────────────────────
    op.create_table(
        "diary_calendar_filters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("diary_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("google_calendar_id", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("diary_id", "google_calendar_id", name="uq_diary_calendar_filters_diary_gcal"),
    )
    op.execute("""
        CREATE TRIGGER trg_diary_calendar_filters_updated_at
        BEFORE UPDATE ON diary_calendar_filters
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── notification_preferences ───────────────────────────────────────────
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("push_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("expo_push_tokens", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("quiet_hours_start", sa.Time(), nullable=False, server_default="20:00"),
        sa.Column("quiet_hours_end", sa.Time(), nullable=False, server_default="07:00"),
        sa.Column("timezone", sa.Text()),
        sa.Column("kinds_disabled", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("email_digest_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("""
        CREATE TRIGGER trg_notification_preferences_updated_at
        BEFORE UPDATE ON notification_preferences
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    """)

    # ── notifications ──────────────────────────────────────────────────────
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False, server_default="normal"),
        sa.Column("channel_push_status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("channel_email_status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("channel_inapp_status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('draft_ready','draft_failed','integration_revoked','entry_published','tier_limit','invite_received','deletion_grace')",
            name="ck_notifications_kind",
        ),
        sa.CheckConstraint("priority IN ('normal','high')", name="ck_notifications_priority"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])

    # ── magic_link_tokens ──────────────────────────────────────────────────
    op.create_table(
        "magic_link_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("ALTER TABLE magic_link_tokens ALTER COLUMN email TYPE citext")
    op.create_index("ix_magic_link_tokens_email_expires", "magic_link_tokens", ["email", "expires_at"])

    # ── refresh_tokens ─────────────────────────────────────────────────────
    op.create_table(
        "refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.Text(), unique=True, nullable=False),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_hint", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])

    # ── audit_log ──────────────────────────────────────────────────────────
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text()),
        sa.Column("target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("refresh_tokens")
    op.drop_table("magic_link_tokens")
    op.drop_table("notifications")
    op.drop_table("notification_preferences")
    op.drop_table("diary_calendar_filters")
    op.drop_table("backfill_runs")
    op.drop_table("scan_runs")
    op.drop_table("scan_jobs")
    op.drop_table("entry_edit_diffs")
    op.drop_table("llm_generations")
    op.drop_table("enrichments")
    op.drop_table("entry_photos")
    op.drop_table("diary_photos")
    op.drop_table("events")
    op.drop_table("entries")
    op.drop_table("invitations")
    op.drop_table("diary_permissions")
    op.drop_table("diaries")
    op.drop_table("photos")
    op.drop_table("oauth_tokens")
    op.drop_table("social_identities")
    op.drop_table("users")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at() CASCADE")
    op.execute('DROP EXTENSION IF EXISTS "pgcrypto"')
    op.execute("DROP EXTENSION IF EXISTS citext")
