# Profile screen — design (tracker rows 29 and 32)

Agreed with Andrew on 2026-11-17. The mockup is the "Profile icon picker" artifact
(https://claude.ai/artifact/DgcdkhwwBPY5gFtP7DcewG); Andrew: "I love that mockup". A copy of
its HTML is in `docs/superpowers/specs/assets/2026-11-17-profile-icons-mockup.html`; open it in a browser.
It holds the 16 icons' real Lucide SVG paths and the colour tokens.

## Why

- **Row 29.** The web app has no page about the account itself. The account endpoints exist,
  but no screen reaches them. Account restore is effectively broken: review finding C-14 says
  it's only reachable for 15 minutes.
- **Row 32.** Weather shows a bare "21°" in °C, so a US parent reads it as °F. The AI is
  handed raw °C values.
- **Andrew's additions:**
  - the screen is called **Profile**;
  - each person can upload a photo or pick a preset icon;
  - that icon shows at the top right of every screen.
- **Andrew's direction:** "This app needs a UI overhaul and this is a step in the right
  direction". The shared header is the first piece of row 39.

## Goals

- A **Profile** screen at `/profile` with these sections:
  - who you are and your plan;
  - your icon;
  - temperature unit;
  - the Google connection;
  - log out on all devices;
  - delete account.
- **One shared header on every signed-in screen.** "Perfect Day" sits on the left. The
  page's own back link sits beside it where the page has one. Your icon sits on the right
  and opens a menu with **Profile** and **Sign out**.
- **The icon** is one of these:
  - an uploaded photo;
  - one of 16 preset icons;
  - by default, the first letter of your email.
- **The temperature unit** is °F or °C, defaulting to **°F**. It's account-wide (Andrew: a
  diary would never want its own unit).
- **A deleted account can be restored by logging in** during its 7-day grace period
  (Andrew's choice). This fixes C-14.

## Non-goals

- The emailed restore link. That's backlog row 49 and waits until the app can send email.
- Notification preferences. Nothing sends notifications yet (row 20).
- Changing your email or password, or display names.
- A cropping tool for uploaded photos. They're centre-cropped (see "My calls").
- Diary sharing. It doesn't exist, so nobody else ever sees your icon.
- The rest of the UI overhaul (row 39). The header is built so row 39 can restyle it.

## Anti-goals

- **An uploaded photo stored or served less carefully than an entry photo.** It may well
  be a photo of the child.
- **The badge and the AI's text disagreeing about a temperature.** For example, the badge
  shows 70°F and the text says 69°F.
- **A deleted account that can still use the app,** or a restore screen that dead-ends.
- **Rewriting text people already have.** Existing entries keep whatever unit they were
  written in.

## Approaches (Andrew picked A and A on 2026-11-17)

| Decision | A (picked) | B (rejected) |
|---|---|---|
| **Shared header** | **One header component that every signed-in page renders in place of its own bar.** Each page passes its back link. | **The header in the root layout,** with back links moved into page bodies. Tidier, but it moves every page's back link, and the layout would have to know about signing in. That's row 39 scope. |
| **Uploaded photo storage** | **Like entry photos:** encrypted in MinIO and served through the API. Reuses the existing photo code, and gets the same care as child photos. | **Encrypted bytes in a database column.** Simpler for one small image, but it's a second home for sensitive pictures, outside the "photos live in MinIO" rule. |

## The design

### Data (one migration, 0020)

New columns on `users`:

| Column | Type | Notes |
|---|---|---|
| `temperature_unit` | `char(1)`, not null, server default `'F'`, check `IN ('F','C')` | Existing users get `F` |
| `avatar_icon` | text, null, check against the 16 names below | Null when not using a preset |
| `avatar_s3_key` | text, null | The encrypted object for an uploaded photo |
| `avatar_dek_ciphertext` | bytea, null | Its wrapped data key, as `photos.dek_ciphertext` |

- **At most one of `avatar_icon` and `avatar_s3_key` is set** (a check constraint). With
  neither set, the person gets their initial.
- **The 16 icon names, in display order:** `cat`, `dog`, `bird`, `fish`, `rabbit`,
  `squirrel`, `turtle`, `panda`, `snail`, `flower`, `sun`, `moon`, `rocket`, `rainbow`,
  `cake`, `paw-print`.
  - These are Lucide icon names. `lucide-react` is already a dependency; ISC licence.
  - Each icon has a fixed colour pair, as in the mockup. The colours are the builder's,
    taken from the mockup's tokens.

### API

- **`GET /v1/auth/me` gains these fields:**
  - `temperature_unit`;
  - `avatar`: `{"kind": "photo"}`, `{"kind": "icon", "name": "<name>"}`, or
    `{"kind": "initial"}`;
  - `deletion_scheduled_for`: the date `hard_delete_after` falls on, else null.

  It answers for an account in its grace period (see Restore).
- **`PATCH /v1/auth/me`** with any of these fields:
  - `temperature_unit`: `F` or `C`.
  - `avatar_icon`: one of the 16 names, or null for the initial. Choosing an icon or the
    initial deletes any uploaded photo object.
  - Anything else gets a 422.
- **`PUT /v1/auth/me/avatar`** takes the raw image body, like `POST /v1/photos`. It
  accepts the same image types and the same size limit (`services/photos.py`), and does
  the following:
  1. Decodes the image, applying its EXIF orientation, and drops all metadata, location
     included.
  2. Centre-crops it to a square and resizes it to **256 × 256**.
  3. Encodes it as JPEG.
  4. Encrypts it the way photo upload does: a data key wrapped for the user, the same
     AES-GCM helpers.
  5. Stores it at `{user_id}/avatar/<random>.jpg`, clears `avatar_icon`, and deletes the
     previous photo object if there was one.

  Account hard delete already scrubs everything under `{user_id}/` (`workers/hard_delete.py`).
- **`GET /v1/auth/me/avatar`** returns the decrypted JPEG to its owner only, with
  `Cache-Control: private, no-store`. It's a 404 when there's no uploaded photo.
- **`DELETE /v1/auth/me/avatar`** deletes the object and goes back to the initial.
- **`EntryOut` gains `temperature_unit`:** the diary owner's unit. It's filled wherever
  entries are returned, the same way the owner's `plan` is.

### Temperature

- **Storage stays in °C.** `workers/open_meteo.py` is unchanged.
- **One rounding rule everywhere.** °F = °C × 9/5 + 32, rounded **half up** to a whole
  number. The badge and the prompt must round identically, and Python's `round()` rounds
  half to even, so it must not be used.
- **The badge** (`components/WeatherBadge.tsx`) shows the unit: "61°F / 70°F" or
  "16°C / 21°C". It uses the entry's `temperature_unit`, so everyone looking at a diary
  sees the owner's unit (my call).
- **The AI prompt.** In each `[weather]` enrichment line, the AI gets converted, rounded
  values instead of the raw `*_c` keys: `temperature_max` and `temperature_min` plus
  `"temperature_unit": "°F"` (or `"°C"`). The fact check builds its source text from the
  same converted payload. Other enrichment kinds are unchanged.
- **Text already drafted keeps its unit.** Only new drafts use the setting.
- **Badge and old text can disagree, and that's accepted** (Andrew, 2026-11-17, option A).
  - An entry whose text was written with °C values shows a badge in the owner's current
    unit. In production on 2026-11-17 that was 2 of 31 entries: 5 had weather, and 2
    mentioned °C in the text. The same happens to older entries after a later switch.
  - The parent can edit the text, or press the AI button, which offers a version in the
    new unit as a suggestion.
  - Rejected: remembering each draft's unit (the badge would stop following the setting,
    and it needs a new field and drafting changes), and defaulting existing accounts to °C
    (contradicts "°F by default").
- **Half up for negative values too** (my call): `floor(x + 0.5)` in both Python and
  TypeScript, so −0.5 becomes 0 and −1.5 becomes −1 in both.
- **The prompt uses the symbol (`°F`), never the word "Fahrenheit".** The fact check flags
  capitalised words it can't find in the sources, so the symbol avoids that.

### Restore by logging in (fixes C-14)

The accounts affected here are inside their grace period: `deleted_at` is set **and**
`hard_delete_after` is set and in the future. One shared check defines this, and every
place below uses it.

- **Accounts blocked by the admin CLI stay blocked** (my call). `manage.py users delete`
  sets `deleted_at` with no `hard_delete_after`. Those accounts get 401
  `account_unavailable` everywhere, restore included.
- **This tightens the existing restore check.** `get_current_user_within_grace` today
  lets an empty `hard_delete_after` through.
- **The deadline is an exact moment, shown in the viewer's local time** (my call).
  - `/me` returns `deletion_scheduled_for` as the ISO timestamp `hard_delete_after`, not
    a date.
  - `DELETE /v1/auth/account` now returns `200 {"deletion_scheduled_for": ...}`, so the
    notice after deleting has the moment too.
  - The web shows it as "Nov 24 at 9:04 AM" in the browser's zone.
  - Reason: the hourly job deletes the account soon after that moment. A UTC-only date
    would let a parent west of UTC log in on "the last day" after their data is gone.

- **Login.** Email login and Google sign-in issue tokens for these accounts instead of
  refusing them.
- **Every other endpoint answers 403 `account_pending_deletion`** for such an account.
  The exceptions:
  - `GET /v1/auth/me`;
  - `POST /v1/auth/account/restore`;
  - `POST /v1/auth/logout`;
  - `POST /v1/auth/refresh`.

  A 403 rather than a 401, so the web app doesn't take it for an expired session and loop
  through refresh.
- **Once `hard_delete_after` has passed,** login and every endpoint refuse with 401
  `account_unavailable`, as today.
- **The web app.**
  - When `/me` reports `deletion_scheduled_for`, or any call returns
    `account_pending_deletion`, every signed-in route shows one screen: "Your account will
    be deleted on Nov 24. [Restore my account] [Sign out]".
  - The date in that screen, and in the notice after deleting, is the exact local moment
    described above, for example "Nov 24 at 9:04 AM".
  - Restore calls the existing endpoint, then loads the app normally.
  - Google stays disconnected, because deletion revoked it. The Profile screen shows
    "Not connected" with Connect.

### The Profile screen (`/profile`)

Laid out as in the mockup and the earlier sketch:

- **Who you are.** "Signed in as <email>", the plan name, and a link to `/plans`.
- **Your icon.**
  - The current icon at 72 px.
  - Upload a photo (a file input, image types only), and Use my initial.
  - The 16 icons as a grid of buttons. The chosen one is marked, and saving happens on
    click.
  - Upload errors (too large, not an image) say what went wrong.
- **Temperature.**
  - The choice is ° F or ° C. It saves on change.
  - The helper text reads: "Weather on your entries and in new AI drafts."
- **Google.**
  - Shows the connection status and the granted scopes.
  - Offers Connect or Disconnect, reusing the existing integration endpoints and the
    existing connect flow. Connect asks for the same scopes as the existing Connect button
    on the diary page.
- **Sessions.**
  - The button reads "Log out on all devices". It calls `logout-all`, then signs out here.
- **Delete account.**
  - The explanation reads: "Your diaries, entries and photos are deleted after 7 days. Log
    in during those 7 days to undo it."
  - **Delete account…** opens a confirmation where the person must type `DELETE`.
  - After confirming, the person is signed out and lands on the login page. The notice
    reads: "Your account will be deleted on Nov 24. Log in before then to restore it."

### The shared header

- **Where it goes:** a component used by the 13 signed-in pages in place of their own
  `<nav>`. Login, register and the landing page keep what they have.
- **Back links:** each page passes its current back link (for example "← Diary") or none.
- **The icon at the top right.**
  - The person's photo, preset or initial, at 34 px on desktop and 30 px on phones.
  - It's a button that opens a menu with **Profile** and **Sign out**.
  - The menu is keyboard-operable, closes on Escape or an outside click, and uses
    `aria-haspopup` and `aria-expanded`.
- **Sign out moves into the menu.** The diary list's "Sign out" button goes away.
- **Loading the photo.** It's fetched once per session with the bearer token (the same way
  photo thumbnails load) and kept as an object URL. It's refreshed after an upload or a
  change.

## My calls (Andrew can override)

- **Everyone sees the diary owner's temperature unit.** That covers editors, viewers and
  the AI.
- **The delete confirmation is typing `DELETE`.** Google-only accounts have no password to
  re-enter.
- **Uploaded photos are centre-cropped,** with no cropping tool. Shown as 256 × 256 JPEG.
- **The default is the first letter of the email** on a fixed blue.
- **The preset colours are fixed per icon,** as in the mockup.
- **The route is `/profile`,** and its page title is "Profile".

## Left to the builder

- The component names and file layout.
- The colour values (from the mockup).
- How the restore screen hooks into the auth context.
- Whether the header reads the user from the auth context or its own fetch.
- The migration's constraint names.
- How the photo upload code is shared with `POST /v1/photos`.

## Verification

- **API tests.**
  - `/me` fields for each avatar kind and unit.
  - `PATCH /me` validation: a bad unit, an unknown icon, extra fields.
  - Choosing an icon deletes an uploaded object.
  - Avatar upload:
    - stores an encrypted object;
    - strips EXIF GPS, checked by reading the stored bytes back;
    - returns 256 × 256;
    - deletes the previous object on replace;
    - is owner-only;
    - 404s with none.
  - Hard delete removes the avatar object.
  - The weather prompt line carries converted, half-up-rounded values with the unit, for
    both units. 21.5 °C becomes 71 °F, half up.
  - Restore: login during grace succeeds; other endpoints answer 403
    `account_pending_deletion`; restore re-enables everything; after grace, login gets 401.
  - An admin-blocked account (`deleted_at` set, no `hard_delete_after`) gets 401 on login,
    `/me` and restore.
  - `DELETE /account` returns `deletion_scheduled_for`.
  - Rounding: −0.5 °C and 21.5 °C (→ 71 °F) give the same result in the Python and the
    TypeScript tests.
  - Google sign-in follows the same rules.
- **Web Jest tests.**
  - The badge shows the unit and rounds half up in both units.
  - The header renders the photo, icon and initial; its menu opens and closes and is
    keyboard-operable.
  - Every signed-in page renders the shared header.
  - The Profile screen:
    - saves the unit and the icon;
    - shows upload errors;
    - the delete flow needs `DELETE`.
  - The restore screen appears on `deletion_scheduled_for` and on a 403
    `account_pending_deletion`.
- **Playwright.**
  - Pick an icon on Profile, and the header shows it on another page.
  - Switch to °C, and an entry's badge shows °C.
  - Delete an account, log in, see the restore screen, restore, and the diaries are back.
- **Suites.** `make test-all` (it now includes Jest), then the dev-stack check.
- **By hand, on localhost and then on Andrew's phone after deploy:**
  - Upload a photo from the phone's library. Check it shows top right, and that a GPS-tagged
    photo's stored copy has no location.
  - Switch units and check an entry's badge.
  - On a test account: delete, log in, restore.

## Research notes (2026-11-17)

These are the facts the design rests on, so a builder or a resumed session doesn't have
to rediscover them.

### What exists today

- **No account page.** The only account control in the web app is "Sign out" on the diary
  list (`apps/web/src/app/diaries/page.tsx`, the `Nav` component).
- **Account endpoints that already exist,** in `apps/api/app/routers/v1/auth.py`:
  - `POST /logout-all`, which revokes every refresh token;
  - `GET /me`, which returns `UserProfile`: id, email, display_name, subscription_tier,
    is_admin, email_verified_at;
  - `GET /me/plan`;
  - `DELETE /account`, which:
    - sets `deleted_at` and `hard_delete_after = now + 7 days`;
    - revokes every refresh token;
    - revokes the Google grant and deletes the token row;
    - returns 204;
  - `POST /account/restore`, behind `get_current_user_within_grace`.
- **Login refuses deleted accounts outright.** It returns 401 `account_unavailable` when
  `deleted_at` or `hard_delete_after` is set (`auth.py` login). `get_current_user` does the
  same (`apps/api/app/core/auth.py`), so restore is only reachable while an old access
  token lives. That's finding C-14 / TR-3 in `design/code-review-2026-11.md`.
- **`get_current_user_within_grace` lets a null `hard_delete_after` through.** That's the
  gap this design closes for admin-blocked accounts.
- **Hard delete.** `process_hard_deletes` is a Celery beat task
  (`apps/api/app/workers/beat_tasks.py`). `hard_delete_user`
  (`apps/api/app/workers/hard_delete.py`) deletes the diaries, then the user-level rows,
  then scrubs MinIO under `{user_id}/` and `tmp/{user_id}/`. So an avatar stored under
  `{user_id}/avatar/` is removed with no extra code.
- **Scans already skip an account that's being deleted**
  (`apps/api/app/workers/tasks.py:114-117`).
- **The app sends no email.** There's a `sendgrid_api_key` / `email_from` setting
  (`apps/api/app/core/config.py:63-65`) and no sending code. That's why the emailed
  restore link is backlog row 49.
- **No diary sharing:** no invite or permission endpoints or screens. Nobody but the owner
  sees a diary today, so nobody else sees a profile icon.
- **Notifications aren't built (row 20),** which is why there are no notification
  preferences on Profile.
- **The admin CLI's soft delete** (`scripts/manage.py users delete`, without `--hard`) sets
  `deleted_at` with no `hard_delete_after`, and soft-deletes the diaries and entries.

### Photos

- **Upload.** `POST /v1/photos` takes the raw request body (`apps/api/app/routers/v1/photos.py`).
  - Size limit: `MAX_BYTES = 50 MB` (`apps/api/app/services/photos.py:22`).
  - Type check: `detect_mime`.
  - Location stripping: `strip_location`.
  - Thumbnails and the HEIC display copy: `generate_thumbnail` and `make_display_jpeg`.
  - Storage: `put_object_bytes`, `get_object_bytes`, `delete_object`, `stream_object`.
- **Encryption.** Each photo has a data key wrapped for the user (`photos.dek_ciphertext`,
  `unwrap_dek(photo.dek_ciphertext, photo.user_id)`). The bytes are AES-GCM, streamed back
  through `iter_decrypt_stream`. Avatars reuse these.
- **Libraries.** `Pillow>=10.4` and `pillow-heif>=0.18` are already dependencies
  (`apps/api/pyproject.toml:51-52`), so iPhone HEIC uploads work.

### Temperature

- **Weather is stored in °C.** It's an enrichment of kind `weather` whose payload holds
  `temperature_max_c` and `temperature_min_c` (`apps/api/app/workers/open_meteo.py:132-133`).
- **The badge** (`apps/web/src/components/WeatherBadge.tsx`) rounds and shows "21°" with no
  unit. It appears only on the single-entry page.
- **The prompt** includes each enrichment as `[kind] {json.dumps(payload)}`
  (`apps/api/app/workers/llm.py`, `build_prompt`, events and hybrid modes). The fact check
  (`validate_citation`) builds its source text from the same `json.dumps` of the payloads.
- **`json.dumps` escapes "°" as `°` by default.** Use `ensure_ascii=False` for the
  converted weather line (my call, raised by the builder check).
- **The fact check** (`_unsupported_words`, `llm.py:344`) only flags capitalised words. It
  never checks numbers. The tracker row's claim that the conversion "keeps the word check
  matching the numbers" was wrong. The real reason to hand the AI converted values is so
  it copies the badge's number instead of doing the arithmetic itself.
- **Production on 2026-11-17:** 31 entries. 5 have weather, 2 mention °C in their text,
  and none mention °F.

### Header

- **13 signed-in pages each build their own `<nav className="nav">`:**
  - `diaries`, `diaries/new`, `diaries/restore`;
  - `diaries/[diaryId]` and its `settings`, `rules`, `rules/new`, `restore` and
    `calendar-pick` pages;
  - `entries/[entryId]`, `rules/[ruleId]`, `photos`, `plans`.
- **What each bar holds.** Only the diary list's has more than a back link: "Perfect Day"
  and "Sign out". The rest show the brand or a back link, such as "← Diary" pointing at
  `/diaries/{id}`, or `/diaries/{id}/rules`.
- **No shared layout component.** `apps/web/src/app/layout.tsx` only wraps `AuthProvider`.

### Icons

- **The source.** `lucide-react` ^1.17.0, already a dependency, ISC licence. All 16 names
  exist in `node_modules/lucide-react/dist/esm/icons/`.
- **The mockup.** The artifact's colours are pairs of light and dark tokens `--t1` to `--t8`
  (bg and fg).
  - The icon-to-tone mapping: cat 1, dog 4, bird 2, fish 7, rabbit 6, squirrel 1,
    turtle 3, panda 8, snail 4, flower 6, sun 4, moon 5, rocket 2, rainbow 5, cake 6,
    paw-print 3.
  - Light values:
    - t1 `#fde2d6` / `#9a3412`
    - t2 `#dbeafe` / `#1e40af`
    - t3 `#dcfce7` / `#166534`
    - t4 `#fef3c7` / `#92400e`
    - t5 `#ede9fe` / `#5b21b6`
    - t6 `#fce7f3` / `#9d174d`
    - t7 `#ccfbf1` / `#115e59`
    - t8 `#e5e7eb` / `#374151`
  - The initial uses t2.

### Review

- **The builder check (2026-11-17, Opus) raised three questions,** all resolved above:
  1. Old °C text against the new badge: Andrew chose to accept it.
  2. The deadline's time zone: shown as an exact local time.
  3. Admin-blocked accounts: they stay blocked.
- **It confirmed** the encryption helpers, the prefix scrub, the size and type limits, HEIC,
  the icons, the 13 pages, the connect flow's `return_to`, and the badge's single location.

## Status and next steps (2026-11-17)

- **The design is approved** (Andrew: "Yes this looks good, I approve").
- **Building.** Branch `feat/profile-screen`, local only (not pushed). The API, the migration, the
  Profile page, the shared header, the restore screen and the Playwright journeys
  (`apps/web/e2e/profile.spec.ts`) are built. Rows 29 and 32 of `POC_PHASE2_TODO.md` are "in review".
- **Next:** final whole-branch review, `make test-all`, the dev-stack check, the PR, CI, the merge,
  a deploy (ask Andrew first), and Andrew's phone check (see Verification, By hand).

