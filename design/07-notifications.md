# Notification Design

## Notification events (PoC)

| Kind | Trigger | Channels | Priority |
|---|---|---|---|
| `draft_ready` | Scan generates new draft entry/entries | Push + Email | Normal |
| `draft_failed` | LLM generation failed for an entry | Push + Email | Normal |
| `integration_revoked` | Google scope removed or app revoked | Push + Email | High |
| `entry_published` | Owner publishes a draft (notifies shared users only) | Push + Email | Normal |
| `invite_received` | Diary invite sent to user | Email always + Push if app installed | Normal |
| `tier_limit` | Auto-generation skipped due to tier | Push | Normal |
| `deletion_grace` | Day 6/28 reminder before hard delete | Email only, at 09:00 user-local | High |

High-priority notifications bypass quiet hours. Normal notifications respect them.

## Channels

### Expo Push (primary mobile channel)

- Token registered via `POST /v1/notifications/devices`.
- Stored in `notification_preferences.expo_push_tokens text[]` (multi-device).
- Delivered via Expo Push API (`https://exp.host/--/api/v2/push/send`). Expo handles APNs (iOS) + FCM (Android).
- `DeviceNotRegistered` response → remove that token immediately.
- 5xx → retry with backoff (3 attempts).

### Email via SendGrid

- Provider-configurable in settings — swapping later is a config change, not code.
- Templates: Jinja2, code-based for PoC. Plaintext + HTML multipart.
- Draft-ready email: subject "A new diary draft is ready for [Diary Name]", brief title preview, "Review draft" CTA only — no full body text in email (privacy + engagement reasons).
- Every email includes unsubscribe link per channel + per kind (CAN-SPAM compliance).
- Bounce/complaint webhooks from SendGrid: hard bounce → disable email for that user after 3 hard bounces in 30 days.
- Free tier: 100 emails/day — sufficient for PoC scale.

### In-app feed (all users)

- `notifications` table always written, regardless of push/email status.
- `GET /v1/notifications` reads it. Fallback when external channels are off or fail.

## Default preferences (new user)

| Setting | Default | Rationale |
|---|---|---|
| `push_enabled` | `true` | Immediate value delivery. |
| `email_enabled` | `true` | Catches web-only users without mobile app. |
| `quiet_hours_start` | `20:00` (user TZ) | Default night window. |
| `quiet_hours_end` | `07:00` (user TZ) | |
| `timezone` | Inferred from browser/device at signup | |
| `kinds_disabled` | `[]` | All kinds active. |
| `email_digest_only` | `false` | Per-event by default; user can switch to daily 09:00 digest. |

## Per-diary mute

- Owner: `diaries.notifications_muted` — mutes all notifications for that diary.
- Shared user: `diary_permissions.notifications_muted` — mutes that diary's notifications for the shared user only.
- Dispatcher checks the relevant field before sending.

## Notification dispatcher

```
notify(user, kind, payload, priority='normal'):
  prefs = notification_preferences(user.id)

  if kind in prefs.kinds_disabled:
    write_inapp(kind='skipped'); return

  if priority == 'normal' and now_in_quiet_hours(user, prefs):
    eta = next_active_window_start(user, prefs)
    enqueue this task at eta
    return

  # Coalesce: batch same-kind notifications within 5-min window
  # Push AND email both coalesce together (one batch per window, not separate)
  pending = unsent notifications for user, kind, within last 5 min
  if len(pending) > 1:
    payload = batch(payload, pending)  # merge entry_ids etc.

  if prefs.push_enabled and not diary_muted(user, payload):
    send_expo_push(user, kind, payload)

  if prefs.email_enabled and not diary_muted(user, payload):
    if prefs.email_digest_only:
      schedule_for_daily_digest(user, kind, payload)
    else:
      send_email(user, kind, payload)

  write_inapp_notification(user, kind, payload)
```

## Coalescing (critical for backfill UX)

Without coalescing, a 50-entry backfill generates 50 push notifications. Mitigation:
- 5-minute coalescing window per (user, kind).
- Max one push + one email per window. Both channels coalesce together (not independently).
- `email_digest_only=true` batches to a single daily email at 09:00.

**Coalescing is atomic.** The dispatcher acquires a Redis key `coalesce:{user_id}:{kind}` using `SETNX` (set-if-not-exists) with a 5-minute TTL. If `SETNX` returns 0 (key already held), the incoming notification is appended to the pending payload instead of triggering a new send. The first setter is the designated sender for that window — it reads the accumulated payload and dispatches at window close. This replaces the previous read-then-write approach, which had a race condition where two concurrent notifications could both see an empty window and send independently.

## Quiet hours — release storm mitigation

When a batch of notifications are deferred until `quiet_hours_end`, they all become eligible at the same moment. To prevent a burst of simultaneous sends:

- At dispatch time, add a random jitter of 0–15 minutes to the `eta` of each notification in the quiet-hours queue: `eta = quiet_hours_end + random(0, 900 seconds)`.
- At the `eta` fire time, re-check the coalescing key. If another notification in the same `(user, kind)` window already fired, add this notification's payload to the in-flight batch instead.

This spreads the post-quiet-hours send over a 15-minute window and gives coalescing a second bite at batching.

## Notification payload schemas

Each `notifications.payload` jsonb field has a fixed shape per `kind`:

| Kind | Payload fields |
|---|---|
| `draft_ready` | `{diary_id, diary_name, entry_ids: [uuid], entry_dates: [date], count: int}` |
| `draft_failed` | `{diary_id, diary_name, entry_id, entry_date, error_summary: string}` |
| `integration_revoked` | `{provider: "google", diary_ids: [uuid], scope: string}` |
| `entry_published` | `{diary_id, diary_name, entry_id, entry_date, entry_title: string}` |
| `invite_received` | `{diary_id, diary_name, inviter_display_name: string, role: "viewer|editor", invitation_token: string}` |
| `tier_limit` | `{diary_id, limit_type: "diary_count|entry_count", current: int, limit: int, required_tier: string}` |
| `deletion_grace` | `{target_type: "account|diary", target_id: uuid, hard_delete_after: datetime, days_remaining: int}` |

Payload is stored in the `notifications` row and also passed to push/email channels. Email and push templates consume the same payload struct.

When owner calls `POST /v1/entries/{id}/publish`:
- Fires `entry_published` to all users with `diary_permissions` rows for that diary (editors + viewers — not the owner).
- Respects each recipient's `diary_permissions.notifications_muted` and their global notification preferences.
- Payload: diary name, entry title, entry date.

## Failure handling

| Failure | Behavior |
|---|---|
| `DeviceNotRegistered` from Expo | Remove token from `expo_push_tokens`. |
| Expo 5xx | Retry backoff (3 attempts). Set `channel_push_status='failed'`. |
| SendGrid hard bounce | Increment bounce counter. After 3 hard bounces in 30 days, set `email_enabled=false` and notify via push. |
| User has no push tokens AND email disabled | In-app feed only. |
| All channels fail | Notification still in `notifications` table; visible on next app open. |

## Mobile-specific notes

- iOS and Android (12+) require explicit permission request in the Expo app for push.
- Backend handles "app installed, permission not granted" gracefully — falls through to email or in-app.
- Background refresh on notification arrival is out of PoC scope; app fetches fresh data on open.
