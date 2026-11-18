# Profile Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `/profile` screen (icon, °F/°C, Google, log out everywhere, delete account), one shared
header with the person's icon on every signed-in page, and restore-by-login during the 7-day grace.

**Architecture:**
- **API.** Migration 0020 adds four `users` columns. A small `services/temperature.py` owns the
  half-up conversion that the prompt, the fact check and `EntryOut` use. `core/auth.py` gains one
  grace check that splits deleted accounts into "pending" (403) and "blocked" (401). A new
  `routers/v1/profile.py` holds `PATCH /auth/me` and the avatar endpoints, storing the photo like
  entry photos.
- **Web.** `AuthProvider` carries the typed profile, the avatar object URL, and a pending-deletion
  flag that `api.ts` raises on a 403 `account_pending_deletion`. `AppHeader` replaces the 13 page
  bars. `/profile` is a new page.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, Alembic, Pillow + pillow-heif, MinIO (S3); Next.js 14
client pages, Jest + Testing Library, Playwright; `lucide-react` icons.

**Spec:** `docs/superpowers/specs/2026-11-17-profile-screen-design.md` (approved by Andrew
2026-11-17). Read it first; its research notes hold the file:line facts this plan relies on.

## Global Constraints

- Temperature: °F = °C × 9/5 + 32, rounded half up with `floor(x + 0.5)` in Python **and**
  TypeScript. Never Python `round()`, never `Math.round` for display. −0.5 °C → 0 °C,
  −1.5 °C → −1 °C, −17.5 °C → 1 °F, 21.5 °C → 71 °F.
- Weather stays stored in °C. `workers/open_meteo.py` is not changed.
- The prompt says `°F` / `°C`, never "Fahrenheit"/"Celsius"; the weather line uses
  `json.dumps(..., ensure_ascii=False)`.
- `temperature_unit` is `'F'` or `'C'`, default `'F'`, account-wide. Everyone viewing a diary sees
  the **owner's** unit.
- The 16 icon names, in display order: `cat`, `dog`, `bird`, `fish`, `rabbit`, `squirrel`,
  `turtle`, `panda`, `snail`, `flower`, `sun`, `moon`, `rocket`, `rainbow`, `cake`, `paw-print`.
- Icon tones (spec, Research notes → Icons): cat 1, dog 4, bird 2, fish 7, rabbit 6, squirrel 1,
  turtle 3, panda 8, snail 4, flower 6, sun 4, moon 5, rocket 2, rainbow 5, cake 6, paw-print 3;
  the initial uses tone 2. Light bg/fg pairs: t1 `#fde2d6`/`#9a3412`, t2 `#dbeafe`/`#1e40af`,
  t3 `#dcfce7`/`#166534`, t4 `#fef3c7`/`#92400e`, t5 `#ede9fe`/`#5b21b6`, t6 `#fce7f3`/`#9d174d`,
  t7 `#ccfbf1`/`#115e59`, t8 `#e5e7eb`/`#374151`.
- Avatar photo: same types and `MAX_BYTES` as `POST /v1/photos`; EXIF orientation applied; **all**
  metadata dropped; centre-cropped; 256 × 256 JPEG; encrypted with a per-object data key wrapped
  for the user; stored at `{user_id}/avatar/<uuid4 hex>.jpg`; served owner-only with
  `Cache-Control: private, no-store`.
- Grace: `deleted_at` set **and** `hard_delete_after` set **and** in the future. Pending → 403
  `account_pending_deletion` everywhere except `GET /v1/auth/me`, `POST /v1/auth/account/restore`,
  `POST /v1/auth/logout`, `POST /v1/auth/refresh`, and login (email and Google). Anything else
  deleted (admin-blocked: no `hard_delete_after`; or past it) → 401 `account_unavailable`
  everywhere, restore included.
- Deadlines are shown as the exact local moment, format "Nov 24 at 9:04 AM".
- Copy, verbatim:
  - Temperature helper: "Weather on your entries and in new AI drafts."
  - Delete explanation: "Your diaries, entries and photos are deleted after 7 days. Log in during those 7 days to undo it."
  - Login notice: "Your account will be deleted on {when}. Log in before then to restore it."
  - Restore screen: "Your account will be deleted on {when}." with buttons "Restore my account" and "Sign out".
  - Sessions button: "Log out on all devices". Delete button: "Delete account…". Confirmation word: `DELETE`.
- Never `ruff format` the whole tree; format only files you touch. Stage explicit paths only.
- After every task: the task's tests, then `make lint` and `make typecheck` stay green.

## Review Focus

1. **Entry action responses carry no unit.** `PATCH /entries/{id}`, publish, accept etc. return
   `temperature_unit: null` (only the single GET fills it, like `plan`). The badge must keep the
   owner's unit after a save, not fall back to °F. Pinned in Task 8 (`unitRef` test).
2. **A Google OAuth callback for an account pending deletion.** A connect started before deleting
   could finish afterwards and store fresh Google tokens. The callback must refuse and store
   nothing. Pinned in Task 3.
3. **An iPhone HEIC, a corrupt file, or a non-image as the avatar.** HEIC must work; the others must
   be 415 `unsupported_mime`, never a 500. Pinned in Task 5.
4. **The old avatar object can't be deleted** (MinIO hiccup) during replace or switch-to-icon. The
   change must still succeed and the new state be saved. Pinned in Task 5.
5. **A 403 `account_pending_deletion` must not trigger a token refresh loop,** and must flip the
   app to the restore screen from any page. Pinned in Task 9.

---

## File map

| File | Responsibility |
|---|---|
| `apps/api/alembic/versions/0020_profile.py` | the four `users` columns and their checks |
| `apps/api/app/models/__init__.py` | `User` columns; `AVATAR_ICONS` tuple |
| `apps/api/app/services/temperature.py` | conversion, rounding, prompt payload, prompt line |
| `apps/api/app/workers/llm.py` | uses the temperature helpers in prompt and fact check |
| `apps/api/app/core/auth.py` | `deletion_state()`, tightened dependencies |
| `apps/api/app/routers/v1/auth.py` | login/Google/refresh/me/delete/restore rules; `UserProfile` |
| `apps/api/app/services/profile.py` | `user_profile(user) -> UserProfile` and `AvatarOut` models |
| `apps/api/app/services/avatar.py` | image → 256² JPEG, encrypt/store/load/delete |
| `apps/api/app/routers/v1/profile.py` | `PATCH /auth/me`, `PUT/GET/DELETE /auth/me/avatar` |
| `apps/api/app/routers/v1/entries.py` | `EntryOut.temperature_unit` |
| `apps/web/src/lib/temperature.ts` | `toUnit`, `formatTemp` |
| `apps/web/src/lib/deadline.ts` | `formatDeadline(iso)` |
| `apps/web/src/lib/avatarIcons.ts` | the 16 icons, their Lucide components and tones |
| `apps/web/src/components/Avatar.tsx` | renders photo / icon / initial at a size |
| `apps/web/src/components/AppHeader.tsx` | brand, back link, avatar menu |
| `apps/web/src/components/RestoreAccountScreen.tsx` | the pending-deletion screen |
| `apps/web/src/app/profile/page.tsx` | the Profile screen |

---

### Task 1: Migration 0020 and the `User` columns

**Files:**
- Create: `apps/api/alembic/versions/0020_profile.py` (`revision = "0020"`, `down_revision = "0019"`)
- Modify: `apps/api/app/models/__init__.py` (class `User`, near line 61)
- Test: `apps/api/tests/integration/test_profile_columns.py`

**Interfaces:**
- Produces: `User.temperature_unit: Mapped[str]`, `User.avatar_icon: Mapped[str | None]`,
  `User.avatar_s3_key: Mapped[str | None]`, `User.avatar_dek_ciphertext: Mapped[bytes | None]`;
  module constant `AVATAR_ICONS: tuple[str, ...]` (the 16 names, in order) in `app/models/__init__.py`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_new_user_defaults_to_fahrenheit_and_initial(db_session): ...
    # insert a User with only email; refresh; temperature_unit == "F", avatar_icon is None, avatar_s3_key is None
async def test_unit_must_be_f_or_c(db_session): ...           # temperature_unit="K" → IntegrityError
async def test_icon_must_be_one_of_the_16(db_session): ...    # avatar_icon="unicorn" → IntegrityError
async def test_icon_and_photo_are_exclusive(db_session): ...  # both set → IntegrityError
def test_avatar_icons_order(): assert AVATAR_ICONS == ("cat", "dog", "bird", "fish", "rabbit", "squirrel", "turtle", "panda", "snail", "flower", "sun", "moon", "rocket", "rainbow", "cake", "paw-print")
```

- [ ] **Step 2: Run, expect FAIL** — `cd apps/api && .venv/bin/pytest tests/integration/test_profile_columns.py -q` → errors on the missing columns / `AVATAR_ICONS`.
- [ ] **Step 3: Write the migration and model columns.** `temperature_unit` `CHAR(1)` not null,
  `server_default='F'`, check `temperature_unit IN ('F','C')`; `avatar_icon` text null with an `IN`
  check over the 16 names; `avatar_s3_key` text null; `avatar_dek_ciphertext` bytea null; check
  `NOT (avatar_icon IS NOT NULL AND avatar_s3_key IS NOT NULL)`. `downgrade()` drops the checks
  and columns. Build the icon check from `AVATAR_ICONS` copied **literally** into the migration
  (migrations must not import app code).
- [ ] **Step 4: Run, expect PASS**; also `make migrate` against a throwaway DB is covered by the
  integration conftest (it runs Alembic) — confirm no Alembic error in the output.
- [ ] **Step 5: Commit** `feat(api): users get temperature_unit and avatar columns (migration 0020)`

---

### Task 2: Temperature conversion, the prompt, and `EntryOut.temperature_unit`

**Files:**
- Create: `apps/api/app/services/temperature.py`
- Modify: `apps/api/app/workers/llm.py` (`build_prompt` ~211-285, `validate_citation` ~373-410, the
  drafting job ~545-750 that loads `diary`)
- Modify: `apps/api/app/routers/v1/entries.py` (`EntryOut` ~151; `GET /entries/{entry_id}` ~529-540)
- Test: `apps/api/tests/unit/test_temperature.py`, `apps/api/tests/unit/test_llm_prompt.py`,
  `apps/api/tests/unit/test_llm_validator.py`, `apps/api/tests/integration/test_entries.py`

**Interfaces:**
- Consumes: `User.temperature_unit` (Task 1).
- Produces (`app/services/temperature.py`):
  - `TemperatureUnit = Literal["F", "C"]`
  - `def round_half_up(x: float) -> int` — `math.floor(x + 0.5)`
  - `def to_unit(celsius: float, unit: TemperatureUnit) -> int`
  - `def weather_for_prompt(payload: dict, unit: TemperatureUnit) -> dict` — a copy without
    `temperature_max_c`/`temperature_min_c`, with `temperature_max`, `temperature_min` (int or
    `None` when the source is missing or not a number) and `temperature_unit` (`"°F"`/`"°C"`).
  - `def enrichment_line(kind: str, payload: dict, unit: TemperatureUnit) -> str` — weather:
    `"[weather] " + json.dumps(weather_for_prompt(...), ensure_ascii=False)`; any other kind:
    exactly today's `f"[{kind}] {json.dumps(payload)}"`.
  - `def enrichment_source_text(enrichments: list, unit: TemperatureUnit) -> str` — what
    `validate_citation` joins today, with weather payloads converted the same way.
- `build_prompt(..., temperature_unit: TemperatureUnit = "F")` and
  `validate_citation(..., temperature_unit: TemperatureUnit = "F")` gain a keyword.
- `EntryOut.temperature_unit: Literal["F", "C"] | None = None`, set on `GET /entries/{entry_id}`
  only (exactly where `out.plan` is set), from the diary owner.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_temperature.py
@pytest.mark.parametrize(("c", "unit", "want"), [
    (21.5, "F", 71), (-17.5, "F", 1), (20.5, "C", 21), (-0.5, "C", 0), (-1.5, "C", -1), (16.0, "F", 61),
])
def test_to_unit_rounds_half_up(c, unit, want): assert to_unit(c, unit) == want

def test_weather_for_prompt_replaces_celsius_keys():
    out = weather_for_prompt({"temperature_max_c": 21.5, "temperature_min_c": 16.0, "condition": "sunny"}, "F")
    assert out == {"temperature_max": 71, "temperature_min": 61, "temperature_unit": "°F", "condition": "sunny"}

def test_weather_for_prompt_missing_values_are_none():
    assert weather_for_prompt({"temperature_max_c": None}, "C")["temperature_max"] is None

def test_weather_line_uses_symbol_not_escape():
    line = enrichment_line("weather", {"temperature_max_c": 21.5}, "F")
    assert '"temperature_unit": "°F"' in line and "\\u00b0" not in line and "Fahrenheit" not in line

def test_other_kinds_unchanged():
    assert enrichment_line("place", {"name": "Zoo"}, "F") == '[place] {"name": "Zoo"}'
```

```python
# tests/unit/test_llm_prompt.py — new
def test_prompt_weather_line_in_owner_unit_events_and_hybrid(): ...
    # build_prompt(..., temperature_unit="C") contains '"temperature_max": 22' for 21.5 °C and
    # no 'temperature_max_c'; with "F" it contains '"temperature_max": 71'. Check both modes.
# tests/unit/test_llm_validator.py — new
def test_fact_check_source_text_uses_converted_weather(): ...
    # validate_citation(..., enrichments=[weather 21.5], temperature_unit="F") does not flag "°F"
    # text and the source text contains "71" (assert via enrichment_source_text directly).
# tests/integration/test_entries.py — new
async def test_get_entry_carries_owner_temperature_unit(...): ...
    # owner unit "C" (set the column directly) → GET /entries/{id} has temperature_unit == "C";
    # PATCH /entries/{id} response has temperature_unit is None.
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: app.services.temperature`, then assertion failures).
- [ ] **Step 3: Implement** the module; make `build_prompt` and `validate_citation` call
  `enrichment_line` / `enrichment_source_text`; in the drafting job load the owner
  (`await db.get(User, diary.owner_user_id)`) and pass `temperature_unit=owner.temperature_unit`
  to both calls (default `"F"` when the owner row is missing); add the `EntryOut` field and set it
  in the single GET.
- [ ] **Step 4: Run, expect PASS** — the four test files above, then the whole `tests/unit -q` (the
  other LLM tests use the default and must not change).
- [ ] **Step 5: Commit** `feat(api): weather reaches the AI in the owner's unit, rounded half up (row 32)`

---

### Task 3: Deletion grace — restore by logging in (fixes C-14)

**Files:**
- Modify: `apps/api/app/core/auth.py`, `apps/api/app/routers/v1/auth.py` (login ~147-170,
  `social_google` ~178-220, `refresh` ~326-335, `me` ~401, `delete_account` ~419-445,
  `restore_account` ~447), `apps/api/app/routers/v1/integrations.py` (Google callback ~234-400)
- Test: `apps/api/tests/integration/test_account_grace.py` (new); update
  `tests/integration/test_auth_flow.py::TestSoftDeleteAccount` (204 → 200, `/me` 401 → 200)

**Interfaces:**
- Produces (`app/core/auth.py`):
  - `DeletionState = Literal["active", "pending", "blocked"]`
  - `def deletion_state(user: User, now: datetime | None = None) -> DeletionState` — `active` when
    `deleted_at` and `hard_delete_after` are both null; `pending` when both set and
    `hard_delete_after > now`; otherwise `blocked`.
  - `get_current_user`: pending → 403 `account_pending_deletion`; blocked → 401 `account_unavailable`.
  - `get_current_user_within_grace`: active or pending pass; blocked → 401 `account_unavailable`.
- `GET /v1/auth/me` uses `get_current_user_within_grace`.
- `DELETE /v1/auth/account` → `200 {"deletion_scheduled_for": "<ISO hard_delete_after>"}`.

- [ ] **Step 1: Write the failing tests** (`test_account_grace.py`; use `register_and_login`)

```python
async def test_login_during_grace_succeeds(client): ...          # delete, then POST /login → 200 with access_token
async def test_pending_account_gets_403_elsewhere(client): ...   # GET /v1/diaries → 403, detail "account_pending_deletion"
async def test_pending_account_may_use_me_refresh_logout(client): ...  # /me 200 with deletion_scheduled_for, /refresh 200, /logout 204
async def test_restore_reenables_everything(client): ...         # restore 204 → /v1/diaries 200; /me deletion_scheduled_for None
async def test_after_grace_login_and_me_are_401(client, db_session): ...  # set hard_delete_after = now-1s → login 401 account_unavailable, /me 401
async def test_admin_blocked_account_stays_401(client, db_session): ...   # deleted_at set, hard_delete_after NULL → login, /me, restore all 401 account_unavailable
async def test_delete_account_returns_deadline(client): ...      # 200, deletion_scheduled_for parses and is ~7 days ahead (±1 min)
async def test_google_login_follows_the_same_rules(client, db_session): ...  # patch google_id_token.verify_oauth2_token as test_google_oauth.py does: pending → 200, blocked → 401
async def test_oauth_callback_refuses_pending_account(client, db_session): ...  # Review Focus 2: a callback for a pending user stores no OAuthToken row; reuse test_google_oauth.py's callback fixtures
```

- [ ] **Step 2: Run, expect FAIL** (login 401, `/diaries` 401 instead of 403, delete 204).
- [ ] **Step 3: Implement.** Login, Google sign-in (existing-identity branch) and refresh use
  `deletion_state(...) == "blocked"` → 401 `account_unavailable` instead of today's checks. `/me`
  switches dependency. `delete_account` returns the deadline (drop `status_code=204`, add a small
  response model). The OAuth callback loads the user and, unless `active`, redirects with the same
  error path it uses for other refusals and stores nothing. `UserProfile` gains
  `deletion_scheduled_for: datetime | None` (= `hard_delete_after` when pending, else None) — Task 4
  moves `UserProfile`; add the field here so this task's tests pass.
- [ ] **Step 4: Run, expect PASS** — `test_account_grace.py`, `test_auth_flow.py`,
  `test_google_oauth.py`, `test_account_deletion_google.py`.
- [ ] **Step 5: Commit** `fix(api): a deleted account can log in and restore during its grace (C-14)`

---

### Task 4: Profile API — `/me` fields and `PATCH /auth/me`

**Files:**
- Create: `apps/api/app/services/profile.py`, `apps/api/app/routers/v1/profile.py`
- Modify: `apps/api/app/routers/v1/auth.py` (move `UserProfile` out; `/me` returns `user_profile(user)`),
  `apps/api/app/main.py` (`app.include_router(profile.router, prefix="/v1")`)
- Test: `apps/api/tests/integration/test_profile_api.py`

**Interfaces:**
- Consumes: `deletion_state` (Task 3), `AVATAR_ICONS` (Task 1).
- Produces (`app/services/profile.py`):
  - `class AvatarPhoto(BaseModel): kind: Literal["photo"]`
  - `class AvatarIcon(BaseModel): kind: Literal["icon"]; name: str`
  - `class AvatarInitial(BaseModel): kind: Literal["initial"]`
  - `class UserProfile(BaseModel)`: today's fields + `temperature_unit: Literal["F","C"]`,
    `avatar: AvatarPhoto | AvatarIcon | AvatarInitial`, `deletion_scheduled_for: datetime | None`
  - `def user_profile(user: User) -> UserProfile`
- `router = APIRouter(prefix="/auth", tags=["profile"])` in `routers/v1/profile.py`;
  `PATCH /v1/auth/me` body `ProfilePatch` (`model_config = {"extra": "forbid"}`;
  `temperature_unit: Literal["F","C"] | None = None`; `avatar_icon: str | None = None`), returns
  `UserProfile`. An explicit `"avatar_icon": null` means "use my initial"; an absent key means no
  change (use `model_fields_set`). `"temperature_unit": null` → 422.

- [ ] **Step 1: Write the failing tests**

```python
async def test_me_defaults(client): ...   # temperature_unit "F", avatar == {"kind": "initial"}, deletion_scheduled_for None
async def test_patch_unit(client): ...    # PATCH {"temperature_unit": "C"} → 200, body and later /me say "C"
async def test_patch_icon_then_initial(client): ...  # {"avatar_icon": "panda"} → avatar {"kind":"icon","name":"panda"}; {"avatar_icon": None} → {"kind":"initial"}
@pytest.mark.parametrize("body", [{"temperature_unit": "K"}, {"temperature_unit": None}, {"avatar_icon": "unicorn"}, {"email": "x@y.z"}])
async def test_patch_rejects(client, body): ...   # 422
async def test_patch_needs_active_account(client): ...  # pending account → 403 account_pending_deletion
```

- [ ] **Step 2: Run, expect FAIL** (PATCH 405, `avatar` missing).
- [ ] **Step 3: Implement.** Icon validation against `AVATAR_ICONS` in the model. Choosing an icon
  or the initial while a photo is set clears `avatar_s3_key`/`avatar_dek_ciphertext` and deletes
  the object — call `delete_avatar_object` from Task 5; until Task 5 exists, write the column clear
  only and leave a `# Task 5` marker that Task 5 replaces (Task 5's tests cover the deletion).
- [ ] **Step 4: Run, expect PASS** — `test_profile_api.py`, `test_auth_flow.py`, `test_account_grace.py`.
- [ ] **Step 5: Commit** `feat(api): PATCH /v1/auth/me sets the unit and the preset icon (row 29)`

---

### Task 5: Avatar photo — upload, serve, delete

**Files:**
- Create: `apps/api/app/services/avatar.py`
- Modify: `apps/api/app/core/photo_crypto.py` (add `PURPOSE_AVATAR = b"avatar"` beside the other
  purposes), `apps/api/app/routers/v1/photos.py` (extract the capped body read),
  `apps/api/app/routers/v1/profile.py` (avatar routes; replace Task 4's marker),
  `apps/api/tests/integration/photo_helpers.py` (move `heic_bytes`, `gps_heic_bytes`, `gps_of`
  here from `test_photo_display_copy.py`, which then imports them)
- Test: `apps/api/tests/integration/test_avatar.py`, `apps/api/tests/integration/test_hard_delete_photos.py`

**Interfaces:**
- Produces:
  - `async def read_capped_body(request: Request) -> bytes` in `routers/v1/photos.py` — today's
    loop (413 `size_exceeds_limit` past `MAX_BYTES`); `upload_photo` uses it.
  - `services/avatar.py`: `AVATAR_SIZE = 256`;
    `def make_avatar_jpeg(data: bytes) -> bytes` (raises `UnsupportedPhoto` from
    `services/photo_ingest.py` for anything Pillow can't open, after `detect_mime` rejects non-images);
    `def store_avatar(user_id: uuid.UUID, jpeg: bytes) -> tuple[str, bytes]` → `(s3_key, wrapped_dek)`;
    `def load_avatar(s3_key: str, wrapped_dek: bytes, user_id: uuid.UUID) -> bytes`;
    `def delete_avatar_object(s3_key: str) -> None` (logs and swallows storage errors).
  - `PUT /v1/auth/me/avatar` (raw body) → `UserProfile`; 413 / 415 `unsupported_mime` like photos.
  - `GET /v1/auth/me/avatar` → `image/jpeg`, `Cache-Control: private, no-store`; 404 when none.
  - `DELETE /v1/auth/me/avatar` → `UserProfile` with `avatar.kind == "initial"`.
- `make_avatar_jpeg`: `pillow_heif.register_heif_opener()`, `ImageOps.exif_transpose`,
  `ImageOps.fit(img, (256, 256), Image.LANCZOS, centering=(0.5, 0.5))`, convert to RGB, save JPEG
  quality 85 **without** an `exif` argument (re-encoding drops all metadata).
- Order on replace: store new object → update row → commit → `delete_avatar_object(old_key)`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_upload_stores_encrypted_256_square(client, db_session): ...
    # PUT jpeg_with_exif() → 200 avatar {"kind":"photo"}; row has avatar_s3_key under f"{user_id}/avatar/";
    # raw object bytes start with b"PD02" (encrypted); GET → 200 image/jpeg, Cache-Control "private, no-store", Image.size == (256, 256)
async def test_served_copy_has_no_gps(client): ...        # gps_of(GET bytes) == {} for a jpeg_with_exif(lat=…) upload
async def test_heic_upload_works(client): ...             # gps_heic_bytes() → 200, served JPEG has no GPS
@pytest.mark.parametrize("body", [b"not an image", b"\xff\xd8\xff\xe0" + b"\x00" * 40])
async def test_bad_upload_is_415(client, body): ...      # Review Focus 3: 415 unsupported_mime, avatar unchanged
async def test_replace_deletes_previous_object(client): ...  # second PUT → first key gone from MinIO
async def test_choosing_icon_deletes_photo_object(client): ...  # PATCH avatar_icon "cat" → object gone, GET avatar 404
async def test_delete_avatar(client): ...                 # DELETE → initial; object gone; GET 404
async def test_old_object_delete_failure_still_saves(client, monkeypatch): ...  # Review Focus 4: make delete_object raise → PUT still 200, new key saved
async def test_avatar_is_owner_only(client): ...          # another user's GET /me/avatar returns their own 404, never the first user's bytes
async def test_get_without_avatar_is_404(client): ...
# test_hard_delete_photos.py
async def test_hard_delete_removes_avatar(...): ...       # store an avatar, hard_delete_user → key absent
```

- [ ] **Step 2: Run, expect FAIL** (404/405 on the routes).
- [ ] **Step 3: Implement** the service, the routes, `read_capped_body`, and the Task 4 deletion.
- [ ] **Step 4: Run, expect PASS** — `test_avatar.py`, `test_hard_delete_photos.py`,
  `test_photo_display_copy.py`, `test_shared_photos.py`, the photo upload tests (`-k photo`).
- [ ] **Step 5: Commit** `feat(api): profile photo, encrypted like entry photos (row 29)`

---

### Task 6: Web — API client, auth context, deadline formatting

**Files:**
- Modify: `apps/web/src/lib/api.ts`, `apps/web/src/lib/auth-context.tsx`
- Create: `apps/web/src/lib/deadline.ts`
- Test: `apps/web/src/lib/__tests__/deadline.test.ts`, `apps/web/src/lib/__tests__/api.pendingDeletion.test.ts`,
  `apps/web/src/lib/__tests__/auth-context.test.tsx`

**Interfaces:**
- Produces (`lib/api.ts`):
  - `type TemperatureUnit = 'F' | 'C'`; `type AvatarIconName` (the 16 names as a union);
    `type AvatarChoice = { kind: 'photo' } | { kind: 'icon'; name: AvatarIconName } | { kind: 'initial' }`
  - `interface UserProfile { id; email; display_name: string | null; subscription_tier; is_admin; email_verified_at: string | null; temperature_unit: TemperatureUnit; avatar: AvatarChoice; deletion_scheduled_for: string | null }`
  - `Entry.temperature_unit?: TemperatureUnit | null`
  - `api.auth.me(): Promise<UserProfile>`; `api.auth.updateProfile(p: { temperature_unit?: TemperatureUnit; avatar_icon?: AvatarIconName | null }): Promise<UserProfile>`;
    `api.auth.uploadAvatar(file: Blob): Promise<UserProfile>` (fetch `PUT`, raw body,
    `Content-Type: file.type || 'application/octet-stream'`, one refresh-and-retry on 401);
    `api.auth.avatar(): Promise<Blob>` (via `apiFetchBlob`); `api.auth.deleteAvatar(): Promise<UserProfile>`;
    `api.auth.logoutAll(): Promise<void>`; `api.auth.deleteAccount(): Promise<{ deletion_scheduled_for: string }>`;
    `api.auth.restoreAccount(): Promise<void>`; `api.integrations.disconnectGoogle(): Promise<void>` (`DELETE /v1/integrations/google`)
  - `export function setPendingDeletionHandler(fn: (() => void) | null): void` — `apiFetch` and
    `apiFetchBlob` call it when an error is 403 with code `account_pending_deletion`, then throw
    the `ApiError` as usual. A 403 never triggers `refreshAccessToken`.
- Produces (`lib/auth-context.tsx`), added to `AuthState`:
  `user: UserProfile | null`; `avatarUrl: string | null` (object URL of the photo, else null);
  `pendingDeletion: boolean` (true when `user.deletion_scheduled_for` or the handler fired);
  `refreshUser(): Promise<void>` (re-reads `/me`; re-fetches the avatar blob when
  `avatar.kind === 'photo'`, revoking the previous object URL); `restore(): Promise<void>`
  (`restoreAccount` then `refreshUser`, clears `pendingDeletion`).
- Produces (`lib/deadline.ts`): `export function formatDeadline(iso: string): string` →
  `"Nov 24 at 9:04 AM"` in the browser's zone (`Intl.DateTimeFormat` month `short`, day `numeric`;
  hour `numeric`, minute `2-digit`).

- [ ] **Step 1: Write the failing tests**

```ts
// deadline.test.ts (Jest TZ is America/Los_Angeles)
it('shows the exact local moment', () => expect(formatDeadline('2026-11-24T17:04:00Z')).toBe('Nov 24 at 9:04 AM'))
// api.pendingDeletion.test.ts — mock global.fetch
it('a 403 account_pending_deletion calls the handler and does not refresh', ...)  // Review Focus 5: handler called once; no fetch to /v1/auth/refresh; rejects with ApiError code 'account_pending_deletion'
it('other 403s do not call the handler', ...)
// auth-context.test.tsx — mock '@/lib/api'
it('pendingDeletion follows /me deletion_scheduled_for', ...)
it('restore clears pendingDeletion and re-reads /me', ...)
it('avatarUrl is an object URL only for a photo avatar', ...)  // URL.createObjectURL mocked
```

- [ ] **Step 2: Run, expect FAIL** — `cd apps/web && npx jest --ci src/lib`.
- [ ] **Step 3: Implement.** Retype `user`; existing pages only test `user` for truthiness, so
  `npx tsc --noEmit` should stay clean — fix any page that reads `user` fields loosely.
- [ ] **Step 4: Run, expect PASS** — `npx jest --ci` (all suites; page tests mock `useAuth` with
  partial objects and must still pass) and `npx tsc --noEmit`.
- [ ] **Step 5: Commit** `feat(web): typed profile, avatar and pending-deletion state in the auth context`

---

### Task 7: Web — Avatar, AppHeader, and the 13 pages

**Files:**
- Create: `apps/web/src/lib/avatarIcons.ts`, `apps/web/src/components/Avatar.tsx`,
  `apps/web/src/components/AppHeader.tsx`
- Modify: the 13 pages listed in the spec (Research notes → Header) and `apps/web/src/app/globals.css`
  (header styles beside `.nav`)
- Test: `apps/web/src/components/__tests__/Avatar.test.tsx`, `AppHeader.test.tsx`,
  `apps/web/src/app/__tests__/sharedHeader.test.ts`

**Interfaces:**
- Consumes: `useAuth()` → `user`, `avatarUrl`, `logout` (Task 6); `AvatarIconName` (Task 6).
- Produces:
  - `avatarIcons.ts`: `AVATAR_ICONS: readonly { name: AvatarIconName; Icon: LucideIcon; tone: 1..8 }[]`
    (spec order, Lucide components `Cat, Dog, Bird, Fish, Rabbit, Squirrel, Turtle, Panda, Snail,
    Flower, Sun, Moon, Rocket, Rainbow, Cake, PawPrint`); `TONES: Record<1..8, { bg: string; fg: string }>`
    (Global Constraints values); `INITIAL_TONE = 2`.
  - `<Avatar avatar={AvatarChoice | undefined} email={string} photoUrl={string | null} size={number} />` —
    photo → `<img alt="">`; icon → tinted circle with the Lucide icon; initial → first letter of the
    email, upper-cased, on tone 2. Missing/partial `avatar`, or a photo with no `photoUrl` yet →
    initial. The root element carries `data-icon` = the icon name, `"photo"` or `"initial"`
    (Task 11's e2e reads it).
  - `<AppHeader back?={{ href: string; label: string }} />` — `nav.app-header`: "Perfect Day"
    link to `/diaries`; the back link beside it when given; at right a `button` with
    `aria-haspopup="menu"`, `aria-expanded`, `aria-label="Account menu"` containing `<Avatar size={34}>`
    (30 px under 600 px wide, CSS); the menu (`role="menu"`) has `Profile` (link `/profile`) and
    `Sign out` (calls `logout`, then `/login`). Escape and an outside click close it; Arrow keys
    move between the two items; focus returns to the button on close. Renders no avatar button
    when there's no user.
- Each page replaces its `<nav className="nav">…</nav>` with `<AppHeader back={…} />` keeping its
  current back link text and href; the diary list's `Nav` and its "Sign out" button go away.

- [ ] **Step 1: Write the failing tests**

```tsx
// Avatar.test.tsx
it('photo', ...)    // img with the given src
it('icon', ...)     // svg present; container background = TONES[3].bg for 'turtle'
it('initial', ...)  // text 'A' for 'andrew@…'; background TONES[2].bg
// AppHeader.test.tsx
it('shows the back link and the brand', ...)
it('menu opens, Escape closes and refocuses the button', ...)
it('outside click closes; aria-expanded tracks it', ...)
it('Sign out logs out and goes to /login', ...)  // mock navigateTo
// sharedHeader.test.ts — reads the 13 page files with fs
it.each(PAGES)('%s renders AppHeader and no className="nav"', ...)
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the three files, the CSS, and the 13 page swaps.
- [ ] **Step 4: Run, expect PASS** — `npx jest --ci`, `npx tsc --noEmit`, `npx eslint src --max-warnings 0`.
- [ ] **Step 5: Commit** `feat(web): one shared header with the person's icon on every signed-in page`

---

### Task 8: Web — the weather badge in the owner's unit

**Files:**
- Create: `apps/web/src/lib/temperature.ts`
- Modify: `apps/web/src/components/WeatherBadge.tsx`, `apps/web/src/app/entries/[entryId]/page.tsx`
  (beside `planRef` ~77-80, and the `<WeatherBadge>` at ~429)
- Test: `apps/web/src/lib/__tests__/temperature.test.ts`, `apps/web/src/components/__tests__/WeatherBadge.test.tsx`,
  `apps/web/src/app/entries/[entryId]/__tests__/unit.test.tsx` (or the page's existing test file)

**Interfaces:**
- Produces: `toUnit(celsius: number, unit: TemperatureUnit): number` (`Math.floor(x + 0.5)`);
  `formatTemp(celsius: unknown, unit: TemperatureUnit): string | null` (`"71°F"`; null for a
  non-number). `WeatherBadge` gains `unit: TemperatureUnit`.
- The entry page keeps `unitRef` like `planRef`: updated whenever a response has a non-null
  `temperature_unit`; defaults to `'F'`.

- [ ] **Step 1: Write the failing tests**

```ts
it.each([[21.5,'F',71],[-17.5,'F',1],[20.5,'C',21],[-0.5,'C',0],[-1.5,'C',-1]])('toUnit(%p,%p)=%p', ...)  // same table as Task 2
it('badge shows °F', ...)   // min 16, max 21 → "61°F" "/" "70°F"
it('badge shows °C', ...)   // → "16°C" "/" "21°C"
it('keeps the owner unit after a save returns temperature_unit null', ...)  // Review Focus 1: GET has 'C'; after PATCH response with null, badge still says °C
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS** — `npx jest --ci`.
- [ ] **Step 5: Commit** `feat(web): weather badge shows the owner's unit, rounded half up (row 32)`

---

### Task 9: Web — the restore screen

**Files:**
- Create: `apps/web/src/components/RestoreAccountScreen.tsx`
- Modify: `apps/web/src/lib/auth-context.tsx` (render the screen instead of `children` while
  `pendingDeletion`; register `setPendingDeletionHandler` on mount, clear on unmount)
- Test: `apps/web/src/components/__tests__/RestoreAccountScreen.test.tsx`, `auth-context.test.tsx`

**Interfaces:**
- Consumes: `formatDeadline` (Task 6), `restore`, `logout` (Task 6).
- `RestoreAccountScreen` text: "Your account will be deleted on {formatDeadline(...)}."; buttons
  "Restore my account" (→ `restore()`, then the app renders normally) and "Sign out" (→ `logout()`
  then `/login`). When the 403 arrived without a deadline in hand, it calls `refreshUser()` first to
  get one; while unknown it shows "Your account is scheduled for deletion."

- [ ] **Step 1: Write the failing tests**

```tsx
it('shows the deadline from /me', ...)          // "Your account will be deleted on Nov 24 at 9:04 AM."
it('a 403 from any call shows the screen', ...)  // fire the registered handler → screen replaces children
it('Restore my account restores and shows the app', ...)
it('Sign out signs out', ...)
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS** — `npx jest --ci`.
- [ ] **Step 5: Commit** `feat(web): a deleted account sees one restore screen until it restores or signs out`

---

### Task 10: Web — the Profile screen and the login notice

**Files:**
- Create: `apps/web/src/app/profile/page.tsx`
- Modify: `apps/web/src/app/login/page.tsx` (notice from `?deleted=<iso>`)
- Test: `apps/web/src/app/profile/__tests__/page.test.tsx`, `apps/web/src/app/login/__tests__/` (the login page's test file)

**Interfaces:**
- Consumes: Task 6 API calls and `refreshUser`; `Avatar`, `AVATAR_ICONS`, `AppHeader` (Task 7);
  `formatDeadline`; `api.integrations.list` / `getGoogleAuthUrl` / `disconnectGoogle`; `PlanBadge`
  or `api.auth.myPlan()` for the plan name.
- The page (`<AppHeader back={{ href: '/diaries', label: '← Diaries' }} />`, `<h1>Profile</h1>`),
  sections in spec order:
  - "Signed in as {email}", plan name, link to `/plans`.
  - Icon: current `<Avatar size={72}>`; file input `accept="image/*"` labelled "Upload a photo";
    button "Use my initial"; a grid of 16 buttons (`aria-pressed` on the chosen one,
    `aria-label` = icon name) that save on click. Errors: 413 → "That photo is too large.";
    415 → "That file isn't a photo we can use."; otherwise "Couldn't save your icon. Try again."
  - Temperature: radio group "° F" / "° C", saves on change, helper text (Global Constraints).
  - Google: status from `integrations.list()`; Connect → `getGoogleAuthUrl('calendar', '/profile')`
    then `navigateTo(url)`; Disconnect → `disconnectGoogle()` then reload the list.
  - Sessions: "Log out on all devices" → `logoutAll()`, then `logout()`, then `/login`.
  - Delete: explanation text; "Delete account…" opens a dialog with a text input; the confirm
    button is disabled until the input is exactly `DELETE`; confirm → `deleteAccount()`, then
    clear the local session and go to `/login?deleted=<deletion_scheduled_for>`.
- Login page: with `?deleted=<iso>` shows "Your account will be deleted on {formatDeadline(iso)}.
  Log in before then to restore it." (ignored when the value doesn't parse as a date).

- [ ] **Step 1: Write the failing tests**

```tsx
it('saves the unit on change', ...)            // updateProfile({temperature_unit:'C'}) then refreshUser
it('saves an icon on click and marks it', ...) // updateProfile({avatar_icon:'panda'}); aria-pressed true
it('Use my initial', ...)                      // updateProfile({avatar_icon:null})
it('upload errors say what went wrong', ...)   // ApiError 413 / 415 messages above
it('delete needs the word DELETE', ...)        // 'delete' and 'DELET' keep it disabled; 'DELETE' enables; confirm → deleteAccount and navigate to /login?deleted=...
it('log out on all devices', ...)              // logoutAll then logout then /login
it('Google connect asks for calendar and returns to /profile', ...)
// login page
it('shows the deletion notice', ...)            // ?deleted=2026-11-24T17:04:00Z → "…on Nov 24 at 9:04 AM. Log in before then to restore it."
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS** — `npx jest --ci`, `npx tsc --noEmit`, `npx eslint src --max-warnings 0`.
- [ ] **Step 5: Commit** `feat(web): the Profile screen (rows 29 and 32)`

---

### Task 11: Playwright, docs and the tracker

**Files:**
- Create: `apps/web/e2e/profile.spec.ts`
- Modify: `POC_PHASE2_TODO.md` (rows 29, 32 → "in review — branch `feat/profile-screen`"; row 39
  note: the shared header is its first piece), `docs/superpowers/specs/2026-11-17-profile-screen-design.md`
  ("Status and next steps": building), `scripts/ADMIN_CLI.md` only if behaviour it describes changed
  (admin soft delete still blocks: say so in one line if the doc mentions restore)

- [ ] **Step 1: Write the e2e tests** (each registers its own `e2e-profile-<Date.now()>@example.com`
  user through the API, logs in through the UI)

```ts
test('picking an icon shows it in the header on another page', ...)
  // /profile → click the 'panda' button → go to /diaries → header button contains the panda icon (data-icon="panda")
test('switching to °C shows °C on an entry badge', ...)
  // create diary + entry via API; set °C on /profile; page.route on GET /v1/entries/<id>: route.fetch(),
  // add a weather enrichment {kind:'weather', payload:{temperature_min_c:16, temperature_max_c:21}} to the
  // real JSON (keep its temperature_unit), fulfil; expect "16°C" and "21°C"
test('delete, log in, restore, diaries are back', ...)
  // create a diary; /profile → Delete account… → type DELETE → confirm → on /login see the notice;
  // log in → restore screen → Restore my account → /diaries lists the diary
```

- [ ] **Step 2: Run** `make test-e2e` (it brings the test stack up and down). Expected: the three
  new tests pass, nothing else regresses.
- [ ] **Step 3: Update the docs and tracker.**
- [ ] **Step 4: Full check** — `make test-all`, then (after it finishes, never during) the dev-stack
  check: `make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`.
  Expected: "All checks passed." and "17 passed, 0 failed" with the smoke cleanup line.
- [ ] **Step 5: Commit** `test(e2e): profile icon, °C badge, delete and restore; tracker`

---

## After the plan (not tasks — Andrew's gates)

1. Final whole-branch review on the most capable model, with this plan's Review Focus.
2. PR, CI green, merge (standing OK: merge when green).
3. Deploy only with Andrew's OK: dump the DB to `/var/backups/perfect-day/pre-pr<N>-…`, then
   `40-update.sh`. Migration 0020 runs during the deploy. Check smoke 17/0, `/me` has the new
   fields, no tracebacks.
4. Andrew's phone check (spec → Verification → By hand): upload a photo from the phone's library,
   check it top right; switch units and check a badge; on a test account delete → log in → restore.
