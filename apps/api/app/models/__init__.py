from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Users & identity
# ---------------------------------------------------------------------------


class User(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(
        String(320), unique=True, nullable=False
    )  # citext via migration
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    subscription_tier: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="free",
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    hard_delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # relationships
    social_identities: Mapped[list[SocialIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    oauth_tokens: Mapped[list[OAuthToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    diaries: Mapped[list[Diary]] = relationship(
        back_populates="owner", foreign_keys="Diary.owner_user_id", cascade="all, delete-orphan"
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    notification_preferences: Mapped[NotificationPreferences | None] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    photos: Mapped[list[Photo]] = relationship(back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "subscription_tier IN ('free','tier1','tier2')",
            name="ck_users_subscription_tier",
        ),
    )


class SocialIdentity(TimestampMixin, Base):
    __tablename__ = "social_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    relay_email: Mapped[str | None] = mapped_column(String(320))

    user: Mapped[User] = relationship(back_populates="social_identities")

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_social_identities_provider_sub"),
        CheckConstraint(
            "provider IN ('google','facebook','apple')", name="ck_social_identities_provider"
        ),
    )


class OAuthToken(TimestampMixin, Base):
    __tablename__ = "oauth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    access_token_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    scopes_granted: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="oauth_tokens")

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_oauth_tokens_user_provider"),
        CheckConstraint("provider IN ('google','spotify')", name="ck_oauth_tokens_provider"),
    )


# ---------------------------------------------------------------------------
# Diaries
# ---------------------------------------------------------------------------


class Diary(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "diaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    subject_name: Mapped[str | None] = mapped_column(Text)
    subject_relation: Mapped[str] = mapped_column(String(20), nullable=False, server_default="self")
    voice_override: Mapped[str | None] = mapped_column(String(20))
    tone_hint: Mapped[str] = mapped_column(Text, nullable=False, server_default="warm, narrative")
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    scan_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    cover_photo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("photos.id", ondelete="SET NULL")
    )
    notifications_muted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    photos_backfill_days_max: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="90"
    )
    hard_delete_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship(back_populates="diaries", foreign_keys=[owner_user_id])
    entries: Mapped[list[Entry]] = relationship(
        back_populates="diary", cascade="all, delete-orphan"
    )
    permissions: Mapped[list[DiaryPermission]] = relationship(
        back_populates="diary", cascade="all, delete-orphan"
    )
    invitations: Mapped[list[Invitation]] = relationship(
        back_populates="diary", cascade="all, delete-orphan"
    )
    scan_job: Mapped[ScanJob | None] = relationship(
        back_populates="diary", cascade="all, delete-orphan", uselist=False
    )
    scan_runs: Mapped[list[ScanRun]] = relationship(
        back_populates="diary", cascade="all, delete-orphan"
    )
    calendar_filters: Mapped[list[DiaryCalendarFilter]] = relationship(
        back_populates="diary", cascade="all, delete-orphan"
    )
    backfill_runs: Mapped[list[BackfillRun]] = relationship(
        back_populates="diary", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("owner_user_id", "slug", name="uq_diaries_owner_slug"),
        CheckConstraint(
            "subject_relation IN ('self','child','family','other_person')",
            name="ck_diaries_subject_relation",
        ),
        CheckConstraint(
            "voice_override IS NULL OR voice_override IN ('first_singular','first_plural','second','third')",
            name="ck_diaries_voice_override",
        ),
    )


class DiaryPermission(TimestampMixin, Base):
    __tablename__ = "diary_permissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    notifications_muted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    diary: Mapped[Diary] = relationship(back_populates="permissions")
    user: Mapped[User] = relationship()

    __table_args__ = (
        UniqueConstraint("diary_id", "user_id", name="uq_diary_permissions_diary_user"),
        CheckConstraint("role IN ('viewer','editor')", name="ck_diary_permissions_role"),
    )


class Invitation(TimestampMixin, Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False
    )
    invited_email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    diary: Mapped[Diary] = relationship(back_populates="invitations")

    __table_args__ = (CheckConstraint("role IN ('viewer','editor')", name="ck_invitations_role"),)


# ---------------------------------------------------------------------------
# Entries & events
# ---------------------------------------------------------------------------


class Entry(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_end_date: Mapped[date | None] = mapped_column(Date)
    parent_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(Text)
    body_markdown: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="draft")
    created_by: Mapped[str] = mapped_column(String(10), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    diary: Mapped[Diary] = relationship(back_populates="entries")
    events: Mapped[list[Event]] = relationship(back_populates="entry", cascade="all, delete-orphan")
    entry_photos: Mapped[list[EntryPhoto]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )
    enrichments: Mapped[list[Enrichment]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )
    llm_generations: Mapped[list[LLMGeneration]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )
    edit_diffs: Mapped[list[EntryEditDiff]] = relationship(
        back_populates="entry", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_entries_diary_entry_date", "diary_id", "entry_date"),
        CheckConstraint("status IN ('draft','published')", name="ck_entries_status"),
        CheckConstraint("created_by IN ('auto','manual')", name="ck_entries_created_by"),
    )


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    entry: Mapped[Entry] = relationship(back_populates="events")

    __table_args__ = (
        Index(
            "ix_events_source_external_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        CheckConstraint(
            "source IN ('google_calendar','google_photos','manual','spotify')",
            name="ck_events_source",
        ),
    )


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------


class Photo(TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    s3_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Text)
    bytes: Mapped[int | None] = mapped_column(BigInteger)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float | None] = mapped_column(Numeric(9, 6))
    lon: Mapped[float | None] = mapped_column(Numeric(9, 6))
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str | None] = mapped_column(Text)
    thumbnail_s3_key: Mapped[str | None] = mapped_column(Text)
    ai_description: Mapped[str | None] = mapped_column(Text)
    dek_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="photos")
    entry_photos: Mapped[list[EntryPhoto]] = relationship(back_populates="photo")

    __table_args__ = (
        Index(
            "ix_photos_source_external_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        CheckConstraint("source IN ('google_photos','upload')", name="ck_photos_source"),
    )


class DiaryPhoto(Base):
    __tablename__ = "diary_photos"

    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), primary_key=True
    )
    photo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )


class EntryPhoto(Base):
    __tablename__ = "entry_photos"

    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), primary_key=True
    )
    photo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int | None] = mapped_column(Integer)

    entry: Mapped[Entry] = relationship(back_populates="entry_photos")
    photo: Mapped[Photo] = relationship(back_populates="entry_photos")


# ---------------------------------------------------------------------------
# Enrichments & LLM
# ---------------------------------------------------------------------------


class Enrichment(TimestampMixin, Base):
    __tablename__ = "enrichments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    captured_for_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entry: Mapped[Entry] = relationship(back_populates="enrichments")

    __table_args__ = (UniqueConstraint("entry_id", "kind", name="uq_enrichments_entry_kind"),)


class LLMGeneration(TimestampMixin, Base):
    __tablename__ = "llm_generations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    entry: Mapped[Entry] = relationship(back_populates="llm_generations")

    __table_args__ = (
        CheckConstraint("status IN ('success','failed')", name="ck_llm_generations_status"),
    )


class EntryEditDiff(Base):
    __tablename__ = "entry_edit_diffs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    llm_generation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_generations.id", ondelete="CASCADE"), nullable=False
    )
    body_before_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    body_after_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entry: Mapped[Entry] = relationship(back_populates="edit_diffs")


# ---------------------------------------------------------------------------
# Scan jobs & runs
# ---------------------------------------------------------------------------


class ScanJob(TimestampMixin, Base):
    __tablename__ = "scan_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("diaries.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    last_scan_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_status: Mapped[str | None] = mapped_column(String(10))
    last_calendar_cursor: Mapped[str | None] = mapped_column(Text)
    last_photos_cursor: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_scan_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    diary: Mapped[Diary] = relationship(back_populates="scan_job")

    __table_args__ = (
        CheckConstraint(
            "last_scan_status IS NULL OR last_scan_status IN ('success','partial','failed')",
            name="ck_scan_jobs_last_status",
        ),
    )


class ScanRun(TimestampMixin, Base):
    __tablename__ = "scan_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False
    )
    triggered_by: Mapped[str] = mapped_column(String(10), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    events_calendar: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    events_photos: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    entries_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    entries_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    llm_calls_made: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    errors: Mapped[dict | None] = mapped_column(JSONB)

    diary: Mapped[Diary] = relationship(back_populates="scan_runs")

    __table_args__ = (
        CheckConstraint(
            "triggered_by IN ('beat','manual','backfill','admin')", name="ck_scan_runs_triggered_by"
        ),
        CheckConstraint(
            "status IN ('running','success','partial','failed')", name="ck_scan_runs_status"
        ),
    )


class BackfillRun(TimestampMixin, Base):
    __tablename__ = "backfill_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False
    )
    from_date: Mapped[date] = mapped_column(Date, nullable=False)
    to_date: Mapped[date] = mapped_column(Date, nullable=False)
    sources: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    events_ingested: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    entries_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    diary: Mapped[Diary] = relationship(back_populates="backfill_runs")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_backfill_runs_status",
        ),
    )


class DiaryCalendarFilter(TimestampMixin, Base):
    __tablename__ = "diary_calendar_filters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("diaries.id", ondelete="CASCADE"), nullable=False
    )
    google_calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    diary: Mapped[Diary] = relationship(back_populates="calendar_filters")

    __table_args__ = (
        UniqueConstraint(
            "diary_id", "google_calendar_id", name="uq_diary_calendar_filters_diary_gcal"
        ),
    )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class NotificationPreferences(TimestampMixin, Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    expo_push_tokens: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    quiet_hours_start: Mapped[time] = mapped_column(Time, nullable=False, server_default="20:00")
    quiet_hours_end: Mapped[time] = mapped_column(Time, nullable=False, server_default="07:00")
    timezone: Mapped[str | None] = mapped_column(Text)
    kinds_disabled: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    email_digest_only: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    user: Mapped[User] = relationship(back_populates="notification_preferences")


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, server_default="normal")
    channel_push_status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="pending"
    )
    channel_email_status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="pending"
    )
    channel_inapp_status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="pending"
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="notifications")

    __table_args__ = (
        CheckConstraint(
            "kind IN ('draft_ready','draft_failed','integration_revoked','entry_published','tier_limit','invite_received','deletion_grace')",
            name="ck_notifications_kind",
        ),
        CheckConstraint("priority IN ('normal','high')", name="ck_notifications_priority"),
    )


# ---------------------------------------------------------------------------
# Auth tokens
# ---------------------------------------------------------------------------


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_magic_link_tokens_email_expires", "email", "expires_at"),)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    device_hint: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (Index("ix_refresh_tokens_family_id", "family_id"),)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meta: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_log_user_id", "user_id"),
        Index("ix_audit_log_created_at", "created_at"),
    )
