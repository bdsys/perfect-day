# Diary sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A diary owner invites family by a copied link; they sign in and read the published entries; owners manage people within plan limits; an owner deleting their account can hand each shared diary to a member, photos included.

**Architecture:** Build on the existing `diary_permissions` and `invitations` tables and on `_get_diary_or_404`, which already resolves the caller's role for every diary-scoped endpoint. A new `services/sharing.py` holds the rules (tokens, limits, effective role). Invite, member and accept endpoints go in a new `routers/v1/sharing.py`. The hand-over and photo moves are retry-safe steps in a new `workers/handover.py`, called by `hard_delete_user` before anything is deleted. The web adds an invite page, a Sharing screen and role-aware diary pages.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Postgres 16, Garage (S3, `copy_object`), Next.js 16 / React, Jest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-20-diary-sharing-design.md` (approved by Andrew 2026-11-22). The core goal it is weighed against: `design/00-vision.md`.

## Global Constraints

- Branch `feat/diary-sharing` in a worktree (`.worktrees/diary-sharing`), never in the main checkout. Stage explicit paths only; never `git add -A`/`-a`; never `ruff format` the whole tree (format only the files you touched).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Plan limits, per diary, counting members plus pending unexpired invites: **Free 3, Plus 10, Family unlimited. Editors: Family only.** The API enforces them, not only the page.
- An invite link works **once, for 7 days**, only for an account whose email matches `invited_email` (case-insensitive). Only the SHA-256 hash of the link token is stored (`invitations.token`); the raw token appears in exactly two responses: create and new-link.
- Nothing about the diary is shown before sign-in, or to a signed-in account whose email doesn't match.
- Coordinates go to the owner only (unchanged). Viewers see published entries only, with no AI badge (unchanged API behaviour).
- On downgrade nobody loses access: over-limit people stay; new invites are refused; editors whose owner isn't on Family act as viewers (stored role kept).
- Deletion cascades (CLAUDE.md). The hand-over is the only exception, and only by the owner's explicit choice. Default is Delete.
- Hand-over and photo moves are **retry-safe steps, not one transaction**: re-check state, copy before updating rows, let the existing prefix scrub delete old copies last. Any step may fail, and the next hourly run finishes the job.
- Copy that the spec fixes, verbatim:
  - Signed out: "You've been invited to a diary on Perfect Day. Sign in or create an account to see it."
  - Signed in: "[owner] shared *[diary name]* with you", with the button **Open diary**.
  - Wrong email: "This invite is for [masked email]. Sign in with that address, or ask [owner] for a new link."
  - Hand-over banner: "[Owner] is deleting their account. *[Diary]* becomes yours on [date]." with **Decline**.
  - Counter: "Shared with 2 of 3 people".
- Run `make test-all` before the PR. Then run the dev-stack check: `make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`. Run it after test-all, never at the same time.

## Review Focus

1. **Email case and spacing.** An invite to `Mom@Example.com ` must be accepted by an account registered as `mom@example.com`. Tested in Task 4.
2. **The recipient isn't able to take the diary at the hard delete.** This covers a recipient whose own account is being deleted, who left, or who was removed. The diary must be deleted, never given to a dead or absent account. Tested in Task 7.
3. **A downgraded editor reaching draft content through a side door.** This covers the photo endpoint, which checks roles in its own SQL, and the entry endpoints. Such an editor must see only what a viewer sees. Tested in Task 2.
4. **A photo used in two places.** A photo of the departing owner attached both to the handed-over diary and to one of their own diaries (which is deleted) must survive, owned by the recipient. A Google Photos item the recipient already has must not make the move fail every hour. Tested in Task 7.
5. **An open redirect through `?next=`.** Values like `//evil.example`, `https://evil.example` and `/\evil.example` must fall back to `/diaries`. Tested in Task 8.

---

### Task 0: Branch and commit the approved design

**Files:**
- Modify: `docs/superpowers/specs/2026-11-20-diary-sharing-design.md:3`

- [ ] **Step 1:** Create the worktree: `git worktree add .worktrees/diary-sharing -b feat/diary-sharing main`.
  - Copy the four new files from the main checkout into the same paths in the worktree: `design/00-vision.md`, `docs/handoff/2026-11-20-diary-sharing.md`, the spec and this plan.
  - Apply the `CLAUDE.md` and `design/README.md` edits with `git diff -- CLAUDE.md design/README.md | git -C .worktrees/diary-sharing apply`.
  - Check every copy with `diff` (expected: no output) before removing anything from the main checkout. Then remove the four files there and run `git checkout -- CLAUDE.md design/README.md`.
  - `git -C /home/andrew/perfect-day status --short`. Expected: clean.
- [ ] **Step 2:** In the spec, change line 3 to `Status: **approved by Andrew 2026-11-22.**`
- [ ] **Step 3: Commit.** Stage the six paths explicitly. Message: `docs: core goal, diary sharing design and plan`.

### Task 1: Schema, plan limits and "owned since"

**Files:**
- Create: `apps/api/alembic/versions/0021_diary_sharing.py`
- Modify: `apps/api/app/models/__init__.py` (Diary), `apps/api/app/services/tier.py`, `apps/api/app/services/read_only.py:14-19`, `apps/api/app/services/plan_view.py`
- Test: `apps/api/tests/integration/test_migration_0021.py`, `apps/api/tests/unit/test_plans.py`, `apps/api/tests/integration/test_read_only_diaries.py`

**Interfaces:**
- Produces:
  - `Diary.owned_since: Mapped[datetime]` (NOT NULL, server default `now()`).
  - `Diary.handover_to_user_id: Mapped[uuid.UUID | None]` (FK `users.id` ON DELETE SET NULL).
  - `PlanLimits.people_per_diary: int | None` and `PlanLimits.editors: bool`.
  - `LimitSource` gains `"people"` and `"editor"`.
  - `MyPlanOut.people_per_diary: int | None` and `MyPlanOut.editors: bool`.

- [ ] **Step 1: Write the failing tests.**
  - `test_migration_0021.py::test_upgrade_backfills_owned_since_and_downgrade_drops`. Follow `test_migration_0019.py`'s scratch-database pattern. Upgrade to 0020, insert a user and a diary with `created_at = '2025-01-02T00:00:00Z'`, and insert one `invitations` row. Upgrade to 0021. Assert:
    - `owned_since == created_at`;
    - `handover_to_user_id` is null;
    - `ix_invitations_diary_id` exists;
    - `invitations` is empty (rows from before this release would hold a raw token).
    
    Then downgrade to 0020 and assert both columns are gone.
  - `test_plans.py::test_people_limits`: assert `limits_for("free").people_per_diary == 3`, `limits_for("plus").people_per_diary == 10`, `limits_for("family").people_per_diary is None`, `[limits_for(t).editors for t in ("free","plus","family")] == [False, False, True]`, `required_tier("people", 4) == "plus"`, `required_tier("people", 11) == "family"` and `required_tier("editor", 1) == "family"`.
  - `test_read_only_diaries.py::test_received_diary_goes_read_only_not_own`. A Free user owns diary A, created now. Diary B was created a year ago, then given to the user: set `owner_user_id` to the user and `owned_since = now() + 1s`. Assert `read_only_diary_ids == {B.id}`.
  - `test_plan_endpoints.py::test_my_plan_reports_people_limit`: `GET /v1/auth/me/plan` for a Free user includes `"people_per_diary": 3, "editors": false`.
- [ ] **Step 2: Run them.** `cd apps/api && .venv/bin/pytest tests/unit/test_plans.py tests/integration/test_migration_0021.py tests/integration/test_read_only_diaries.py tests/integration/test_plan_endpoints.py -q`. Expected: FAIL (missing revision, attributes and keys).
- [ ] **Step 3: Implement.**
  - Migration `0021` (revises `0020`):
    - add `owned_since` nullable, `UPDATE diaries SET owned_since = created_at`, then make it NOT NULL with `server_default=sa.func.now()`;
    - add `handover_to_user_id` with FK `fk_diaries_handover_to_user_id_users` ON DELETE SET NULL;
    - `DELETE FROM invitations`;
    - create index `ix_invitations_diary_id`.
    
    The docstring says `invitations.token` now holds the SHA-256 hex digest of the link token.
  - Model columns to match.
  - `PLAN_LIMITS`: `people_per_diary` 3 / 10 / None; `editors` False / False / True.
  - `_limit_for_source`: `"people"` returns `people_per_diary`; `"editor"` returns `None if limits.editors else 0`.
  - `_active_diary_ids` orders by `Diary.owned_since, Diary.created_at, Diary.id`. Update the module docstring to say "the diaries owned longest stay writable".
  - `MyPlanOut` gets the two new fields.
- [ ] **Step 4: Run the tests again.** Same command. Expected: PASS. Then `make test`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): sharing columns, people limits, read-only by owned-since`.

### Task 2: Sharing rules and the downgraded-editor role

**Files:**
- Create: `apps/api/app/services/sharing.py`
- Modify: `apps/api/app/routers/v1/diaries.py:108-131` (`_get_diary_or_404`), `apps/api/app/routers/v1/photos.py:140-176` (`_readable_photo`)
- Test: `apps/api/tests/unit/test_sharing_rules.py`, `apps/api/tests/integration/test_downgraded_editor.py`

**Interfaces:**
- Produces (in `app/services/sharing.py`):
  - `INVITE_TTL = timedelta(days=7)`
  - `new_invite_token() -> tuple[str, str]`: `(raw, sha256_hex)`. Use `secrets.token_urlsafe(32)` and `app.core.security.hash_token`.
  - `normalize_email(email: str) -> str`: strip, then lowercase.
  - `mask_email(email: str) -> str`: `"mom@example.com"` becomes `"m•••@example.com"`, and a one-letter local part `"a@x.io"` becomes `"a•••@x.io"`.
  - `effective_role(stored_role: str, owner_tier: str) -> Literal["editor", "viewer"]`
  - `person_name(user: User) -> str`: `display_name` if it isn't blank, else `email`.
  - `async def people_used(db, diary_id: uuid.UUID, *, exclude_invitation_id: uuid.UUID | None = None) -> int`: permission rows, plus invitations with `accepted_at IS NULL AND expires_at > now()`.
  - `async def ensure_room_for_person(db, diary: Diary, owner_tier: str, *, exclude_invitation_id=None) -> None`. It takes `pg_advisory_xact_lock(hashtext('share:' || :diary_id))` first. When `people_used >= people_per_diary`, it raises `tier_limit_error(source="people", limit=..., current=used, needed=used+1)`.
  - `ensure_editor_allowed(owner_tier: str) -> None`: raises `tier_limit_error(source="editor", limit=0, current=0, needed=1)` unless the tier allows editors.
- Produces (behaviour): `_get_diary_or_404` returns the **effective** role, so every existing `role == "viewer"` / `require_editor` check treats a downgraded editor as a viewer.

- [ ] **Step 1: Write the failing tests.**
  - Unit: `test_mask_email`, `test_effective_role` (`("editor","plus") -> "viewer"`, `("editor","family") -> "editor"`, `("viewer","family") -> "viewer"`), and `test_new_invite_token_hash_matches`.
  - Integration, `test_downgraded_editor.py`. Set up a Plus owner with an editor permission row, a draft entry, and a photo the owner uploaded and attached to it.
    - `test_downgraded_editor_cannot_see_drafts`: the editor's `GET /v1/diaries/{id}/entries` lists no drafts, and `GET /v1/entries/{draft}` returns 404.
    - `test_downgraded_editor_cannot_read_draft_photo`: `GET /v1/photos/{id}` returns 404.
    - `test_downgraded_editor_cannot_edit`: `PATCH /v1/entries/{published}` returns 403.
    - `test_family_editor_still_edits`: the owner is set to `family`, and the same PATCH returns 200.
- [ ] **Step 2: Run them.** `.venv/bin/pytest tests/unit/test_sharing_rules.py tests/integration/test_downgraded_editor.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - `sharing.py` as specified above.
  - `_get_diary_or_404`: on the permission path, load the owner's `subscription_tier` and return `effective_role(perm.role, tier)`.
  - `_readable_photo`: the role check becomes owner, OR (permission `editor` AND owner tier `family`), OR (any permission row AND entry published). Join `User` aliased on `Diary.owner_user_id`.
- [ ] **Step 4: Run the tests again.** Same command plus `tests/integration/test_shared_photos.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): sharing rules; editors act as viewers off Family`.

### Task 3: Owner endpoints: invites and members

**Files:**
- Create: `apps/api/app/routers/v1/sharing.py`, `apps/api/tests/integration/sharing_helpers.py`
- Modify: `apps/api/app/main.py` (include the router)
- Test: `apps/api/tests/integration/test_sharing_owner.py`

**Interfaces:**
- Consumes: everything in Task 2.
- Produces (endpoints; all owner-only via `_get_diary_or_404(require_owner=True)` except the member DELETE):
  - `POST /v1/diaries/{id}/invitations` with body `{email: EmailStr, role: "viewer"|"editor" = "viewer"}` → 201 `InvitationOut` with `token`.
  - `GET /v1/diaries/{id}/invitations` → `list[InvitationOut]` (no `token`). Unaccepted invites only, newest first. `status` is `"pending"` or `"expired"`.
  - `DELETE /v1/diaries/{id}/invitations/{invitation_id}` → 204.
  - `POST /v1/diaries/{id}/invitations/{invitation_id}/new-link` → 200 `InvitationOut` with a new `token` and `expires_at = now + 7 days`. For an expired invite it calls `ensure_room_for_person(..., exclude_invitation_id=it)` first.
  - `GET /v1/diaries/{id}/permissions` → `list[MemberOut]`.
  - `PATCH /v1/diaries/{id}/permissions/{user_id}` with body `{role}` → `MemberOut`. Changing to editor calls `ensure_editor_allowed`.
  - `DELETE /v1/diaries/{id}/permissions/{user_id}` → 204. Allowed for the owner, or for the member removing themself. It also clears `diaries.handover_to_user_id` when that pointed at the removed user.
  - `InvitationOut {id, invited_email, role, expires_at, status, token: str | None}`; `MemberOut {user_id, name, email, role, effective_role}`.
  - Error codes:
    - 422 `cannot_invite_self`;
    - 409 `already_member`;
    - 409 `already_invited` (a pending, unexpired invite for that email exists; the owner uses New link);
    - 403 `tier_limit` with source `people` or `editor`;
    - the existing `diary_read_only` 403 (`ensure_diary_writable`) on create and new-link;
    - 404 `not_found` for an unknown invitation or member.
- Produces (test helpers, `sharing_helpers.py`):
  - `register(client, email) -> dict` (headers);
  - `make_shared_diary(client, owner_email, tier="free") -> tuple[dict, str]` (owner headers, diary id); it sets the tier on the user row;
  - `invite(client, headers, diary_id, email, role="viewer") -> dict`;
  - `accept(client, headers, token) -> httpx.Response`.

- [ ] **Step 1: Write the failing tests.**
  - `test_invite_returns_link_token_once`: the create response has `token`, and the list response's items have `token is None`. The stored `invitations.token == hash_token(token)`, and no row holds the raw value.
  - `test_free_limit_counts_members_and_pending`: a Free owner creates 3 invites (201 each). The 4th returns 403 with `detail.code == "tier_limit"`, `details.source == "people"`, `limit == 3` and `required_tier == "plus"`.
  - `test_expired_invites_do_not_count`: set one invite's `expires_at` to the past; a 4th invite is then 201.
  - `test_new_link_on_expired_invite_checks_limit`: with 3 pending and 1 expired, New link on the expired one returns 403 `people`.
  - `test_new_link_replaces_token`: the old token's hash is no longer stored, and the new token's is.
  - `test_editor_invite_needs_family`: Plus owner, `role: editor` → 403 with `details.source == "editor"` and `required_tier == "family"`. A Family owner gets 201.
  - `test_cannot_invite_self_or_member_or_twice`: 422 `cannot_invite_self` (compared case-insensitively); 409 `already_member`; 409 `already_invited`.
  - `test_non_owner_gets_403_on_owner_endpoints`: a viewer calling GET invitations or permissions gets 403.
  - `test_member_can_leave_and_owner_can_remove`. The member's DELETE of their own row returns 204. Another member deleting someone else returns 403.
  - `test_leaving_clears_handover_choice`: with `handover_to_user_id` set to the member, after they leave it is null.
  - `test_downgrade_keeps_people_and_blocks_new_invites`. A Plus owner has 5 members and is set to Free. The list still has 5, and a new invite returns 403 `people`. `MemberOut.effective_role` for an editor of a Plus owner is `"viewer"`, while its `role` stays `"editor"`.
- [ ] **Step 2: Run them.** `.venv/bin/pytest tests/integration/test_sharing_owner.py -q`. Expected: FAIL (404s, since the routes don't exist yet).
- [ ] **Step 3: Implement** `routers/v1/sharing.py`. Keep invitation creation in one function, `create_invitation(db, diary, owner, email, role) -> tuple[Invitation, str]`, so project 2 can hook its email onto it (spec). Mount the router in `main.py` next to the diaries router.
- [ ] **Step 4: Run the tests again.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): invite, member and leave endpoints with plan limits`.

### Task 4: Invitee endpoints: look and accept

**Files:**
- Modify: `apps/api/app/routers/v1/sharing.py`
- Test: `apps/api/tests/integration/test_sharing_accept.py`

**Interfaces:**
- Produces:
  - `GET /v1/invitations/{token}` (auth required) → `InvitePreviewOut {diary_id, diary_name, owner_name, role, status: "pending"|"member"|"owner"}`.
  - `POST /v1/invitations/{token}/accept` (auth required) → `{diary_id}`.
  - Both check in this order:
    1. unknown hash → 404 `invite_not_found`;
    2. the caller owns the diary → `status: "owner"` (accept is a no-op);
    3. the caller is already a member → `"member"` (a no-op);
    4. already accepted → 404 `invite_not_found` (the link works once);
    5. email mismatch → 403 `{"code": "invite_wrong_email", "details": {"masked_email", "owner_name"}}`, never the diary name;
    6. expired → 410 `{"code": "invite_expired", "details": {"owner_name"}}`.
  - Accept locks the invitation row `FOR UPDATE`. It inserts `DiaryPermission(role=invitation.role)` and sets `accepted_at` and `accepted_by_user_id`.

- [ ] **Step 1: Write the failing tests.**
  - `test_accept_creates_permission_and_opens`: after accepting, `GET /v1/diaries` for the invitee lists the diary, and a second accept returns 200 (idempotent). The Free invitee can still create a diary of their own (`POST /v1/diaries` → 201): shared diaries don't count against their limit.
  - `test_accept_matches_email_case_insensitively`: an invite to `" Mom@Example.com "`; the account is registered as `mom@example.com`; accept returns 200.
  - `test_wrong_email_is_refused_without_diary_name`: 403. The body contains `m•••@example.com` and the owner's name, and the diary name doesn't appear anywhere in the response text.
  - `test_link_works_once`: A accepts, then leaves; A's second accept with the same link returns 404 `invite_not_found`.
  - `test_expired_link_410`.
  - `test_owner_opening_own_invite_is_harmless`: `status == "owner"`, and no permission row is created.
  - `test_revoked_invite_404`.
  - `test_signed_out_gets_401`: a request with no auth gets 401 with no invite details in the body.
- [ ] **Step 2: Run them.** `.venv/bin/pytest tests/integration/test_sharing_accept.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** both endpoints. Look tokens up by `hash_token(token)`.
- [ ] **Step 4: Run the tests again.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): look at and accept an invite`.

### Task 5: What the diary responses tell members, and Decline

**Files:**
- Modify: `apps/api/app/routers/v1/diaries.py` (`DiaryOut`, `_diary_out`, `list_diaries`, `get_diary`), `apps/api/app/routers/v1/sharing.py`
- Test: `apps/api/tests/integration/test_sharing_diary_out.py`

**Interfaces:**
- Produces, on every `DiaryOut`:
  - `role: Literal["owner","editor","viewer"]` (effective);
  - `owner_name: str`;
  - `people: PeopleOut | None`, owner only, where `PeopleOut {used: int, limit: int | None}`;
  - `deletion_notice: DeletionNoticeOut | None`, members only, set while the owner's `deletion_state` is `"pending"`. `DeletionNoticeOut {owner_name, deletes_on: datetime, becomes_yours: bool}`, where `becomes_yours` means the caller is the chosen recipient.
- `GET /v1/diaries` lists owned diaries first, then shared ones; each group is ordered by `name`.
- `POST /v1/diaries/{id}/handover/decline` → 204. It locks the diary row `FOR UPDATE`. It returns 404 unless the caller is `handover_to_user_id` and the owner's deletion is pending, and it sets `handover_to_user_id = NULL`.

- [ ] **Step 1: Write the failing tests.**
  - `test_shared_diary_says_who_shared_it`: the invitee's list item has `role == "viewer"`, `owner_name == "Andrew"` (the owner's display name) and `people is None`. The owner's item has `people == {"used": 1, "limit": 3}`.
  - `test_owner_name_falls_back_to_email`.
  - `test_members_see_deletion_notice`. The owner deletes their account with hand-over to member A. A's `GET /v1/diaries/{id}` has `deletion_notice.becomes_yours is True`; member B's has `False`; and `deletes_on` equals the owner's `hard_delete_after`.
  - `test_recipient_can_decline`: decline returns 204, `handover_to_user_id` becomes null, and A's notice now has `becomes_yours is False`.
  - `test_non_recipient_decline_404`.
- [ ] **Step 2: Run them.** `.venv/bin/pytest tests/integration/test_sharing_diary_out.py -q`. Expected: FAIL. The deletion-notice tests need Task 6's request body, so write them to set `handover_to_user_id`, `deleted_at` and `hard_delete_after` directly in the database.
- [ ] **Step 3: Implement.** In `list_diaries`, load owners and people counts in one query each, with no per-diary queries.
- [ ] **Step 4: Run the tests again.** Same command, plus `tests/integration/test_diaries.py tests/integration/test_diary_coordinates_privacy.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): diaries say who shared them, member counts, deletion notice, decline`.

### Task 6: Account deletion with hand-over choices

**Files:**
- Modify: `apps/api/app/routers/v1/auth.py:415-477`
- Test: `apps/api/tests/integration/test_account_handover_choice.py`

**Interfaces:**
- Produces:
  - `GET /v1/auth/account/handover-options` → `list[HandoverOptionOut {diary_id, diary_name, members: list[{user_id, name}]}]`. It covers owned live diaries with at least one member whose account is `active`.
  - `DELETE /v1/auth/account` takes an optional body `{handovers: dict[uuid.UUID, uuid.UUID]}` mapping diary id to member user id. With no body it behaves exactly as today. Each pair must be a live diary the caller owns and a current member with an active account; otherwise 422 `invalid_handover` and nothing changes. Valid pairs set `diaries.handover_to_user_id` in the same transaction as the deletion.
  - `POST /v1/auth/account/restore` also runs `UPDATE diaries SET handover_to_user_id = NULL WHERE owner_user_id = :me`, so "if the owner restores, nothing changes".

- [ ] **Step 1: Write the failing tests.**
  - `test_handover_options_list_shared_diaries_only`.
  - `test_delete_with_handover_records_choice`.
  - `test_delete_without_body_unchanged`. The existing `test_account_grace.py` must still pass.
  - `test_handover_to_non_member_422_and_account_not_deleted`: `deleted_at` stays null.
  - `test_restore_clears_handover_choice`.
- [ ] **Step 2: Run them.** `.venv/bin/pytest tests/integration/test_account_handover_choice.py tests/integration/test_account_grace.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Validate the pairs before setting `deleted_at`.
- [ ] **Step 4: Run the tests again.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): choose who gets each shared diary when deleting an account`.

### Task 7: The hand-over and photo moves at the hard delete

**Files:**
- Create: `apps/api/app/workers/handover.py`, `apps/api/app/services/diary_slugs.py`
- Modify: `apps/api/app/workers/hard_delete.py` (`hard_delete_user`, between the lock check and the "owned diaries" step), `apps/api/app/routers/v1/diaries.py` (use `unique_slug` in create and restore)
- Test: `apps/api/tests/integration/test_handover.py`

**Interfaces:**
- Produces:
  - `async def unique_slug(db, owner_user_id: uuid.UUID, base: str, *, exclude_diary_id: uuid.UUID | None = None) -> str`. It checks every row of that owner, since `uq_diaries_owner_slug` covers soft-deleted ones too, and appends `-1`, `-2` and so on.
  - `async def transfer_chosen_diaries(departing_user_id: uuid.UUID) -> list[uuid.UUID]`. It runs one `db_session()` per diary and locks the diary `FOR UPDATE`.
    - It skips a diary that is no longer owned by the departing user (already done) or has no choice.
    - If the recipient is no longer a member, or their `deletion_state` isn't `"active"`, it clears the choice and skips; the diary is then deleted as today.
    - Otherwise it sets `owner_user_id = recipient`, `owned_since = now()`, `handover_to_user_id = NULL`, `slug = unique_slug(...)` and `scan_enabled = False`.
    - It deletes the recipient's own permission row on the diary, the diary's `DiaryCalendarFilter` rows (they name the old owner's calendars) and its unattached `Event` rows (`entry_id IS NULL`: the old owner's calendar, not in any entry).
    - It resets `ScanJob`: `last_calendar_cursor = last_photos_cursor = next_scan_after = NULL` and `consecutive_failures = 0`.
    - It deletes the diary's unaccepted invitations, and adds `AuditLog(action="diary.handover", target_type="diary", target_id=...)`.
    - It returns the diary ids it moved.
  - `async def move_departing_photos(departing_user_id: uuid.UUID) -> int`. It covers every finalized, not-deleted photo of the user that is attached to an entry (soft-deleted entries included) in a diary they don't own. Each moves to the owner of the diary holding its earliest attachment, by `Entry.created_at, Entry.id`. It returns the count moved.
  - `async def _move_photo(photo_id: uuid.UUID, departing_user_id: uuid.UUID, new_owner_id: uuid.UUID) -> None`. Retry-safe steps, in one session:
    1. Lock the photo `FOR UPDATE`. If `photo.user_id != departing_user_id`, return.
    2. If `source == "google_photos"` and the new owner has a live photo with the same `external_id`: re-point each `entry_photos` link to it, deleting the link instead when that entry already has it. Then delete this photo row and return. Its objects stay under the old prefix for the scrub.
    3. Otherwise `copy_object` each of `s3_key`, `thumbnail_s3_key` and `display_s3_key` to the same key with the leading `{departing}/` replaced by `{new_owner}/`. A key without that prefix gets `{new_owner}/{basename}`.
    4. Set `dek_ciphertext = wrap_dek(unwrap_dek(old, departing), new_owner)`, the new keys and `user_id`, then commit.
    
    Any storage error is logged as `handover_photo_move_failed` and re-raised (the hourly retry, as in #91).
  - `hard_delete_user` order after the lock check: `transfer_chosen_diaries`, then `move_departing_photos`, then the existing owned-diary deletes, user data and prefix scrub.

- [ ] **Step 1: Write the failing tests.** They run against the real Garage container through the `client` fixture, as `test_hard_delete_photos.py` does.
  - `test_handover_gives_diary_and_photos_to_recipient`. The owner uploads a photo through the API and attaches it to a published entry. They delete their account with hand-over to member M, and are made due with `mark_due_for_hard_delete`; then `hard_delete_user` runs.
    - The diary's owner is now M, and M has no permission row on it.
    - M's `GET /v1/photos/{id}?kind=original` returns the uploaded bytes exactly.
    - Objects exist under `{M}/`, and nothing is left under `{owner}/`.
    - Another member's permission row remains.
  - `test_recipient_unable_means_diary_deleted`, parametrized over: recipient left; recipient removed; recipient's own account pending deletion; recipient declined. In each case the diary row is gone after the hard delete.
  - `test_received_diary_slug_clash`: M already owns a diary with slug `test-diary`, and the received one becomes `test-diary-1`.
  - `test_received_diary_over_limit_is_read_only`: M is on Free with one diary of their own; after the hand-over, `read_only_diary_ids(M)` is `{received}`.
  - `test_received_diary_scan_off_and_calendar_state_cleared`: `scan_enabled is False`, no `DiaryCalendarFilter` rows, no unattached events, and the attached events remain.
  - `test_editor_photos_stay_in_owners_diary`. An editor uploads a photo into the owner's entry; the editor's account is hard-deleted. The photo is now the owner's, and the owner reads the same bytes.
  - `test_photo_in_handed_and_deleted_diary_survives`: the owner's photo is attached in a handed-over diary and in one of the owner's own diaries (no members). After the hard delete the photo exists and belongs to M.
  - `test_google_photos_clash_resolves`. M already has `source="google_photos", external_id="g1"`, and the owner's photo has the same pair. After the hard delete: one photo row, owned by M; the entry links point at it; and the hard delete finished (the user row is gone).
  - `test_move_retry_after_storage_failure`. Make `copy_object` raise on the first call. `hard_delete_user` raises and the user row remains. Run it again unpatched: it completes, and the photo is owned by M.
- [ ] **Step 2: Run them.** `.venv/bin/pytest tests/integration/test_handover.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `handover.py`, `diary_slugs.py` and the call order. Replace the two inline slug loops in `diaries.py` with `unique_slug`.
- [ ] **Step 4: Run the tests again.** Same command, plus `tests/integration/test_hard_delete.py tests/integration/test_hard_delete_photos.py tests/integration/test_account_deletion_google.py tests/integration/test_diaries.py`. Expected: PASS. Then `make test`. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(api): hand a diary and its photos over at account deletion`.

### Task 8: Web: API client, safe return path, invite page

**Files:**
- Create: `apps/web/src/lib/nextPath.ts`, `apps/web/src/app/invite/[token]/page.tsx`
- Modify: `apps/web/src/lib/api.ts`, `apps/web/src/app/login/page.tsx`, `apps/web/src/app/register/page.tsx`
- Test: `apps/web/src/lib/__tests__/nextPath.test.ts`, `apps/web/src/app/invite/__tests__/page.test.tsx`, `apps/web/src/app/login/__tests__/page.test.tsx`

**Interfaces:**
- Produces:
  - `safeNext(raw: string | null): string`. It returns `raw` only when it starts with `/` and its second character is neither `/` nor `\`; otherwise `'/diaries'`.
  - New `api.ts` types mirroring Tasks 3–6: `Invitation`, `Member`, `InvitePreview`, `HandoverOption`, `PeopleCount`, `DeletionNotice`. `Diary` gains `role`, `owner_name`, `people` and `deletion_notice`; `MyPlan` gains `people_per_diary` and `editors`.
  - New `api.ts` methods:
    - `api.sharing.invite(diaryId, email, role)`, `.invitations(diaryId)`, `.revoke(diaryId, invId)`, `.newLink(diaryId, invId)`, `.members(diaryId)`, `.setRole(diaryId, userId, role)`, `.remove(diaryId, userId)`, `.preview(token)`, `.accept(token)`, `.decline(diaryId)`;
    - `api.auth.handoverOptions()`;
    - `api.auth.deleteAccount(handovers?: Record<string, string>)`.
  - `inviteUrl(token: string): string` is `${window.location.origin}/invite/${token}`.
- Login and register (including Google sign-in on both) go to `safeNext(searchParams.get('next'))` instead of `/diaries`. Each page's link to the other carries `next` along.

- [ ] **Step 1: Write the failing tests.**
  - `nextPath.test.ts`: `'/invite/abc'` is kept; `'//evil.example'`, `'https://evil.example'`, `'/\\evil.example'`, `null` and `''` all give `'/diaries'`.
  - Invite page:
    - signed out, it shows the exact signed-out sentence, links to `/login?next=%2Finvite%2Ftok` and `/register?next=%2Finvite%2Ftok`, and makes no API call;
    - signed in, it shows "Andrew shared" plus the diary name, and **Open diary** calls `accept` then `router.push('/diaries/<id>')`;
    - a `invite_wrong_email` error shows the exact wrong-email sentence with the masked email;
    - `invite_expired` shows "This invite has expired. Ask Andrew for a new link.";
    - `invite_not_found` shows "This invite link doesn't work any more. Ask the person who sent it for a new link.";
    - `status: 'owner'` or `'member'` goes straight to the diary.
  - Login: with `?next=/invite/tok`, a successful login pushes `/invite/tok`.
- [ ] **Step 2: Run them.** `cd apps/web && npx jest src/lib/__tests__/nextPath.test.ts src/app/invite src/app/login`. Expected: FAIL.
- [ ] **Step 3: Implement.** The invite page lives inside `Suspense` for `useSearchParams` where it is needed, as on the login page.
- [ ] **Step 4: Run the tests again.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(web): invite page and return-to-invite after sign-in`.

### Task 9: Web: Sharing screen and role-aware diary page

**Files:**
- Create: `apps/web/src/app/diaries/[diaryId]/sharing/page.tsx`, `apps/web/src/components/DeletionNotice.tsx`
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx`, `apps/web/src/lib/plan.ts`
- Test: `apps/web/src/app/diaries/[diaryId]/sharing/__tests__/page.test.tsx`, `apps/web/src/app/diaries/[diaryId]/__tests__/page.test.tsx`, `apps/web/src/components/__tests__/DeletionNotice.test.tsx`, `apps/web/src/lib/__tests__/plan.test.ts`

**Interfaces:**
- Consumes: Task 8's client.
- Produces:
  - `peopleCounter(used: number, limit: number | null): string` gives `"Shared with 2 of 3 people"`, `"Shared with 1 of 3 people"`, and, with no limit, `"Shared with 2 people"` or `"Shared with 1 person"`.
  - `peopleLimitNote(planName: string | null, n: number): string` gives `"Free plan: 3 people per diary."`.
  - `limitMessage` handles `people` (with `peopleLimitNote`) and `editor` (`"Editors are on the Family plan."`).
- Sharing screen (owner only; a non-owner is sent to the diary):
  - heading "Sharing" and the counter;
  - the invite form: Email, plus a Role select shown only when `plan.editors`;
  - the button **Make invite link**. After it: "Invite link for {email}. It works once, for 7 days.", then **Copy link** (`navigator.clipboard.writeText`), then **Share…** (only when `navigator.share` exists);
  - at the limit the form is replaced by `LimitNote` with `peopleLimitNote` and the Upgrade link;
  - pending invites: "{email} · Viewer · expires {date}" or "Expired", with **New link** and **Cancel invite**;
  - people: "{name} · Viewer", with **Remove** (confirm: "Remove {name}? They won't see {diary} any more."). On Family the role is a select; an editor whose owner isn't on Family shows "Editor — needs Family".
- Diary page:
  - owner: a **Share** link to `/diaries/{id}/sharing`;
  - any member: "Shared by {owner_name}" under the title, and **Leave diary** (confirm: "Leave {diary}? You'll need a new invite to see it again."), which goes to `/diaries` when done;
  - viewer: only the title line, `DeletionNotice`, and the published entries with "Load older entries". Settings, Connect, Scan, Backfill, New entry, Calendar pick, Rules, Photos, Deleted entries, Delete diary and the All/Draft/Published filter are all hidden;
  - editor: today's page minus Settings and Delete diary, which are owner-only in the API.
  - The empty state for a viewer is "No entries shared yet."
- `DeletionNotice`:
  - with `becomes_yours`, the exact hand-over sentence plus **Decline** (confirm: "Decline? {diary} will be deleted with {owner}'s account on {date}.");
  - otherwise "{owner} is deleting their account. {diary} will be deleted on {date}."

- [ ] **Step 1: Write the failing tests.** Cover each bullet above:
  - the counter strings;
  - the limit replacing the form;
  - the Role select hidden off Family;
  - the copy button writing `inviteUrl(token)`;
  - New link and Cancel calling the client;
  - a viewer's page with no `Scan now`, `Settings` or `Delete diary` and with the "Shared by" line;
  - a member's Leave calling `api.sharing.remove(diaryId, me.id)`;
  - the two `DeletionNotice` variants, and Decline calling `api.sharing.decline`.
- [ ] **Step 2: Run them.** `npx jest src/app/diaries src/components/__tests__/DeletionNotice.test.tsx src/lib/__tests__/plan.test.ts`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests again.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(web): Sharing screen, viewer's diary page, leave and decline`.

### Task 10: Web: diary list, plans page, account deletion choices

**Files:**
- Modify: `apps/web/src/app/diaries/page.tsx`, `apps/web/src/app/plans/page.tsx`, `apps/web/src/app/profile/page.tsx`
- Test: `apps/web/src/app/diaries/__tests__/page.test.tsx`, `apps/web/src/app/plans/__tests__/page.test.tsx`, `apps/web/src/app/profile/__tests__/page.test.tsx`

**Interfaces:**
- Diary list:
  - a shared card's second line is "Shared by {owner_name}";
  - an owned card with `people.used > 0` adds a line with `peopleCounter`;
  - `planDetails` adds `People per diary`, with `3`, `10` or `Unlimited`.
  - An owned card whose id is in `plan.read_only_diary_ids` adds the line "Read-only on the {plan_name} plan". This is how a person who received a diary over their limit is told (spec goal 7).
  - When the person owns no diary but has shared ones, they see only those. The plan badge, the limit note and the New diary / Photos / Deleted diaries buttons are hidden, and below the list sits "Want a diary of your own? Create one", where "Create one" links to `/diaries/new`.
  - With no diaries at all, today's empty state stays.
- Plans page `ROWS` gains `{ label: 'People who can read', values: ['3', '10 per diary', 'Unlimited'] }` and `{ label: 'Editors', values: ['—', '—', '✓'] }` after "Photos per entry".
- Profile, Delete account. When the confirm panel opens it loads `api.auth.handoverOptions()`. For each option it shows "{diary} is shared with {names}." with radios: **Delete it** (checked by default), then **Give it to {name}** for each member. **Delete my account** sends the chosen pairs. With no options, the screen looks exactly as today.

- [ ] **Step 1: Write the failing tests.**
  - the "Shared by" line, the counter line and the read-only line;
  - a person with only shared diaries sees no "New diary" button, no badge and no "Create your first one", and does see "Want a diary of your own?";
  - the plans page has the two rows with their values;
  - the profile shows the radios with Delete it checked; picking "Give it to Mom" and confirming calls `deleteAccount({ [diaryId]: momId })`; with no options it calls `deleteAccount(undefined)`.
- [ ] **Step 2: Run them.** `npx jest src/app/diaries/__tests__ src/app/plans src/app/profile`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests again.** Same command. Expected: PASS.
- [ ] **Step 5: Commit.** `feat(web): shared diaries in the list, plan rows, hand-over choice on delete`.

### Task 11: End-to-end test and docs

**Files:**
- Create: `apps/web/e2e/sharing.spec.ts`
- Modify:
  - `COMPETITION.md:91-115`: the tier matrix row "Family sharing" becomes "People who can read: 3 / 10 per diary / Unlimited"; add a row "Editors: — / — / ✓"; note the co-parent's calendar as planned for Family;
  - `design/02-data-model.md`: `owned_since`, `handover_to_user_id`, the invitation token hash, and who owns photos after a deletion;
  - `design/03-api-surface.md` § Diary sharing: the actual endpoints, codes and responses from Tasks 3–6;
  - `design/08-security-privacy.md`: the deletion flow with hand-over, the token hash, and the accepted unverified-email gap, quoting Andrew's "the risk seems low" (2026-11-20) and that project 2 closes it;
  - `design/09-poc-scope.md:59` (#19 done, with the PR);
  - `POC_PHASE2_TODO.md`: row 67 gets "must also re-apply hand-overs (2026-11-22)", plus a new row for the sharing PR;
  - `apps/api/app/routers/v1/auth.py` docstrings only if they mention what deletion removes.
- Test: the e2e spec.

- [ ] **Step 1: Write the e2e spec.** Use the API setup style of `tier-limits.spec.ts`.
  1. Register the owner with `display_name: 'Andrew'`.
  2. Create a diary with one published manual entry, titled "Park day", and one draft, titled "Secret draft".
  3. In the browser as the owner, open the diary, then **Share**. Enter the mom email and click **Make invite link**. Read the link from the page.
  4. In a new browser context, open the link signed out and see the signed-out sentence. Click **Create account**, register with the mom email, and land back on the invite. Click **Open diary**.
  5. See "Park day", not "Secret draft", and no "Scan now" button.
  6. Go to `/diaries` and see "Shared by Andrew".
  7. Back as the owner, the Sharing screen says "Shared with 1 of 3 people".
- [ ] **Step 2: Run it.** `make test-e2e` (it builds and starts the test stack). Expected: PASS. If it fails, the cause is a bug in an earlier task: debug it there, don't loosen the spec.
- [ ] **Step 3: Update the docs** listed above.
- [ ] **Step 4: Run the full suite.** `make test-all`. Expected: "All checks passed." Then the dev-stack check, in order and never at the same time as test-all: `make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`. Expected: smoke passes.
- [ ] **Step 5: Commit.** `test(e2e): invite, accept and read a shared diary; docs for sharing`.

## Verification after merge (Andrew's OK needed for each step that touches production)

1. Check the clock first: the backup runs at 03:00 Pacific and shares the deploy lock. Take a manual database dump (`umask 077`) to `/var/backups/perfect-day/` before deploying: this release migrates.
2. Deploy: `sudo ./scripts/nuc/40-update.sh`. Expected: migration 0021 applied, `/readyz` ok (postgres, redis, storage), smoke passes.
3. On the NUC, `sudo ./scripts/admin.sh users list` still works.
4. Andrew's phone check:
   - invite your own second address and accept it in a private window;
   - the shared diary shows only published entries;
   - "Shared with 1 of 3 people" shows;
   - remove the test person.
