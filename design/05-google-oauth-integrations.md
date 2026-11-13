# Google OAuth + Calendar + Photos Integration

> **Google Photos changed on 31 March 2025.** The full-library scope `photoslibrary.readonly` was removed; apps can only read photos they created (<https://developers.google.com/photos/support/updates>). The Photos grant for scanning and the scan-time photo filtering below **cannot be built**. Instead, the Photos grant now uses the **Picker API** scope, and the parent picks photos each time (see "Photos Picker (item 14)" below). Automatic photos move to the mobile app's camera roll: see [`11-mobile-app.md`](11-mobile-app.md) M-1. Decision: Andrew, 2026-11-13.

## Three flows, separated

OAuth scopes for login vs Calendar vs Photos are separated. Login uses minimum scopes; Calendar and Photos are requested only when the user opts into the integration on a diary.

| Flow | When | Scopes |
|---|---|---|
| **Login** | Sign-in / sign-up | `openid email profile` |
| **Calendar grant** | Per diary, when user enables Calendar integration | `calendar.readonly` |
| **Photos grant** | When the parent first uses "Add from Google Photos" | `photospicker.mediaitems.readonly` (the old `photoslibrary.readonly` was removed by Google 2025-03-31; migration 0014 drops that string from stored grants) |

Combined grant supported when the user enables both at once — single consent screen with both scopes. User can still partial-grant by unchecking one on the consent screen.

## Login providers (PoC)

All login providers covered for PoC:

| Provider | Identity flow | Backend endpoint |
|---|---|---|
| **Email + password** | Standard | `/v1/auth/register`, `/v1/auth/login` |
| **Google** | Frontend gets ID token via Google Identity Services / `expo-auth-session`; backend verifies against Google JWKS | `POST /v1/auth/social/google` |
| **Facebook** | ID token verification only (no Graph API data used) | `POST /v1/auth/social/facebook` |
| **Apple** | Sign In with Apple ID token; backend verifies against Apple JWKS. **Required for iOS App Store** (Guideline 4.8). | `POST /v1/auth/social/apple` |
| **Magic link email** | User enters email; backend sends signed link with 15-min token | `POST /v1/auth/magic-link/request`, `GET /v1/auth/magic-link/consume?token=...` |

Account linking is **not automatic** when an existing user matches by email. The login returns a `link_required` signal; the frontend must prompt the user to confirm ownership (existing-credentials login OR a verification email click-through) before `POST /v1/auth/social/{provider}/link` performs the merge. Without this step, a new Google account for an email already registered with a password allows an attacker to take over the existing account.

**Apple identities** are keyed by `sub` (the durable `provider_user_id`), never by email. Apple Private Relay rotates relay addresses, and users can revoke them entirely — email is not a stable join key. The `social_identities.relay_email` column stores the relay address for reference only. The system accepts the multi-account-per-Apple-user reality rather than risking email-based takeover.

No `oauth_tokens` row is created for login flows — login is a one-time identity check; no API calls are made on the user's behalf.

## Calendar/Photos grant flow

```
1. User clicks "Connect Google Calendar/Photos" in diary settings.
2. Frontend calls GET /v1/integrations/google/authorize?scopes=calendar,photos
3. Backend returns Google OAuth URL with:
   - response_type=code, access_type=offline, prompt=consent
   - include_granted_scopes=true
   - scope=openid email profile <requested API scopes>
   - state=signed_token (HMAC-signed: user_id + diary_id + nonce)
   - redirect_uri=https://api.diary.perfectday.andrewlass.com/v1/integrations/google/callback
4. User grants/denies/partial-grants on Google's consent screen.
5. Google redirects back with ?code= and ?scope= (actual granted scopes).
6. Backend:
   - Verifies state HMAC + nonce (Redis-stored, 10-min TTL, single-use).
   - Exchanges code for access_token + refresh_token.
   - Encrypts tokens (AES-256-GCM, app-level master key).
   - Upserts oauth_tokens row with scopes_granted parsed from response.
   - Redirects to web app:
     - ?google=connected         (all requested scopes granted)
     - ?google=partial&missing=photos   (partial grant)
     - ?google=denied            (full denial)
     - ?google=cancelled         (Cancel pressed on Google's screen)
     - ?google=wrong_account&expected=<email>   (a different Google account)
     - ?google=failed&reason=browser_mismatch|nonce_reuse|token_exchange_failed
```

## Photos Picker (item 14)

Used by the entry page's "Add photos" panel. Google's picker shows the parent's library; the parent chooses; we copy only those photos. We never see the rest of the library.

**Scope.** `https://www.googleapis.com/auth/photospicker.mediaitems.readonly`, stored in `scopes_granted` as `photospicker.mediaitems.readonly`. Scopes are unioned with what the user already granted, so adding Photos never removes Calendar.

**Return path.** `authorize` takes `return_to` (a same-site path; anything else falls back to the diaries page). It is carried in the HMAC-signed state, and the callback redirects back to it. The parent lands on the entry they started from.

**One Google account.** `authorize` adds `login_hint` with the connected account's email. If the parent consents with a different account, nothing is saved and the redirect is `?google=wrong_account&expected=<email>`. A stored NULL email (older rows) accepts the consenting account and records it.

**Calendar state.** `calendar_state()` (`services/calendar_connection.py`) is `none` when there is no connection or it lacks `calendar.readonly` (for example photos only), `lost` when the calendar connection is revoked, otherwise `connected`. A photos-only grant does not count as a calendar connection.

**S-5: the connect is tied to the browser.** When `authorize` issues the URL it sets the cookie `pd_google_connect` = sha256(nonce): HttpOnly, Secure, SameSite=Lax, Path `/v1/integrations/google/callback`, Max-Age 600. The callback checks the cookie against the nonce in the state before it consumes the nonce, and refuses a missing or different one (`reason=browser_mismatch`). The cookie is cleared on every outcome. A link sent to someone else therefore cannot attach their Google to the sender's account. Because the cookie is always Secure, a non-localhost plain-http origin cannot complete the connect (see `docs/local-dev.md`).

**S-8: disconnect revokes at Google.** `DELETE /v1/integrations/google` POSTs the refresh token (else the access token) to `https://oauth2.googleapis.com/revoke`, then deletes the token row whatever Google answered. A failed revoke is logged without the token.

**Import flow.** `POST /v1/entries/{id}/photo-imports` creates a picker session at Google and returns `picker_uri`; the web app sends the parent there. A Celery task polls the session, copies the picked photos in, and attaches them to the entry. Statuses and endpoints: [`03-api-surface.md`](03-api-surface.md) § Photos. If Google rejects the stored token, the import ends as `needs_reconnect`.

## Token storage

One `oauth_tokens` row per `(user_id, provider='google')`. Google issues a single refresh token covering all granted scopes. `scopes_granted text[]` is the source of truth for what's authorized.

| Item | Storage | Encrypted |
|---|---|---|
| Access token | `oauth_tokens.access_token_ciphertext` | Yes (AES-256-GCM) |
| Refresh token | `oauth_tokens.refresh_token_ciphertext` | Yes (AES-256-GCM) |
| Scopes granted | `oauth_tokens.scopes_granted` text[] | No |
| Master encryption key | Env var (PoC); KMS later | N/A |
| OAuth state nonces | Redis with 10-min TTL | No |

Key rotation supported via key version prefix in ciphertext blob. OAuth tokens use a separate master secret env var from photo encryption.

## Partial-grant handling

| Scenario | Behavior |
|---|---|
| Calendar granted, Photos denied | Diary functions on calendar only. UI shows "Grant Photos access" CTA. Worker checks scopes before each Photos API call; skips silently if absent. |
| Photos granted, Calendar denied | Symmetric. Manual entries still work; photos can attach. Auto photo-only entries are v1.1. |
| User revokes one scope in Google account settings | Worker hits 401/403 → updates `scopes_granted` (removes that scope), fires "scope revoked" notification with reconnect deep link. Google is source of truth. |
| User revokes app entirely from Google | All API calls 401. Worker sets `oauth_tokens.revoked_at`, stops scanning, fires "Google integration revoked" notification. Diary stays alive (manual entries OK). |

## Worker call pattern

```python
def fetch_calendar_for_diary(diary):
    user = diary.owner
    token = oauth_tokens.get(user.id, "google")

    if not token or token.revoked_at:
        skip("no google token")
        return

    if "calendar.readonly" not in token.scopes_granted:
        skip("calendar scope not granted")
        return

    access_token = ensure_fresh_access_token(token)  # refresh if expired
    # call Google API; on 401/403, update scopes_granted or mark revoked
```

`ensure_fresh_access_token` checks `expires_at`, refreshes via refresh token if expired, persists new access token. Refresh failure → mark `revoked_at` and stop.

## Calendar selection (per-diary)

PoC default: **primary calendar only.** Diary settings UI lists all calendars from Google with checkboxes. Additional calendars stored in `diary_calendar_filters` (one row per diary + calendar_id).

## Photos filtering (per-diary)

**Superseded (2026-11-13):** depends on the removed scope; see the note at the top.

**No album selection — date-range filtering only.** Worker pulls photos taken within ±3 days of any Entry's date range.

Privacy/cost mitigations:

1. **Metadata-first fetch.** Worker fetches Google Photos *metadata* first (no image bytes). Filters by:
   - `taken_at` in scan window
   - has location OR taken with rear camera (skips screenshots, webcam selfies)
   - `mime_type` is image (skips video for PoC)
2. **Backfill cap.** Initial backfill bounded by `diaries.photos_backfill_days_max` (default 90 days).

## Spotify

Skipped for PoC. Stub endpoints present in the API surface for future plumbing; no implementation.

## Quotas and rate limits

| API | Limit | Notes |
|---|---|---|
| Calendar | ~1M queries/day per project, 600 reads/min/user | Generous for PoC |
| Photos | 10K queries/day per project, 30/sec/user | **Real bottleneck** — metadata-first filter mitigates |
| Worker on 429 | Exponential backoff (use `Retry-After` header if present, else 1s/4s/16s) | |
| Repeated 429 | Mark scan run `partial`, set `next_scan_after` to back off | |
| App verification | Unverified OK for PoC. Hard cap: 100 users. Verification ($15K–$75K, months) deferred to public launch. | Start process early |

## Google Cloud project structure

Separate dev and prod projects from day one. Avoids dev calls eating prod quota; cleaner credential management.

## Decisions locked

- Calendar selection: primary by default; per-diary checkbox UI via `diary_calendar_filters`.
- ~~Photos: date-range filter only; metadata-first; 90-day default backfill cap.~~ Superseded 2026-11-13: Google removed library access.
- Facebook: login only. No `oauth_tokens` row.
- Spotify: skipped. Stubs only.
- Separate dev/prod Google Cloud projects from day one.
- App stays unverified for PoC; 100-user cap acceptable.
- Login providers: email+password, Google, Facebook, Apple (required for iOS), magic link.
