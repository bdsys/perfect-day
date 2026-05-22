# Data Model

PoC-grade Postgres schema. All tables have `created_at`, `updated_at` (omitted from listings below). UUID primary keys unless noted. Soft delete via `deleted_at` is the default; hard delete is a background job triggered by user-initiated account/diary deletion (see [08-security-privacy.md](08-security-privacy.md)).

## Entity diagram

```
users ──┬──< social_identities
        ├──< oauth_tokens
        ├──< photos ──< diary_photos >── diaries
        ├──< notification_preferences
        └──< diaries ──┬──< diary_permissions >── users
                       ├──< invitations
                       ├──< scan_jobs (1:1)
                       └──< entries ──┬──< events
                                      ├──< entry_photos >── photos
                                      ├──< enrichments
                                      ├──< llm_generations
                                      └──< entries (parent_entry_id, future use)
```

## Tables

### `users`
- `id` UUID PK
- `email` citext UNIQUE NOT NULL
- `email_verified_at` timestamptz NULL
- `password_hash` text NULL  *(NULL when only social login is used)*
- `display_name` text
- `subscription_tier` text NOT NULL DEFAULT `'free'`  *(`free | tier1 | tier2`)*
- `stripe_customer_id` text NULL  *(placeholder, not used in PoC)*
- `is_admin` boolean NOT NULL DEFAULT false
- `deleted_at` timestamptz NULL
- `hard_delete_after` timestamptz NULL  *(set on account deletion request; 7-day grace)*

### `social_identities`
- `id` UUID PK
- `user_id` UUID FK → users
- `provider` text NOT NULL  *(`google | facebook | apple`)*
- `provider_user_id` text NOT NULL  *(Apple: durable `sub` value — not email)*
- `relay_email` citext NULL  *(Apple Private Relay address; stored for reference only; never used as a lookup key)*
- UNIQUE (`provider`, `provider_user_id`)

### `oauth_tokens`
*(per user, per provider — partial-grant aware via `scopes_granted`)*
- `id` UUID PK
- `user_id` UUID FK → users
- `provider` text NOT NULL  *(`google | spotify`)*  *(Facebook login does not create a row)*
- `access_token_ciphertext` bytea NOT NULL  *(AES-256-GCM, app-level)*
- `refresh_token_ciphertext` bytea NULL
- `scopes_granted` text[] NOT NULL  *(e.g., `['calendar.readonly', 'photoslibrary.readonly']`)*
- `expires_at` timestamptz NULL
- `revoked_at` timestamptz NULL
- UNIQUE (`user_id`, `provider`)

### `diaries`
- `id` UUID PK
- `owner_user_id` UUID FK → users
- `name` text NOT NULL
- `slug` text UNIQUE NOT NULL  *(derived from name; used in display URLs)*
- `subject_name` text NULL  *(e.g., child's name; used in LLM prompt)*
- `subject_relation` text NOT NULL DEFAULT `'self'`  *(CHECK: `self | child | family | other_person`)*
- `voice_override` text NULL  *(CHECK: `first_singular | first_plural | second | third`; overrides derivation)*
- `tone_hint` text NOT NULL DEFAULT `'warm, narrative'`
- `timezone` text NOT NULL  *(IANA tz — all `entry_date` values in this diary are interpreted in this timezone; see [06-scan-worker.md](06-scan-worker.md) § Time and timezones)*
- `scan_interval_minutes` int NOT NULL DEFAULT 60
- `scan_enabled` boolean NOT NULL DEFAULT true
- `cover_photo_id` UUID FK → photos NULL
- `notifications_muted` boolean NOT NULL DEFAULT false
- `photos_backfill_days_max` int NOT NULL DEFAULT 90
- `deleted_at` timestamptz NULL
- `hard_delete_after` timestamptz NULL  *(set on diary deletion request; 30-day grace)*

App-layer check: per-user diary count vs tier (free=1, tier1=2, tier2=4).

Display URLs: `diary.perfectday.bdsys.net/d/{slug}/entries/{entry_date}`. Internal IDs remain UUIDs.

### `diary_permissions`
- `id` UUID PK
- `diary_id` UUID FK → diaries
- `user_id` UUID FK → users
- `role` text NOT NULL  *(`viewer | editor`)*  *(owner is implied by `diaries.owner_user_id`)*
- `notifications_muted` boolean NOT NULL DEFAULT false
- UNIQUE (`diary_id`, `user_id`)

### `invitations`
- `id` UUID PK
- `diary_id` UUID FK → diaries
- `invited_email` citext NOT NULL
- `role` text NOT NULL  *(`viewer | editor`)*
- `token` text NOT NULL UNIQUE  *(opaque, single-use)*
- `expires_at` timestamptz NOT NULL
- `accepted_at` timestamptz NULL
- `accepted_by_user_id` UUID FK → users NULL

### `entries`
- `id` UUID PK
- `diary_id` UUID FK → diaries
- `entry_date` date NOT NULL  *(start date, interpreted in `diaries.timezone` — never in server/UTC time)*
- `entry_end_date` date NULL  *(NULL = single-day; non-null = multi-day span)*
- `parent_entry_id` UUID FK → entries NULL  *(reserved; not used in PoC — supports future nesting/merge UX)*
- `title` text
- `body_markdown` text  *(narrative)*
- `status` text NOT NULL  *(`draft | published | archived`)*
- `created_by` text NOT NULL  *(`auto | manual`)*
- `published_at` timestamptz NULL
- `deleted_at` timestamptz NULL

INDEX on (`diary_id`, `entry_date` DESC) for timeline. Timeline order: `entry_date` DESC, then `created_at` DESC. Multi-day entries display under their `entry_date` with the range.

### `events`
*(raw source data tied to an entry — what the LLM prompt is built from)*
- `id` UUID PK
- `entry_id` UUID FK → entries
- `source` text NOT NULL  *(`google_calendar | google_photos | manual | weather | spotify`)*
- `external_id` text NULL  *(provider's id; for dedup)*
- `occurred_at` timestamptz NULL  *(used for ordering events within an entry)*
- `payload` jsonb NOT NULL  *(provider-specific normalized data)*
- UNIQUE (`source`, `external_id`) WHERE external_id IS NOT NULL

### `photos`
*(per-user library — read authorization rules are defined in [03-api-surface.md](03-api-surface.md) § Photo authorization)*
- `id` UUID PK
- `user_id` UUID FK → users  *(photo belongs to the user, not a specific diary)*
- `s3_key` text NOT NULL UNIQUE  *(MinIO object key — ciphertext stored at this key)*
- `mime_type` text
- `bytes` bigint
- `taken_at` timestamptz NULL  *(EXIF)*
- `lat` numeric(9,6) NULL
- `lon` numeric(9,6) NULL
- `source` text NOT NULL  *(`google_photos | upload`)*
- `external_id` text NULL  *(Google Photos media item id)*
- `thumbnail_s3_key` text NULL
- `ai_description` text NULL  *(reserved for future vision LLM L3; unused in PoC)*
- `dek_ciphertext` bytea NULL  *(wrapped DEK for AES-GCM photo encryption; see security doc)*
- `finalized_at` timestamptz NULL  *(set after upload confirmed; orphan sweeper uses this)*
- `deleted_at` timestamptz NULL
- UNIQUE (`source`, `external_id`) WHERE external_id IS NOT NULL

### `diary_photos`
*(many-to-many — diary-level photo membership before entry attachment)*
- `diary_id` UUID FK → diaries
- `photo_id` UUID FK → photos
- PK (`diary_id`, `photo_id`)

### `entry_photos`
*(many-to-many; one photo can appear in multiple entries)*
- `entry_id` UUID FK → entries
- `photo_id` UUID FK → photos
- `position` int  *(display order; worker sets from `taken_at`)*
- PK (`entry_id`, `photo_id`)

### `enrichments`
- `id` UUID PK
- `entry_id` UUID FK → entries
- `kind` text NOT NULL  *(`weather | music | location | …`)*
- `payload` jsonb NOT NULL  *(shape depends on kind)*
- `source` text  *(`open_meteo | spotify | google_photos_exif`)*
- `captured_for_at` timestamptz NULL  *(the moment the enrichment describes)*
- `fetched_at` timestamptz NOT NULL
- UNIQUE (`entry_id`, `kind`)  *(PoC: one row per kind; relax later for multi-day per-day weather)*

### `llm_generations`
- `id` UUID PK
- `entry_id` UUID FK → entries
- `model` text NOT NULL
- `prompt_hash` text NOT NULL
- `input_tokens` int
- `output_tokens` int
- `cost_usd` numeric(10,6) NULL
- `latency_ms` int
- `status` text NOT NULL  *(`success | failed`)*
- `error` text NULL

### `entry_edit_diffs`
*(captures user edits to LLM drafts on publish — future fine-tuning signal; no consumer in PoC)*
- `id` UUID PK
- `entry_id` UUID FK → entries
- `llm_generation_id` UUID FK → llm_generations
- `body_before_markdown` text NOT NULL
- `body_after_markdown` text NOT NULL
- `captured_at` timestamptz NOT NULL DEFAULT now()

### `scan_jobs`
*(1:1 with diaries; config + state)*
- `id` UUID PK
- `diary_id` UUID FK → diaries UNIQUE
- `last_scan_started_at` timestamptz NULL
- `last_scan_completed_at` timestamptz NULL
- `last_scan_status` text NULL  *(`success | partial | failed`)*
- `last_calendar_cursor` text NULL  *(Google syncToken)*
- `last_photos_cursor` text NULL
- `consecutive_failures` int NOT NULL DEFAULT 0
- `next_scan_after` timestamptz NULL  *(backoff control)*

### `scan_runs`
*(per-scan audit trail)*
- `id` UUID PK
- `diary_id` UUID FK → diaries
- `triggered_by` text NOT NULL  *(`beat | manual | backfill | admin`)*
- `started_at` timestamptz NOT NULL
- `completed_at` timestamptz NULL
- `status` text NOT NULL  *(`running | success | partial | failed`)*
- `events_calendar` int NOT NULL DEFAULT 0
- `events_photos` int NOT NULL DEFAULT 0
- `entries_created` int NOT NULL DEFAULT 0
- `entries_updated` int NOT NULL DEFAULT 0
- `llm_calls_made` int NOT NULL DEFAULT 0
- `errors` jsonb NULL  *(per-source error details)*

### `backfill_runs`
- `id` UUID PK
- `diary_id` UUID FK → diaries
- `from_date` date NOT NULL
- `to_date` date NOT NULL
- `sources` text[] NOT NULL
- `status` text NOT NULL  *(`pending | running | completed | failed | cancelled`)*
- `started_at` timestamptz NULL
- `completed_at` timestamptz NULL
- `error` text NULL
- `events_ingested` int NOT NULL DEFAULT 0
- `entries_created` int NOT NULL DEFAULT 0

### `diary_calendar_filters`
*(per-diary calendar selection; primary calendar used by default)*
- `id` UUID PK
- `diary_id` UUID FK → diaries
- `google_calendar_id` text NOT NULL
- `enabled` boolean NOT NULL DEFAULT true
- UNIQUE (`diary_id`, `google_calendar_id`)

### `notification_preferences`
*(1:1 with users)*
- `user_id` UUID PK FK → users
- `push_enabled` boolean NOT NULL DEFAULT true
- `email_enabled` boolean NOT NULL DEFAULT true
- `expo_push_tokens` text[] NOT NULL DEFAULT `'{}'`  *(multi-device)*
- `quiet_hours_start` time NOT NULL DEFAULT `'20:00'`  *(in user's timezone)*
- `quiet_hours_end` time NOT NULL DEFAULT `'07:00'`
- `timezone` text NULL  *(inferred from browser/device at signup)*
- `kinds_disabled` text[] NOT NULL DEFAULT `'{}'`  *(per-kind opt-out)*
- `email_digest_only` boolean NOT NULL DEFAULT false  *(batch to daily 09:00 when true)*

### `notifications`
*(coalescing, in-app feed, and delivery tracking)*
- `id` UUID PK
- `user_id` UUID FK → users
- `kind` text NOT NULL  *(`draft_ready | draft_failed | integration_revoked | entry_published | tier_limit | invite_received | deletion_grace`)*
- `payload` jsonb NOT NULL  *(entry_ids array, diary_id, error message, etc.)*
- `priority` text NOT NULL DEFAULT `'normal'`  *(`normal | high`)*
- `channel_push_status` text NOT NULL DEFAULT `'pending'`  *(`pending | sent | skipped | failed`)*
- `channel_email_status` text NOT NULL DEFAULT `'pending'`
- `channel_inapp_status` text NOT NULL DEFAULT `'pending'`  *(`pending | read | dismissed`)*
- `scheduled_for` timestamptz NULL  *(delayed delivery; set for quiet-hours deferrals)*
- `sent_at` timestamptz NULL
- `read_at` timestamptz NULL
- `created_at` timestamptz NOT NULL DEFAULT now()

### `magic_link_tokens`
- `id` UUID PK
- `email` citext NOT NULL
- `token_hash` text NOT NULL UNIQUE  *(token itself is sent in email; only hash stored)*
- `expires_at` timestamptz NOT NULL  *(15-minute TTL)*
- `consumed_at` timestamptz NULL  *(single-use)*
- `created_at` timestamptz NOT NULL DEFAULT now()
- INDEX on (`email`, `expires_at`) for rate limiting

### `refresh_tokens`
- `id` UUID PK
- `user_id` UUID FK → users
- `token_hash` text NOT NULL UNIQUE
- `family_id` UUID NOT NULL  *(all rotations of same token share this; used for theft detection)*
- `device_hint` text NULL  *(user-agent or device name)*
- `expires_at` timestamptz NOT NULL
- `revoked_at` timestamptz NULL
- `created_at` timestamptz NOT NULL DEFAULT now()

### `audit_log`
- `id` UUID PK
- `user_id` UUID NULL  *(nulled on account anonymization)*
- `action` text NOT NULL  *(`diary.delete | account.delete | permission.grant | …`)*
- `target_type` text
- `target_id` UUID
- `metadata` jsonb
- `created_at` timestamptz NOT NULL DEFAULT now()

---

## Behavior decisions locked

- **Sharing visibility:** Owner sees raw `events` + drafts + published. Editors see drafts + published. Viewers see published entries + their attached photos only. Enforced at API layer.
- **Photo ownership on account deletion:** Photos die with the owner. Shared-diary edge case documented as known; "transfer ownership before delete" UX deferred to v2.
- **Photo attribution heuristic (PoC):** Time + location only. Layered roadmap:
  - L1 (PoC): time + location heuristic — narrower entry range wins.
  - L2 (PoC if cheap, else v1.1): surface ambiguous attachments in draft review UI.
  - L3 (v1.1+): vision LLM on ambiguous photos only, gated by paid tier; cached in `photos.ai_description`.
- **Multi-day grouping (Model 3):** Multi-day calendar events become one entry; everything else is day-bounded. User can manually merge/nest in draft review later.
- **Soft/hard delete hybrid:**
  - Account: soft on request, hard delete after 7-day grace.
  - Diary: soft on request, hard delete after 30-day grace.
  - Single entries: soft indefinitely; recoverable from UI.
