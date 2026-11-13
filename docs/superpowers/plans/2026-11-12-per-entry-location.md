# Per-entry Location and Time Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every entry gets its own place (filled automatically from photo GPS or calendar text, or set by the parent), weather follows it, diaries get a settable home place, and an advanced per-entry time zone moves event times and the entry's date.

**Architecture:**
- **A geocoding service** wraps Open-Meteo's search and the strict US rules, with a Redis cache.
- **A location service** picks an entry's place in the order parent > photo > event > home.
- **One worker task,** `refresh_entry_location`, works the place out again and refetches weather when the place or the dates change. The draft task, photo attach/detach, entry PATCH and diary PATCH all queue it.
- **The web** gets a shared city-search box, used on the entry page and in diary settings.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Celery, Redis, httpx, Next.js 15, Jest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-12-per-entry-location-design.md`

## Global Constraints

- **Order of sources:** parent > photo > event > home > none. A `parent` place is never replaced by anything automatic.
- **Strict rules, verbatim from the spec:**
  - **Drop the country:** a trailing "USA" or "United States" is removed.
  - **ZIP:** used only directly after a US state abbreviation, and the result must be in that state.
  - **City:** the text must end in a recognised state (abbreviation or full name, optionally followed by a ZIP). The city is the part just before it, never the first part, and never a part containing digits. Only `feature_code` starting `PPL` in that state is accepted.
  - **Anything else:** no match.
- **Wrong is worse than none:** when unsure, return no match. A failed lookup is not an error the parent sees.
- **Cache:** Redis, key = SHA-256 of the location text, 90-day expiry. A definite no-match is cached; a network or upstream failure is not.
- **Open-Meteo search URL:** `https://geocoding-api.open-meteo.com/v1/search`, no key. Credit link "Location and weather: Open-Meteo" → `https://open-meteo.com/`, wherever the place or the weather shows.
- **`location_source` values:** `parent | photo | event | home`, or null.
- **`tz_override`:** an IANA name or null. Unknown names → 422.
- **Permissions:** editors and owners may change an entry's place and time zone; only the owner may change the diary's home place; any member who can see the entry sees its place.
- **Database dependency:** every new endpoint uses `Depends(get_db, scope="function")` (D-8; a unit test enforces it).
- **Migration:** `0012`, with downgrade.

## Review Focus

1. **Wrong place from tricky text.** Inputs: a house number that looks like a ZIP, a venue name as the first part, a full state name, a theme park, non-US text. Expected: no match, never a wrong town. Pinned in Task 2's rule tests.
2. **Automatic run overwrites the parent's choice.** Input: regeneration or a photo added after the parent set a place. Expected: the parent's place stays. Pinned in Task 3.
3. **Stale weather.** Input: the place or the dates change. Expected: old weather rows are removed and new ones fetched, never mixed. Pinned in Tasks 3 and 4.
4. **Geocoder down.** Input: a timeout or 5xx from Open-Meteo. Expected: the entry falls back to the next source, nothing is cached, and the city search returns 503 `geocoding_unavailable` (the box shows "Search isn't working right now"). Pinned in Tasks 2, 3 and 6.
5. **Time zone on an entry without events.** Expected: the date is unchanged. And on a multi-day entry, both dates are recomputed. Pinned in Task 4.

## Calls in this plan beyond the spec (shown to Andrew before building)

1. **Diary home place reuses the existing fields.** It is stored as the existing `lat`/`lon` plus a new `location_name`, sent as flat fields on `PATCH /v1/diaries/{id}`, not as a nested `location` object.
2. **A failed city search is a 503,** `geocoding_unavailable`, not an empty list, so the box can say search is down instead of "no matches".
3. **Editing an entry's date by hand also refetches its weather,** by queuing the same refresh task. Today the weather stays on the old dates.
4. **The web unit tests are run explicitly** (`npx jest`), because `make test-all` doesn't run them.
5. **A plain two-part "City, ST" counts as a city.** The spec's rule (never the first part) also rejects "Seattle, WA", which is clearly a city. Proposed: when the whole text is exactly a city and a state (two parts, no digits), the first part may be the city. It is still checked against the state and must be a populated place. Tests: `"Seattle, WA"` → `("city", "Seattle", "Washington")`; `"Canlis, Seattle, WA"` still uses Seattle.

---

### Task 1: Schema for places and time zones

**Files:**
- Create: `apps/api/alembic/versions/0012_entry_location.py`
- Modify: `apps/api/app/models/__init__.py` (`Entry`, `Diary`)
- Test: `apps/api/tests/integration/test_entry_location.py` (new)

**Interfaces:**
- Produces:
  - `Entry.location_name: str | None`
  - `Entry.lat`, `Entry.lon`: `Numeric(9, 6) | None`
  - `Entry.location_source: str | None`, with check constraint `ck_entries_location_source` (`IN ('parent','photo','event','home')` or NULL)
  - `Entry.tz_override: str | None`
  - `Diary.location_name: str | None`

- [ ] **Step 1: Write the failing test.** `test_entry_stores_a_place_and_time_zone` creates an entry with all five fields set and reads them back. `test_location_source_rejects_unknown_values` expects an `IntegrityError` for `location_source="gps"`.
- [ ] **Step 2: Run it.** `cd apps/api && .venv/bin/pytest tests/integration/test_entry_location.py -q`. Expected: FAIL (`Entry` has no `location_name`).
- [ ] **Step 3: Implement** the migration (`down_revision="0011"`) and the model columns.
- [ ] **Step 4: Run it.** Same command. Expected: PASS.
- [ ] **Step 5: Check the migration both ways** on a scratch Postgres: `alembic upgrade head`, `downgrade -1`, `upgrade head`. Expected: no errors, and the columns are gone after the downgrade.
- [ ] **Step 6: Commit.** `feat(api): entry place and time zone columns (item 24)`

### Task 2: Geocoding service with the strict rules

**Files:**
- Create: `apps/api/app/services/geocoding.py`
- Test: `apps/api/tests/unit/test_geocoding_rules.py`, `apps/api/tests/integration/test_geocoding_cache.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Place: name: str; admin1: str | None; country_code: str | None; lat: float; lon: float; timezone: str | None`, with property `label -> str` ("Mukilteo, Washington", or name plus country outside the US).
  - `class GeocodingUnavailable(Exception)`
  - `async def search_places(query: str, *, count: int = 5, country_code: str | None = None) -> list[Place]`. Calls Open-Meteo. Raises `GeocodingUnavailable` on timeout, 5xx or a non-JSON reply. Returns `[]` for no results.
  - `def parse_us_location(text: str) -> tuple[Literal["zip","city"], str, str] | None`. Returns `(kind, query, state_name)`, with no network. These are the strict rules.
  - `async def place_from_event_text(text: str) -> Place | None`. Applies `parse_us_location`, searches with `country_code="US"`, accepts only a result whose `admin1 == state_name` (and, for `city`, whose `feature_code` starts `PPL`), and uses the Redis cache.
  - `US_STATES: dict[str, str]`, abbreviation → full name, including DC.

- [ ] **Step 1: Write failing rule tests** (`test_geocoding_rules.py`), one per shape. Each asserts `parse_us_location` returns the exact tuple, or `None`:
  - `"Woodinville, WA 98072, USA"` → `("zip", "98072", "Washington")`
  - `"Mukilteo Library, 4675 Harbour Pointe Blvd, Mukilteo, WA"` → `("city", "Mukilteo", "Washington")`
  - `"12345 Main St, Everett, WA"` → `("city", "Everett", "Washington")`. The house number is never read as a ZIP.
  - `"White River Amphitheatre, Auburn, Washington"` → `("city", "Auburn", "Washington")`
  - `"Nordstrom, 3200 184th St SW, Lynnwood (Alderwood)"` → `None` (no state)
  - `"Disneyland Park"` → `None`, and `"Home"` → `None`
  - `"Seattle, WA"` → `("city", "Seattle", "Washington")` (call 5: exactly "City, ST")
  - `"10 Downing St, London, UK"` → `None`
- [ ] **Step 2: Write failing service tests** (`test_geocoding_cache.py`, with `search_places` patched):
  - A `zip` match in another state → `None`.
  - A `city` result whose `feature_code` is `AMUS` → `None`.
  - A second call with the same text doesn't search again (cache hit), and neither does a cached no-match.
  - `GeocodingUnavailable` → `None`, and nothing is cached.
  - The Redis key is `geocode:` plus the SHA-256 hex of the text, and the raw text appears nowhere in Redis.
- [ ] **Step 3: Run both.** Expected: FAIL (module missing).
- [ ] **Step 4: Implement** `geocoding.py`:
  - Use `httpx.AsyncClient`, with the timeout from `settings.open_meteo_timeout_seconds`.
  - Set `language=en` on every search.
  - Cache via `get_redis()`, storing JSON of the `Place` or `null`, with a 90-day expiry.
- [ ] **Step 5: Run both.** Expected: PASS.
- [ ] **Step 6: Commit.** `feat(api): geocoding with strict US rules and a cache (item 24)`

### Task 3: Working out an entry's place, and refreshing weather

**Files:**
- Create: `apps/api/app/services/entry_location.py`
- Modify:
  - `apps/api/app/workers/enrichments.py`: `_resolve_lat_lon`, plus a weather-reset helper.
  - `apps/api/app/workers/tasks.py`: the new task `refresh_entry_location`, and a call in `_generate_entry_draft` before the weather step.
  - `apps/api/app/routers/v1/photos.py`: attach and detach queue the refresh.
- Test: `apps/api/tests/integration/test_entry_location.py`

**Interfaces:**
- Consumes: `place_from_event_text` (Task 2) and the Task 1 columns.
- Produces:
  - `async def resolve_entry_location(entry: Entry, db: AsyncSession) -> bool`. Sets `location_name`/`lat`/`lon`/`location_source` in the source order and returns `True` if `(lat, lon)` changed. It never touches a `parent` place.
    - A photo place has `location_name=None`.
    - Events are tried in `occurred_at` order, and the first whose `payload["location"]` resolves wins.
    - A home place uses `diary.location_name`.
  - `async def reset_weather(entry_id: uuid.UUID, db) -> int`. Deletes the entry's `kind="weather"` enrichments.
  - `_resolve_lat_lon(entry)` returns the entry's own `lat`/`lon` when set, before photos and diary.
  - Celery task `refresh_entry_location(entry_id: str) -> None`, with `_refresh_entry_location(entry_id_str, *, dates_changed: bool = False)`. It resolves the place. If the place or the dates changed, it resets the weather and calls `enrich_entry_weather`.

- [ ] **Step 1: Write failing tests:**
  - `test_photo_gps_beats_event_text`
  - `test_event_text_used_when_no_photo_gps` (geocoder patched)
  - `test_home_place_used_when_nothing_else` (`location_source="home"`)
  - `test_no_place_when_nothing_resolves` (all four fields null)
  - `test_parent_place_survives_regeneration`: run `_generate_entry_draft` with the LLM and geocoder patched; the `parent` place is unchanged.
  - `test_geocoder_down_falls_back_to_home`
  - `test_place_change_replaces_weather`: the old weather row is deleted and the new one comes from the new coordinates (patch `fetch_daily` and assert the call's `lat`/`lon`).
  - `test_attaching_a_photo_queues_a_refresh` and `test_detaching_a_photo_queues_a_refresh` (patch `refresh_entry_location.delay`, and assert it's called after the commit).
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement** the service, the helper, the task, the draft hook and the photo hooks.
- [ ] **Step 4: Run** the file plus `tests/integration/test_weather_enrichment.py`. Expected: all PASS (the existing weather tests still pass).
- [ ] **Step 5: Commit.** `feat(api): entries work out their own place; weather follows it (item 24)`

### Task 4: Entry API: place, time zone and the dates they move

**Files:**
- Modify:
  - `apps/api/app/routers/v1/entries.py`: `EntryPatch`, `EntryOut`, `_entry_out_from_orm`, `patch_entry`.
  - `apps/api/app/workers/tz_utils.py`: the new date helper.
- Test: `apps/api/tests/integration/test_entry_location.py`, `apps/api/tests/unit/test_tz_utils.py`

**Interfaces:**
- Consumes: `refresh_entry_location` (Task 3).
- Produces:
  - `class LocationIn(BaseModel): name: str (1–200); lat: float [-90, 90]; lon: float [-180, 180]`
  - `EntryPatch.location: LocationIn | None` (explicit `null` = use automatic location) and `EntryPatch.tz_override: str | None`.
  - `class LocationOut(BaseModel): name: str | None; lat: float; lon: float; source: str`. `EntryOut.location: LocationOut | None` and `EntryOut.tz_override: str | None`.
  - `def entry_dates_from_events(events: list[dict], tz_name: str) -> tuple[date, date | None] | None`. Takes the earliest start and the latest end across `google_event_to_entry_date` for each event. The end is `None` when it equals the start. Returns `None` when no event has a usable date.

- [ ] **Step 1: Write failing tests:**
  - Unit, `entry_dates_from_events`:
    - A 9:00 JST event on 2026-05-16 gives 2026-05-16 in `Asia/Tokyo` and 2026-05-15 in `America/Los_Angeles`.
    - Two events on different days give a range.
    - An all-day event is the same in any zone.
  - Integration:
    - `test_parent_sets_a_place`: `PATCH {"location": {...}}` gives `source == "parent"` and queues a refresh.
    - `test_null_location_returns_to_automatic`: the source goes back to an automatic one after the refresh runs.
    - `test_time_zone_moves_the_date_of_an_entry_with_events`
    - `test_time_zone_leaves_an_entry_without_events_alone`
    - `test_multi_day_entry_dates_recomputed`
    - `test_clearing_the_time_zone_uses_the_diary_zone`
    - `test_unknown_time_zone_is_422`
    - `test_viewer_cannot_change_the_place` (403)
    - `test_editing_the_date_by_hand_queues_a_refresh` (call 3)
    - `test_entry_out_shows_place_to_a_viewer_of_a_published_entry`
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** In `patch_entry`:
  - Validate `tz_override` with `zoneinfo.ZoneInfo`. Failure → 422 `unknown_time_zone`.
  - On a `tz_override` change, recompute the dates from `entry.events` payloads, using `tz_override or diary.timezone`.
  - Set or clear the parent place.
  - Queue `refresh_entry_location` after the commit, with `dates_changed` when the dates changed.
- [ ] **Step 4: Run** the file and `tests/unit/test_tz_utils.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): set an entry's place and time zone (item 24)`

### Task 5: Diary home place

**Files:**
- Modify: `apps/api/app/routers/v1/diaries.py` (`DiaryPatch`, `DiaryOut`, `_CLEARABLE_DIARY_FIELDS`, `patch_diary`), `apps/api/app/workers/tasks.py` (new task).
- Test: `apps/api/tests/integration/test_entry_location.py`

**Interfaces:**
- Produces:
  - `DiaryPatch.location_name: str (≤200) | None`, clearable. `lat`/`lon` already exist.
  - `DiaryOut.location_name`.
  - A rule: `location_name`, `lat` and `lon` are set or cleared together; sending only some of them → 422 `incomplete_location`.
  - Celery task `refresh_diary_locations(diary_id: str)`. Queues `refresh_entry_location` for each live entry whose `location_source` is `home` or null.

- [ ] **Step 1: Write failing tests:**
  - `test_owner_sets_home_place_and_entries_follow`
  - `test_editor_cannot_set_home_place` (403)
  - `test_partial_home_place_is_422`
  - `test_parent_place_ignores_home_change`
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): a diary's home place (item 24)`

### Task 6: City search endpoint

**Files:**
- Create: `apps/api/app/routers/v1/geocode.py`. Modify: `apps/api/app/main.py` (`include_router`, prefix `/v1`).
- Test: `apps/api/tests/integration/test_geocode_endpoint.py`

**Interfaces:**
- Consumes: `search_places` (Task 2).
- Produces: `GET /v1/geocode?q=<2–100 chars>` → `list[GeocodeResult]`, where `GeocodeResult = {name, admin1, country, lat, lon, timezone, label}`, with at most 5 results.
  - 401 without sign-in.
  - 503 `geocoding_unavailable` when `GeocodingUnavailable` is raised.
  - 422 for a query shorter than 2 characters.
  - The default per-user rate limit applies.

- [ ] **Step 1: Write failing tests:**
  - `test_needs_sign_in`
  - `test_returns_up_to_five_places` (`search_places` patched)
  - `test_short_query_is_422`
  - `test_geocoder_down_is_503`
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** them plus `tests/unit/test_get_db_scope.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): city search endpoint (item 24)`

### Task 7: Web: place, city search, credit and advanced time zone

**Files:**
- Create:
  - `apps/web/src/components/PlaceSearch.tsx`
  - `apps/web/src/components/__tests__/PlaceSearch.test.tsx`
  - `apps/web/e2e/entry-location.spec.ts`
- Modify:
  - `apps/web/src/lib/api.ts`
  - `apps/web/src/components/WeatherBadge.tsx` (+ its test)
  - `apps/web/src/app/entries/[entryId]/page.tsx`
  - `apps/web/src/components/DiaryForm.tsx` (edit mode only)
  - `apps/web/src/app/diaries/[diaryId]/settings/page.tsx`

**Interfaces:**
- Consumes: Tasks 4–6 endpoints.
- Produces:
  - `api.ts`:
    - `interface GeocodeResult`
    - `Entry.location: {name: string | null; lat: number; lon: number; source: 'parent'|'photo'|'event'|'home'} | null`
    - `Entry.tz_override: string | null`
    - `Diary.location_name`
    - `api.geocode.search(q)` (throws `GeocodingUnavailableError` on 503)
    - `api.entries.update` accepts `location` and `tz_override`
  - `<PlaceSearch label: string; onSelect(place: GeocodeResult): void />`:
    - Debounced 300 ms, searching from 2 characters.
    - Results shown as `label`.
    - "No matches" when empty; "Search isn't working right now" on 503.
  - `formatEventTime(event, timeZone?: string)`: when `timeZone` is set, times use `Intl` `timeZone` and carry a short zone name.

- [ ] **Step 1: Write failing Jest tests:**
  - `PlaceSearch`:
    - Waits for 2 characters.
    - Shows labels.
    - Calls `onSelect`.
    - Shows the 503 message.
  - `WeatherBadge`:
    - Renders a link with the text "Location and weather: Open-Meteo" to `https://open-meteo.com/`.
- [ ] **Step 2: Write the failing Playwright spec** `entry-location.spec.ts`. It mocks `/v1/geocode` with `page.route` and seeds the entry with `psql` like `suggestion-box.spec.ts`:
  - The entry shows its place name and the credit.
  - An entry with a photo place shows "From a photo".
  - Edit location → search "Muk" → choose → the place shows, and the PATCH body carries `location`.
  - "Use automatic location" sends `location: null`.
  - "Advanced" is collapsed by default. Choosing `Asia/Tokyo` sends `tz_override`, and event times show "JST".
  - Diary settings: the Home location search saves `location_name`/`lat`/`lon`.
  - A viewer sees the place but no Edit location.
- [ ] **Step 3: Run them.**
  - Jest: `cd apps/web && npx jest PlaceSearch WeatherBadge`. Expected: FAIL.
  - Playwright: the spec, against the test stack. Expected: FAIL.
- [ ] **Step 4: Implement** the component, the badge credit, the entry page (place line, Edit location, Use automatic location, Advanced time zone with the `Intl.supportedValuesOf('timeZone')` list and a "Diary time zone" default) and the settings field.
- [ ] **Step 5: Run.**
  - `npx jest`: all pass.
  - `npx tsc --noEmit`, `npx eslint src --max-warnings 0`: clean.
  - The Playwright spec: PASS.
- [ ] **Step 6: Commit.** `feat(web): entry place, city search, advanced time zone (item 24)`

### Task 8: Docs, full suite and the real-calendar check

**Files:**
- Modify:
  - `POC_PHASE2_TODO.md`: item 24 status, and the scaffold row points to the spec.
  - `design/02-data-model.md` and `design/03-api-surface.md`: the new columns and endpoints.
  - `docs/BACKLOG.md`: P-6 notes the entry columns now exist.

- [ ] **Step 1: Update the docs.**
- [ ] **Step 2: Run `make test-all`, then `cd apps/web && npx jest`.** Expected: all green. Report any flaky test by name.
- [ ] **Step 3: Real-calendar check.** On the local dev stack, with Andrew's data:
  - Run `refresh_entry_location` for every live entry.
  - Report how many entries got each `location_source`.
  - List a sample of 10 `event` places, each with the entry's event location, shown to Andrew locally, not sent anywhere else.
  - Expected: no entry placed outside the state its text names.
- [ ] **Step 4: Commit.** `docs: item 24 per-entry location`
