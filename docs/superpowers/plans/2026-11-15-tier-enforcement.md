# Tier Enforcement (Free / Plus / Family) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the Free / Plus / Family limits in the API and show a Free parent their limits: a coloured badge with the reset date and Upgrade now, and grayed-out controls with a note at each limit.

**Architecture:**
- **Plan data.** One plan table in `services/tier.py` feeds everything.
- **AI drafts** are spent by a reservation row in a new `ai_draft_spends` table. `generate_draft_for_entry` takes the reservation right before the LLM call, under a per-user advisory lock, and the Celery task id makes it idempotent. The reservation is released when no AI text is saved.
- **Read-only diaries** are computed from the owner's plan and never stored. The guard is one function, called in each blocked route and at each worker entry.
- **The web app** reads the owner's plan from the diary and entry detail responses, and the user's own plan from `GET /v1/auth/me/plan`.

**Tech Stack:** FastAPI 0.162.5, SQLAlchemy async, Alembic (head 0016, this plan adds 0017), Celery, pytest with testcontainers on Python 3.12; Next.js 15, Jest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-15-tier-enforcement-design.md`. Read it. It is the authority, and this plan argues from it.

## Global Constraints

- **Branch:** `feat/18-tier-enforcement`, cut from `feat/14-photos-on-drafts`. Photo gating adds checks to feat/14's attach and import paths without restructuring them.
- **Limits:**

  | Plan | Diaries | AI drafts per month | Photos per entry |
  |---|---|---|---|
  | `free` | 1 | 5 | 1 |
  | `plus` | 2 | unlimited | unlimited |
  | `family` | 4 | unlimited | unlimited |

  Manual entries are unlimited on every plan. An unknown tier value is treated as `free`.
- **Blocked requests:** 403 with `detail={"code": "tier_limit", "details": {"limit", "current", "source", "required_tier"}}`.
  - `source` is one of `diary`, `ai_draft`, `photo`, `diary_read_only`.
  - AI-draft errors also carry `"resets_on": "YYYY-MM-DD"`.
  - The error handler already passes this shape through (`core/errors.py:36`) as `{"error": {"code", "details"}}`.
- **Whose plan:** always the **diary owner's**.
- **Copy:** use these strings exactly. `{date}` is formatted "Dec 1" (en-US, short month, day, no year).
  - Badge: `AI drafts: {left} of {limit} left · resets {date}`
  - Badge: `Diaries: {left} of {limit} left`
  - Button: `Upgrade now` (links to `/plans`)
  - AI note: `No AI drafts left this month. Resets {date}.`
  - Entry note: `Out of AI drafts until {date}. Write it yourself, or generate it then.`
  - Photo note: `Free plan: 1 photo per entry.`
  - Diary note: `{Plan} plan: {n} diary.` or `{Plan} plan: {n} diaries.`
  - Read-only banner: `This diary is read-only on the {Plan} plan. Upgrade, or delete another diary, to use it again.`
  - Plans page: heading `Plans`, plus the line `Paid plans are coming soon.`
- **Colours:** green when `used == 0`, yellow when `0 < used < limit`, red when `used >= limit`. Use the CSS tokens; the mockup used `#2e7d32`, `#f9a825` and `#c62828`. The badge never shows a negative number (`left = max(0, limit - used)`).
- **No badge on Plus or Family.** Notes and grayed-out controls still apply at their limits.
- **Code rules:**
  - Every `Depends(get_db)` uses `scope="function"`.
  - No new dependencies.
  - `ruff check app tests` and mypy are clean; `tsc` and eslint are clean.
- **Tests:** patch async functions with `AsyncMock`; no "coroutine never awaited" warnings.
- **Git:**
  - Stage explicit paths. Never use `git add -A` or `git commit -a`.
  - Never run two implementers at once.
  - Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Commands:**
  - API: `cd apps/api && .venv/bin/pytest <paths> -q`. Integration tests start their own Postgres via testcontainers.
  - Web: `cd apps/web && npx jest <paths>`.
  - The whole thing: `make test-all`. It ends with `docker compose ... down -v`, which also stops Andrew's dev containers. Data survives, and `make infra` restarts them.
- **Finish:** `make test-all` is green, and so is `cd apps/web && npx jest`.

## Review Focus

1. **Concurrent spending:** two Regenerates, or a Regenerate racing a scan, at 4 of 5 must give exactly 5 used, never 6. (Task 3 test `test_concurrent_reservations_never_exceed_limit`.)
2. **A Celery redelivery of the same draft task** must not spend twice. (Task 3 test `test_same_key_reserves_once`.)
3. **Two photos attached at the same moment** to an empty entry on Free must leave exactly one. (Task 5 test `test_concurrent_attach_keeps_one_photo`.)
4. **Moving back up a plan** must reactivate read-only diaries immediately, with no stored state to clean up. (Task 2 test `test_upgrade_reactivates_read_only_diary`.)
5. **The usage month uses the diary's timezone,** not UTC. 23:30 on Nov 30 in `America/Los_Angeles` is still November. (Task 3 test `test_month_follows_oldest_diary_timezone`.)

---

### Task 1: Plans, rename and usage table

**Files:**
- Modify: `apps/api/app/services/tier.py` (rewrite; delete `TIER_ENTRY_LIMITS`, `enforce_entry_tier_limit`, `try_enforce_entry_tier_limit`)
- Create: `apps/api/alembic/versions/0017_plans_and_ai_draft_spends.py`
- Modify: `apps/api/app/models/__init__.py:100` (constraint), and add model `AiDraftSpend`
- Modify:
  - `apps/api/app/routers/v1/diaries.py:20,194-213,349-373`: use `limits_for` and `tier_limit_error`; drop `TIER_DIARY_LIMITS`.
  - `apps/api/app/routers/v1/entries.py:467-472,684-689`: remove the entry-count checks.
  - `apps/api/app/routers/v1/calendar_events.py:125-130`: remove the check.
  - `apps/api/app/workers/rules.py:206-215`: remove the check; the entry is always created.
- Modify: `scripts/manage.py:107-108,178-179` (choices `free`, `plus`, `family`) and `scripts/ADMIN_CLI.md:42-49`
- Modify: `apps/web/e2e/entry-location.spec.ts:51-52` (use `'plus'`, and update the comment)
- Delete: `tests/integration/test_entry_tier_limits.py`, `tests/integration/test_rule_tier_limit.py` and `tests/unit/test_tier_worker_helper.py`. They encode the superseded lifetime limits.
- Test: `tests/unit/test_plans.py`, `tests/integration/test_migration_0017.py`, `tests/integration/test_plan_limits.py`

**Interfaces:**
- Produces, in `app/services/tier.py`:
  ```python
  LimitSource = Literal["diary", "ai_draft", "photo", "diary_read_only"]

  @dataclass(frozen=True)
  class PlanLimits:
      diaries: int
      ai_drafts_per_month: int | None  # None = unlimited
      photos_per_entry: int | None

  PLAN_LIMITS: dict[str, PlanLimits]   # keys "free", "plus", "family"
  PLAN_NAMES: dict[str, str]           # {"free": "Free", "plus": "Plus", "family": "Family"}

  def limits_for(tier: str) -> PlanLimits: ...
  def required_tier(source: LimitSource, needed: int) -> str: ...
  def tier_limit_error(*, source: LimitSource, limit: int, current: int,
                       needed: int, resets_on: date | None = None) -> HTTPException: ...
  ```
  - `limits_for`: an unknown tier gives `free`.
  - `required_tier`: the cheapest plan whose limit for `source` allows `needed`, where `None` means unlimited. It returns `"family"` when none allows it. `diary_read_only` uses the diary limit.
  - `tier_limit_error`: a 403 whose details carry `required_tier=required_tier(source, needed)`. It adds `"resets_on": d.isoformat()` when given.
- Produces, as a model: `AiDraftSpend`, table `ai_draft_spends`, with these columns:
  - `id` UUID primary key;
  - `user_id` → `users.id` ON DELETE CASCADE, NOT NULL;
  - `entry_id` → `entries.id` ON DELETE SET NULL, nullable;
  - `month` String(7), NOT NULL;
  - `key` Text, NOT NULL, UNIQUE;
  - `created_at` timestamptz, server default now();
  - index `ix_ai_draft_spends_user_month (user_id, month)`.

- [ ] **Step 1: Write the failing tests.**
  - `test_plans.py`:
    - `limits_for("free") == PlanLimits(1, 5, 1)`, `limits_for("plus") == PlanLimits(2, None, None)` and `limits_for("family") == PlanLimits(4, None, None)`;
    - `limits_for("tier1") == limits_for("free")`;
    - `required_tier("diary", 2) == "plus"`, `required_tier("diary", 3) == "family"` and `required_tier("diary", 9) == "family"`;
    - `required_tier("ai_draft", 6) == "plus"` and `required_tier("photo", 2) == "plus"`;
    - `tier_limit_error(source="ai_draft", limit=5, current=5, needed=6, resets_on=date(2026,12,1))` has status 403 and `detail == {"code": "tier_limit", "details": {"limit": 5, "current": 5, "source": "ai_draft", "required_tier": "plus", "resets_on": "2026-12-01"}}`.
  - `test_migration_0017.py`, in the style of `test_migration_0016.py`:
    - upgrade to 0016, insert users with tiers `free`, `tier1` and `tier2`, upgrade to 0017;
    - assert the tiers are `free`, `plus` and `family`;
    - assert inserting `tier1` now violates `ck_users_subscription_tier`;
    - assert `ai_draft_spends` exists with a unique `key`;
    - downgrade to 0016 and assert the tiers are back to `free`, `tier1` and `tier2`.
  - `test_plan_limits.py`:
    - `test_free_user_creates_sixth_manual_entry` (201);
    - `test_plus_second_diary_allowed_third_blocked`: the 3rd gives 403 with details `source="diary"`, `limit=2`, `current=2` and `required_tier="family"`;
    - `test_family_fourth_diary_allowed`;
    - `test_free_diary_limit_says_plus`;
    - `test_rule_creates_entry_past_old_auto_limit`: a Free user with 3 existing auto entries gets a 4th from a rule match.
- [ ] **Step 2: Run them, and check they fail.**
  - Run: `cd apps/api && .venv/bin/pytest tests/unit/test_plans.py tests/integration/test_migration_0017.py tests/integration/test_plan_limits.py -q`
  - Expected: they FAIL, with an ImportError on `PlanLimits` and the missing revision 0017.
- [ ] **Step 3: Implement.**
  - **Migration 0017:**
    - it does `UPDATE users SET subscription_tier = CASE ... END` before swapping the check constraint to `IN ('free','plus','family')`;
    - it creates `ai_draft_spends`;
    - the downgrade reverses both.
  - **Remove the old checks** at the call sites listed under Files, plus their imports.
  - **The rule in `rules.py`** creates the entry unconditionally. Delete the tier-limit branch and its log line, and update the docstring at `rules.py:178-181`.
- [ ] **Step 4: Run Step 2's command, plus `tests/integration/test_diaries.py tests/integration/test_trash.py tests/integration/test_rules_evaluation.py`.**
  - Expected: PASS. Fix any test there that asserted lifetime entry limits by deleting that assertion.
- [ ] **Step 5: Commit.** Use the message `feat(api): Free/Plus/Family plans and the AI draft usage table`.

### Task 2: Read-only diaries over the plan's limit

**Files:**
- Create: `apps/api/app/services/read_only.py`
- Modify, to add `await ensure_diary_writable(db, diary)` after the access check:
  - `routers/v1/entries.py`: create 454, patch 508, publish 576, suggestion accept 596, suggestion dismiss 623, unpublish 637, restore 659, regenerate 694;
  - `routers/v1/diaries.py`: patch 280;
  - `routers/v1/scan.py`: run 78, backfill start 166;
  - `routers/v1/rules.py`: 168, 214, 247, 263, 318;
  - `routers/v1/calendar_events.py`: 95;
  - `routers/v1/photos.py`: attach 285, detach 335;
  - `routers/v1/photo_imports.py`: create 91.
- **Not guarded** (allowed on read-only diaries): every GET, entry delete 649, diary delete 317, diary restore 334 (it keeps its own diary-limit check), backfill cancel 234, photo-import cancel 193 and dismiss 215, and `DELETE /photos/{id}` 250.
- Modify, to add worker skips that return early with log line `diary_read_only_skip` and fields `diary_id` and `where`:
  - `workers/tasks.py` `_scan_diary` (70) and `_backfill_diary` (865);
  - `workers/rules.py` `evaluate_event_against_rules` (91);
  - `workers/llm.py` `generate_draft_for_entry` (504): skip before any LLM call, and write no generation row;
  - `workers/beat_tasks.py` `_dispatch_due_scans` (18);
  - `workers/photo_imports.py` `_can_still_edit` (333): return False when the diary is read-only.
- Test: `tests/integration/test_read_only_diaries.py`

**Interfaces:**
- Consumes: `limits_for` and `tier_limit_error` from Task 1.
- Produces, in `app/services/read_only.py`:
  ```python
  async def read_only_diary_ids(db: AsyncSession, owner_user_id: uuid.UUID, tier: str) -> set[uuid.UUID]
  async def is_diary_read_only(db: AsyncSession, diary: Diary) -> bool
  async def ensure_diary_writable(db: AsyncSession, diary: Diary) -> None
  ```
  - `read_only_diary_ids`: the owner's diaries that are not deleted, ordered by `created_at ASC, id ASC`. Every one after the first `limits_for(tier).diaries` is read-only.
  - `is_diary_read_only` loads the owner's tier itself.
  - `ensure_diary_writable` raises `tier_limit_error(source="diary_read_only", limit=<plan diaries>, current=<active count>, needed=<active count>)`.

- [ ] **Step 1: Write the failing tests** in `test_read_only_diaries.py`. Set up a Free owner with diaries A (oldest) and B, created by inserting directly or by downgrading from Plus.
  - `test_oldest_diary_stays_writable`: PATCH on an entry in A gives 200.
  - `test_read_only_blocks_writes`, parametrized over one request per guarded route listed above, in diary B: each gives 403 with `detail.code == "tier_limit"` and `details.source == "diary_read_only"`.
  - `test_read_only_allows_reads_and_deletes`: GET on the B entry gives 200, DELETE on the B entry gives 204, and DELETE on diary B gives 200.
  - `test_upgrade_reactivates_read_only_diary`: set the tier to `plus`, and the same PATCH in B gives 200.
  - `test_editor_sees_owner_read_only`: an editor of B gets the same 403.
  - `test_workers_skip_read_only`:
    - `_scan_diary(B)` and `generate_draft_for_entry(<B entry>)` return without calling the provider. Patch `AnthropicProvider.generate` with `AsyncMock` and assert it was not awaited, and that no `llm_generations` row was added.
    - `_can_still_edit` is False for a B entry.
- [ ] **Step 2: Run them, and check they fail.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_read_only_diaries.py -q`
  - Expected: FAIL. The guarded routes return 200 or 201.
- [ ] **Step 3: Implement the service and add the calls** at the listed lines.
- [ ] **Step 4: Run Step 2's command, plus `tests/integration -q -x`.**
  - Expected: PASS. Existing tests use one diary per Free user; any that make two must set `subscription_tier="plus"` on the factory.
- [ ] **Step 5: Commit.** Use the message `feat(api): diaries over the plan's limit are read-only`.

### Task 3: Spending AI drafts in the worker

**Files:**
- Create: `apps/api/app/services/ai_drafts.py`
- Modify:
  - `apps/api/app/workers/llm.py`, `generate_draft_for_entry` at 504-890. Reserve in the first session, once `mode` is known (after line 591). Release in the second session.
  - `apps/api/app/workers/tasks.py:701-735`: pass `self.request.id` through `_generate_entry_draft` as `reservation_key`.
- Test: `tests/integration/test_ai_drafts.py`

**Interfaces:**
- Consumes: `limits_for` and `AiDraftSpend` from Task 1.
- Produces, in `app/services/ai_drafts.py`:
  ```python
  AI_DRAFT_LIMIT_ERROR = "ai_draft_limit"   # llm_generations.error when the limit stopped a draft

  @dataclass(frozen=True)
  class UsagePeriod:
      month: str        # "2026-11"
      resets_on: date   # first day of the next month

  async def usage_period(db, owner_user_id: uuid.UUID, now: datetime | None = None) -> UsagePeriod
  async def drafts_used(db, owner_user_id: uuid.UUID, month: str) -> int
  async def ai_drafts_left(db, owner: User, now: datetime | None = None) -> int | None   # None = unlimited; never < 0
  async def reserve_ai_draft(db, *, owner: User, entry_id: uuid.UUID, key: str,
                             now: datetime | None = None) -> bool
  async def release_ai_draft(db, key: str) -> None
  ```
  - `usage_period`: the month runs in the timezone of the owner's oldest non-deleted diary, falling back to UTC.
  - `reserve_ai_draft`:
    - takes `pg_advisory_xact_lock(hashtext('ai_drafts:' || user_id))`;
    - returns True when a row with `key` already exists;
    - otherwise inserts a row when the plan is unlimited or `used < limit`, and returns True;
    - returns False when the limit is reached.
    - Usage rows are written on every plan.
  - `release_ai_draft` deletes the row with that key.
  - `generate_draft_for_entry` gains the keyword parameter `reservation_key: str | None = None`. When it's `None`, a `uuid4` hex is used.

- [ ] **Step 1: Write the failing tests** in `test_ai_drafts.py`:
  - `test_month_follows_oldest_diary_timezone`: diary tz `America/Los_Angeles`, `now=2026-12-01T07:30Z` gives `month == "2026-11"` and `resets_on == date(2026,12,1)`. With no diary, the same `now` gives `"2026-12"`.
  - `test_reserve_until_limit`: on Free, 5 reserves return True and the 6th returns False; `ai_drafts_left == 0`.
  - `test_same_key_reserves_once`: reserving the same key twice gives `drafts_used == 1`.
  - `test_concurrent_reservations_never_exceed_limit`: at 4 used, `asyncio.gather` of 3 reserves in separate sessions gives exactly one True, and `drafts_used == 5`.
  - `test_deleting_entry_keeps_usage`: hard-delete the entry, and `drafts_used` is unchanged (`entry_id` is NULL).
  - `test_plus_records_usage_and_downgrade_shows_zero`: 20 reserves on Plus, then the tier becomes `free`, and `ai_drafts_left == 0`.
  - `test_successful_draft_spends_one`: `generate_draft_for_entry` with a provider `AsyncMock` returning valid JSON gives `drafts_used == 1`.
  - `test_failed_draft_releases`: the provider raises `LLMPermanentError` on every provider, and `drafts_used == 0`.
  - `test_discarded_draft_releases`: the entry is deleted between the two sessions (patch the provider to soft-delete the entry first), and `drafts_used == 0`.
  - `test_limit_writes_fallback_and_marker`:
    - at 5 of 5, a rule entry with events and no body gets the fallback title and body (`body_source == "fallback"`);
    - one `llm_generations` row has `status="failed"`, `model="none"` and `error == "ai_draft_limit"`;
    - the provider is not awaited.
  - `test_limit_leaves_parents_text`: at 5 of 5, an entry with parent-written text is unchanged, and the marker row is written.
  - `test_editor_regenerate_spends_owner_quota`: a draft on the owner's entry adds a row with `user_id == owner.id`.
- [ ] **Step 2: Run them, and check they fail.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_ai_drafts.py -q`
  - Expected: FAIL, with an ImportError on `app.services.ai_drafts`.
- [ ] **Step 3: Implement.**
  - **The reservation's owner** is the diary owner, loaded in the first session.
  - **Release** in the second session on every path that does not save AI text. That covers:
    - a failed generation row;
    - the fallback;
    - "entry deleted or published meanwhile";
    - a suggestion that is not usable.
  - **At the limit:**
    - write the marker row and bump `updated_at`, so the frontend's polling resolves;
    - for mode `events` with no body, also write the deterministic fallback via `_build_fallback_body`;
    - never call a provider.
- [ ] **Step 4: Run Step 2's command, plus `tests/integration/test_regenerate_endpoint.py tests/integration/test_suggestions.py tests/integration/test_rules_evaluation.py -q`.**
  - Expected: PASS.
- [ ] **Step 5: Commit.** Use the message `feat(api): AI drafts are reserved per month and released on failure`.

### Task 4: Regenerate gate and plan responses

**Files:**
- Create: `apps/api/app/services/plan_view.py`
- Modify:
  - `routers/v1/entries.py` regenerate (694-705): return 403 before queuing when `ai_drafts_left == 0`.
  - `routers/v1/entries.py` `EntryOut` (147) and `GET /entries/{entry_id}` (498): fill `plan`.
  - `routers/v1/diaries.py` `DiaryOut` (77) and `GET /{diary_id}` (270): fill `plan`.
  - `routers/v1/auth.py`: add `GET /me/plan` next to `/me` (400).
- Test: `tests/integration/test_plan_endpoints.py`

**Interfaces:**
- Consumes:
  - `ai_drafts_left`, `usage_period` and `drafts_used` (Task 3);
  - `read_only_diary_ids` and `is_diary_read_only` (Task 2);
  - `limits_for`, `PLAN_NAMES` and `tier_limit_error` (Task 1).
- Produces, in `app/services/plan_view.py`:
  ```python
  class OwnerPlanOut(BaseModel):
      tier: str
      plan_name: str
      ai_drafts_limit: int | None
      ai_drafts_left: int | None
      resets_on: date
      photos_per_entry: int | None
      read_only: bool

  class MyPlanOut(BaseModel):
      tier: str
      plan_name: str
      diaries_limit: int
      diaries_used: int
      ai_drafts_limit: int | None
      ai_drafts_used: int
      ai_drafts_left: int | None
      resets_on: date
      photos_per_entry: int | None
      read_only_diary_ids: list[uuid.UUID]

  async def owner_plan_out(db: AsyncSession, diary: Diary) -> OwnerPlanOut
  async def my_plan_out(db: AsyncSession, user: User) -> MyPlanOut
  ```
  - `DiaryOut.plan: OwnerPlanOut | None = None` and `EntryOut.plan: OwnerPlanOut | None = None`. They are filled only by the two detail GETs; every other response leaves them `None`.
  - The route is `GET /v1/auth/me/plan`, returning `MyPlanOut`.

- [ ] **Step 1: Write the failing tests** in `test_plan_endpoints.py`:
  - `test_regenerate_blocked_at_limit`: at 5 of 5, regenerate gives 403 with details `{"limit": 5, "current": 5, "source": "ai_draft", "required_tier": "plus", "resets_on": <first of next month>}`. Patch `generate_entry_draft.delay` and assert it was not called.
  - `test_regenerate_allowed_below_limit`: 200, and `delay` is called once.
  - `test_editor_blocked_by_owner_quota`: an editor of a Free owner at 5 of 5 gets 403.
  - `test_me_plan_free`: with 1 diary and 2 drafts used, gives `diaries_used == 1`, `ai_drafts_used == 2`, `ai_drafts_left == 3` and `photos_per_entry == 1`.
  - `test_me_plan_plus`: `ai_drafts_limit is None`, `ai_drafts_left is None` and `photos_per_entry is None`.
  - `test_diary_and_entry_detail_carry_owner_plan`: an editor's GET of the owner's diary and entry shows the owner's `tier` and `ai_drafts_left`, and `read_only` matches.
- [ ] **Step 2: Run them, and check they fail.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_plan_endpoints.py -q`
  - Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run Step 2's command, plus `tests/integration/test_entries.py tests/integration/test_diaries.py tests/integration/test_auth_flow.py -q`.**
  - Expected: PASS.
- [ ] **Step 5: Commit.** Use the message `feat(api): plan and usage in diary, entry and me responses; Regenerate gated`.

### Task 5: Photos per entry

**Files:**
- Create: `apps/api/app/services/photo_limits.py`
- Modify:
  - `routers/v1/photos.py` attach (285-331): call `ensure_photo_room` before inserting, only when the photo is not already linked.
  - `routers/v1/photo_imports.py` create (91-130):
    - return 403 when the room is 0;
    - when the room is limited, call `create_session(max_items=min(MAX_PICK, room))`.
  - `workers/photo_imports.py` `_copy` (233-330): just before `_attach`, if `photo_room(...) == 0`, stop the loop. Do not download further items, end the import as `done`, and delete the session as the other exits do.
- Test: `tests/integration/test_photo_limits.py`

**Interfaces:**
- Consumes: `limits_for` and `tier_limit_error` (Task 1).
- Produces, in `app/services/photo_limits.py`:
  ```python
  async def photo_room(db: AsyncSession, entry_id: uuid.UUID, owner: User, *, lock: bool = True) -> int | None
  async def ensure_photo_room(db: AsyncSession, entry_id: uuid.UUID, owner: User) -> None
  ```
  - `photo_room` returns `None` for unlimited; otherwise `max(0, limit - attached)`. With `lock=True` it takes `SELECT ... FOR UPDATE` on the entry row before counting `entry_photos`.
  - `ensure_photo_room` raises `tier_limit_error(source="photo", limit=1, current=<attached>, needed=<attached>+1)`.
  - `owner` is always the diary owner.

- [ ] **Step 1: Write the failing tests** in `test_photo_limits.py`:
  - `test_free_second_photo_blocked`: the 2nd attach gives 403 with `source == "photo"` and `required_tier == "plus"`.
  - `test_reattach_same_photo_is_not_counted`: attaching the already-attached photo again gives 201.
  - `test_plus_attaches_many`: 3 attaches give 201.
  - `test_concurrent_attach_keeps_one_photo`: on an empty Free entry, `asyncio.gather` of two attaches of different photos, through two clients and two sessions, gives exactly one 201 and one 403, and `entry_photos` count is 1.
  - `test_legacy_entry_keeps_its_photos`: a Free entry with 3 photos inserted directly still lists 3, and a 4th attach gives 403.
  - `test_import_full_entry_403`.
  - `test_import_caps_picker_to_room`: assert the `create_session` mock was awaited with `max_items=1`.
  - `test_import_worker_stops_at_room`: 2 picked items on an empty Free entry give `added_count == 1`, the second item is not downloaded, and the status is `done`.
  - `test_editor_in_plus_owner_diary_unlimited`.
- [ ] **Step 2: Run them, and check they fail.**
  - Run: `cd apps/api && .venv/bin/pytest tests/integration/test_photo_limits.py -q`
  - Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run Step 2's command, plus `tests/integration/test_photos.py tests/integration/test_photo_imports.py tests/integration/test_photo_upload_direct.py tests/integration/test_shared_photos.py -q`.**
  - Expected: PASS. Existing tests that attach several photos to one entry on Free set `subscription_tier="plus"`.
- [ ] **Step 5: Commit.** Use the message `feat(api): Free plan allows 1 photo per entry`.

### Task 6: Web building blocks: badge, note, plans page

**Files:**
- Create: `apps/web/src/lib/plan.ts`, `apps/web/src/components/PlanBadge.tsx`, `apps/web/src/components/LimitNote.tsx` and `apps/web/src/app/plans/page.tsx`
- Modify: `apps/web/src/lib/api.ts`. Add types `OwnerPlan` and `MyPlan`, mirroring Task 4's models with snake_case fields. Add `plan?: OwnerPlan | null` to `Diary` (168) and `Entry` (283), and `myPlan(): Promise<MyPlan>` beside the existing `me()` call.
- Modify: `apps/web/src/app/globals.css` (badge colour classes)
- Test: `apps/web/src/lib/__tests__/plan.test.ts`, `apps/web/src/components/__tests__/PlanBadge.test.tsx` and `apps/web/src/app/plans/__tests__/page.test.tsx`

**Interfaces:**
- Produces:
  ```ts
  // lib/plan.ts
  export type LimitColor = 'green' | 'yellow' | 'red'
  export function limitColor(used: number, limit: number): LimitColor
  export function formatResetDate(isoDate: string): string        // "2026-12-01" -> "Dec 1", read as a calendar date (UTC), never shifted by the browser's zone
  export function diaryLimitNote(planName: string, n: number): string  // "Free plan: 1 diary." / "Plus plan: 2 diaries."
  // components/PlanBadge.tsx
  export default function PlanBadge(props: {
    label: string; used: number; limit: number; resetsOn?: string | null;
    details?: { label: string; text: string }[];
  }): JSX.Element
  // components/LimitNote.tsx
  export default function LimitNote(props: { children: React.ReactNode; id?: string }): JSX.Element
  ```
  - **PlanBadge** renders `{label}: {left} of {limit} left`, plus ` · resets {formatResetDate(resetsOn)}` when `resetsOn` is set.
  - It carries the attribute `data-color={limitColor(used, limit)}`.
  - Next to it is a link `Upgrade now` to `/plans`.
  - Clicking the badge toggles a list of `details` (`aria-expanded`).
  - **LimitNote** is small muted text. Controls point at it with `aria-describedby={id}`.

- [ ] **Step 1: Write the failing tests:**
  - `limitColor(0,5)` is `'green'`, `(1,5)` and `(4,5)` are `'yellow'`, `(5,5)` and `(7,5)` are `'red'`, `(0,1)` is `'green'` and `(1,1)` is `'red'`.
  - `formatResetDate('2026-12-01') === 'Dec 1'`, with `TZ` set to `America/Los_Angeles` in the test.
  - `diaryLimitNote('Free',1) === 'Free plan: 1 diary.'` and `diaryLimitNote('Plus',2) === 'Plus plan: 2 diaries.'`.
  - PlanBadge `used=2 limit=5 resetsOn='2026-12-01'` shows `AI drafts: 3 of 5 left · resets Dec 1`, with `data-color` `yellow` and an `Upgrade now` link to `/plans`.
  - `used=7 limit=5` shows `0 of 5 left`.
  - Clicking the badge shows the details text.
  - The plans page shows the heading `Plans`, the three plan names, the four limit rows from Global Constraints, and `Paid plans are coming soon.`
- [ ] **Step 2: Run them, and check they fail.**
  - Run: `cd apps/web && npx jest src/lib/__tests__/plan.test.ts src/components/__tests__/PlanBadge.test.tsx src/app/plans`
  - Expected: FAIL, with modules not found.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run Step 2's command, plus `npx tsc --noEmit` and `npx eslint src`.**
  - Expected: PASS, and both are clean.
- [ ] **Step 5: Commit.** Use the message `feat(web): plan badge, limit note and plans page`.

### Task 7: Web pages show limits and gray out controls

**Files:**
- Modify:
  - `apps/web/src/app/diaries/page.tsx`: Diaries badge for Free, from `myPlan()`. At the limit, `New diary` (76) becomes a disabled button with the diary note.
  - `apps/web/src/app/diaries/[diaryId]/page.tsx`: AI-drafts badge from `diary.plan`, Free only.
    - When `diary.plan.read_only`, show the read-only banner and Upgrade now, and disable every write control in the action row (360-545): Scan now and its gear, Backfill, New entry, New entry from Google Calendar, Auto-Creation Rules and Settings.
    - Leave Delete diary and Deleted entries enabled.
  - `apps/web/src/app/entries/[entryId]/page.tsx`: AI-drafts badge from `entry.plan`, Free only.
    - When `plan.ai_drafts_left === 0`, the AI button (574-588) is disabled with the AI note.
    - When `entry.last_generation?.error === 'ai_draft_limit'` **and** `plan.ai_drafts_left === 0`, show the entry note above the body. After the reset, the note goes and the AI button is enabled again.
    - Badge `details` on both the diary and entry pages list every limit of the owner's plan: AI drafts (left and reset date) and photos per entry.
    - The regenerate error banner (413) never shows the raw code `ai_draft_limit`; it shows the AI note instead.
    - A 403 `tier_limit` from regenerate also shows the AI note.
    - When `plan.read_only`: disable Edit, Publish or Unpublish, and AI; leave Delete enabled; show the read-only banner.
  - `apps/web/src/components/AddPhotos.tsx`: new prop `room: number | null`, where `null` means unlimited.
    - With `room === 0`: the controls are disabled and the photo note is shown.
    - With `room === 1`: the file input has no `multiple`.
    - The entry page passes `room = plan.photos_per_entry == null ? null : Math.max(0, plan.photos_per_entry - entry.photos.length)`.
  - `apps/web/src/components/GooglePhotosPick.tsx`: a `disabled` prop, set when `room === 0`.
  - `apps/web/src/app/diaries/[diaryId]/restore/page.tsx:88-91`: for `source === 'diary_read_only'`, show the read-only banner text instead of the entry-count sentence.
- Test: add to `apps/web/src/app/entries/[entryId]/__tests__/page.test.tsx`, `apps/web/src/app/diaries/__tests__/` and `apps/web/src/components/__tests__/AddPhotos.test.tsx`.

**Interfaces:**
- Consumes: Task 6's `PlanBadge`, `LimitNote`, `formatResetDate`, `diaryLimitNote`, `OwnerPlan`, `MyPlan` and `api.myPlan`. On action responses the API's `plan` is `null`, so pages keep the last non-null `plan` they received.

- [ ] **Step 1: Write the failing tests:**
  - **Entry page:**
    - a Free plan with `ai_drafts_left: 0` gives a disabled AI button whose `aria-describedby` points at the text `No AI drafts left this month. Resets Dec 1.`;
    - `last_generation.error 'ai_draft_limit'` with `ai_drafts_left: 0` shows `Out of AI drafts until Dec 1. Write it yourself, or generate it then.`; with `ai_drafts_left: 5` that note is absent and the AI button is enabled;
    - a Plus plan shows no badge;
    - `photos_per_entry: 1` with one photo disables Add photos and shows `Free plan: 1 photo per entry.`;
    - `read_only: true` disables Edit and Publish and leaves Delete enabled.
  - **Diary list:** a Free plan with `diaries_used: 1` shows `Diaries: 0 of 1 left` in red and a disabled `New diary` with `Free plan: 1 diary.`
  - **Diary page:** `read_only` shows the banner text, Scan now is disabled, and Delete diary is enabled.
  - **AddPhotos:** with `room={1}` the input has no `multiple` attribute; with `room={0}` it is disabled.
- [ ] **Step 2: Run them, and check they fail.**
  - Run: `cd apps/web && npx jest src/app/entries src/app/diaries src/components/__tests__/AddPhotos.test.tsx`
  - Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `cd apps/web && npx jest && npx tsc --noEmit && npx eslint src`.**
  - Expected: all pass, and both are clean.
- [ ] **Step 5: Commit.** Use the message `feat(web): limits badge, notes and grayed-out controls on diary and entry pages`.

### Task 8: End-to-end checks and docs

**Files:**
- Create: `apps/web/e2e/tier-limits.spec.ts`
- Modify: any existing e2e spec that now crosses a Free limit. For example, `photo-upload.spec.ts` attaching several photos to one entry: set that user to `plus` with the spec's `sql()` helper, as `entry-location.spec.ts` does.
- Modify the docs, per the spec's "Docs to update with the build":
  - `COMPETITION.md`: the design-intent line; the photo limit is now an upgrade reason.
  - `design/02-data-model.md:30,74`, `design/03-api-surface.md:52,218`, `design/06-scan-worker.md:172`, `design/07-notifications.md:120`, `design/09-poc-scope.md:46` and `POC_PHASE2_TODO.md` (item 18 row, and the scaffold row);
  - `design/code-review-2026-11.md`: L-6, TR-12 and TR-17 done; TR-41 moved to billing.
- Test: `apps/api/tests/unit/test_manage_cli.py`. If a test for `manage.py` already exists, extend it.

- [ ] **Step 1: Write the e2e spec** `tier-limits.spec.ts`. Register a Free user with one diary (timezone `UTC`) and one manual entry.
  - **"AI limit shows red and grays out the AI button":**
    - `sql()` inserts 5 `ai_draft_spends` rows for the user, `month` = the current UTC `YYYY-MM`;
    - open the entry;
    - expect the text `AI drafts: 0 of 5 left`, the badge with `data-color="red"`, the AI button disabled, and the AI note;
    - click `Upgrade now`; the URL is `/plans`, and the page shows `Paid plans are coming soon.`
  - **"Second photo is blocked":** upload one photo through Add photos, then expect Add photos disabled and the text `Free plan: 1 photo per entry.`
  - **"Plus shows no badge":** `sql()` sets `subscription_tier='plus'`, reload, and the badge text is absent.
  - The test stack has no LLM key, so generations always fail and release. That's why usage is seeded with SQL.
- [ ] **Step 2: Write the CLI test.**
  - `users set-tier --tier plus` updates the row.
  - `--tier tier1` exits non-zero, because click rejects the choice.
  - Use click's `CliRunner` and the existing test DB fixtures. If `manage.py` can't be imported in tests, put a `Ruling:` in the ledger and check the choices list instead.
- [ ] **Step 3: Update the docs.**
  - Replace every `tier1` and `tier2` in `design/` and `docs/` with `plus` and `family`. Leave `docs/archive/` alone.
  - Check with `grep -rn "tier1\|tier2" design docs scripts apps --include=*.md --include=*.py --include=*.ts --include=*.tsx | grep -v archive`. Expected: only the 0017 migration and its test.
- [ ] **Step 4: Run `make test-all`.**
  - Expected: `All checks passed.`
  - Afterwards, run `make infra` to restart Andrew's dev containers.
- [ ] **Step 5: Commit.** Use the message `test(e2e): tier limits; docs: Free/Plus/Family`.
