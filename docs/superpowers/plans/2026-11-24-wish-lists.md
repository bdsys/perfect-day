# Wish lists (piece 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A parent makes a wish list in a diary and publishes it. Family who can see the diary find it under "Coming up" and claim items. Anyone given its link can open it without signing in and claim an item with their name. The parent sees every claim and can turn the link off.

**Architecture:**
- **Shared foundation, reused by pieces 2–4:**
  - a `public_links` table: SHA-256 hash for lookup, plus the token encrypted so it can be copied again;
  - `services/public_links.py`, which creates, revokes and resolves links;
  - a `/v1/public/{token}` router with its own per-IP limiter and its own response headers;
  - tokens redacted from every log.
- **Wish lists:** three tables (`wish_lists`, `wish_list_items`, `wish_list_claims`). The rules (caps, claiming, what each role sees) live in `services/wish_lists.py`, used by both the signed-in router and the public router.
- **Web:** signed-in pages under `/diaries/[diaryId]/wish-lists`, a "Coming up" box on the diary page, and one public page `/p/[token]`. That page is rendered on the server so link previews work; its claims go from the browser.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Postgres 16, slowapi, Caddy 2 (`caddy:2-alpine`), Next.js 16.4 / React, Jest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-23-invitations-and-wish-lists-design.md` (approved by Andrew 2026-11-23), § Shared foundation and § Piece 1. The core goal it is weighed against: `design/00-vision.md` (point 3 and its exception).

## Global Constraints

**Working rules**
- Branch `feat/wish-lists` in the worktree `.worktrees/wish-lists`, never in the main checkout.
- Stage explicit paths only; never `git add -A` or `-a`. Never `ruff format` the whole tree; format only the files you touched.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**Tokens and links**
- Link tokens are `secrets.token_urlsafe(32)`. Store the SHA-256 hex (`hash_token`) for lookup, plus the token encrypted with the existing AES-GCM key (`oauth_token_secret`, the same machinery as Google tokens). A database dump on its own must give no working link.
- **Lifetime:** a link opens its page only while all of these hold:
  - it isn't revoked;
  - the wish list is published;
  - the diary isn't deleted;
  - the owner's `deletion_state` is `"active"`;
  - the diary's local date is ≤ `list_date + 7 days`.
- **One answer for every dead link:** unknown, revoked, expired, unpublished, deleted diary or blocked owner all give HTTP 404, detail `"link_inactive"`. The web shows "This link is no longer active".

**What the public page shows**
- It shows only: the list's title, its date, the pasted-list URL, `shared_by` (the owner's display name), and the items (name, store link, note, "as many as you like", taken or not, claim count).
- Never: the diary's name, `subject_name`, entries, photos or places, or any claimer's name. Never an email address as a name: no fallback to `person_name`.

**Headers and links**
- Every `/v1/public/` response and every `/p/` page sends `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`.
- `/p/` pages also carry `<meta name="robots" content="noindex, nofollow">` and `<meta name="referrer" content="no-referrer">`.
- Outbound links get `rel="noopener noreferrer"` and `target="_blank"`. Only `http` and `https` URLs are accepted, up to 2048 characters.
- **Link preview:** title and date only, no image. The title is `Wish list: {title} · {EEE d MMM}`, e.g. "Wish list: Emma's 6th birthday · Sat 14 Mar".

**Rate limits and caps**
- **Per-IP limits on `/v1/public/`:** page reads 60/minute, claim writes (claim and undo) 20/hour.
- **Caps:** 100 items per list; 50 claims per item; claimer name 1–60 characters after trimming.
  - My calls: title 1–120 characters, item name 1–200, note up to 500.
- No plan limit on wish lists. No new `LimitSource`.

**Roles**
- Owner and editors (`role` None or `"editor"` from `_get_diary_or_404`) create, edit, publish and delete lists, manage links, and see every claim with the claimer's name. They can release any claim.
- Viewers see published lists only and claim items. They see "taken" and counts, never who claimed.

**Read-only diaries** (`services/read_only.py`)
- Blocked: create, edit, publish, new link (`ensure_diary_writable`).
- Still allowed: viewing, claiming (signed in and public), undoing or releasing claims, unpublishing, turning a link off, deleting a list.
- Links already published keep working.

**"Coming up"**
- Shows published lists with today ≤ `list_date` ≤ today + 42 days, in the diary's timezone, soonest first.
- Everyone who can see the diary sees it.

**Logs**
- The token never appears in Caddy's access log, uvicorn's access log, structlog's bound `path`, or the path logged by `core/errors.py`.
- Diary-invite tokens (`/v1/invitations/{token}`) stay logged, as the spec rules.

**Copy fixed by the spec:**
- "I'm getting this"
- "You're getting this"
- "Taken"
- "{n} people are getting this" (n ≥ 2; "1 person is getting this" for 1)
- "This link is no longer active"
- "{title} list on {host}" for the pasted list (`host` without a leading `www.`)

**Before the PR**
- Run `make test-all`, then the dev-stack check: `make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`. Never run them at the same time.

**Before the deploy**
- This piece doesn't deploy until the edge review (Task 9) is done and Andrew has OK'd the deploy.
- It has migrations, so take a pre-deploy dump first.

## Review Focus

1. **Two people claim the last "Taken"-able item at the same moment.**
   - Exactly one wins; the other gets 409 `item_taken`.
   - The same race applies to the 50-claims cap.
   - Tested in Task 4 with two concurrent sessions.
2. **The timezone edge of "Coming up" and of expiry.**
   - A diary in `Pacific/Auckland` on the server's UTC evening: "today" must be the diary's date, not UTC's.
   - The link works on `list_date + 7` local and is dead on `+8`.
   - Tested in Tasks 2 and 4 with a frozen clock.
3. **A token in a log line through a side door:**
   - a 404 or 422 on a public path;
   - an unhandled exception inside a public handler;
   - a `?query` after the token.
   - Each must log the redacted path. Tested in Task 1.
4. **A forwarded or stale link after "make a new link".**
   - The old token must give `link_inactive` at once.
   - Republishing after unpublish must give a new token, never the old one revived.
   - Tested in Task 3.
5. **A date-only value shown a day early.**
   - `new Date("2026-03-14")` is UTC midnight, which is 13 Mar in California, so `formatShortDate` would show "Mar 13".
   - Every `list_date` on screen and in the preview title goes through `formatListDate`, which never shifts the day.
   - Tested in Task 6 with `TZ=America/Los_Angeles`.
   - (Non-http(s) pasted URLs are covered by Task 3's `test_bad_urls_refused`.)

---

### Task 0: Branch setup

- [ ] **Step 1:** Confirm the branch and the approved spec are present.

Run: `git -C .worktrees/wish-lists log --oneline -3 && ls .worktrees/wish-lists/docs/superpowers/specs/2026-11-23-invitations-and-wish-lists-design.md`
Expected: the branch shows the design commit; the file exists. (Once the design PR has merged, `git rebase origin/main` first.)

- [ ] **Step 2:** Symlink main's `apps/web/node_modules`, `apps/api/.venv` and `apps/api/.env` into the worktree. Remove them again before `git worktree remove`.

---

### Task 1: Tokens stay out of the logs

**Files:**
- Create: `apps/api/app/core/redact.py`
- Modify: `apps/api/app/core/logging.py`, `apps/api/app/middleware/request_id.py`, `apps/api/app/core/errors.py`
- Modify: `deploy/caddy/Caddyfile.tmpl` (the `log` block)
- Test: `apps/api/tests/unit/test_redact.py`, `scripts/nuc/tests/edge-client-ip.test.sh` (new checks)
- Docs: `design/08-security-privacy.md`. Add a "Tokens in logs" ruling, with both cases side by side:
  - diary invites: one use, 7 days, one email, so they stay logged;
  - link-only pages: months long, anyone can use them, so they're redacted.

**Interfaces:**
- Produces:
  - `redact_path(path: str) -> str`, which replaces the segment after `/v1/public/` with `[redacted]` and keeps the rest of the path, e.g. `/v1/public/abc/claims/x` → `/v1/public/[redacted]/claims/x`. Only path segments, so a `?query` after the token goes too.
  - `PublicTokenAccessFilter(logging.Filter)`.
  - Public web pages live at `/p/{token}`.

- [ ] **Step 1: Write the failing tests** in `tests/unit/test_redact.py`:
  - `test_redact_public_path`: `redact_path("/v1/public/AbC-_9/claims/1") == "/v1/public/[redacted]/claims/1"`.
  - `test_redact_bare_token`: `redact_path("/v1/public/AbC") == "/v1/public/[redacted]"`.
  - `test_redact_leaves_invites_and_others`: `/v1/invitations/tok` and `/v1/diaries/1` come back unchanged.
  - `test_access_filter_rewrites_uvicorn_args`: a `LogRecord` with args `("1.2.3.4:5", "GET", "/v1/public/SECRET?x=1", "1.1", 200)` comes back with `"SECRET"` in no arg.
  - `test_request_id_binds_redacted_path`: run `RequestIDMiddleware` around a stub app that reads `structlog.contextvars.get_contextvars()["path"]`; a request to `/v1/public/SECRET` binds `/v1/public/[redacted]`.
  - `test_unhandled_exception_logs_redacted_path`: call `unhandled_exception_handler` with a request for `/v1/public/SECRET`, capture with `structlog.testing.capture_logs()`, and assert `"SECRET"` appears in no field.

- [ ] **Step 2:** Run `cd apps/api && .venv/bin/pytest tests/unit/test_redact.py -v`. Expected: FAIL (`app.core.redact` not found).

- [ ] **Step 3: Implement `redact_path` and `PublicTokenAccessFilter` in `core/redact.py`.**
  - The filter rewrites `record.args[2]` when it's a str.
  - `configure_logging` adds it to `logging.getLogger("uvicorn.access")`. Uvicorn configures its loggers before importing the app, so adding a filter in `create_app` holds.
  - `RequestIDMiddleware` binds `path=redact_path(...)`.
  - `unhandled_exception_handler` logs `path=redact_path(request.url.path)`.

- [ ] **Step 4:** Run the same pytest command. Expected: PASS.

- [ ] **Step 5: Caddy.** Replace the `log { … format json }` block with:

```
log {
	output stdout
	format filter {
		request>uri regexp ^/(p|v1/public)/[^/?]+ /${1}/[redacted]
		request>headers>Referer delete
		wrap json
	}
}
```

Source: https://caddyserver.com/docs/caddyfile/directives/log ("filter", "regexp"; groups as `${1}`).
- Referer is deleted too, in case a browser ignores `no-referrer`.

Add these checks to `edge-client-ip.test.sh`, after the existing ones:
- `GET /p/SECRETTOKEN?x=1` on the web host, and `GET /v1/public/SECRETTOKEN/claims` on the API host;
- `check_absent "the access log never holds a public token" "SECRETTOKEN" "$(docker logs "${NET}-edge" 2>&1)"`;
- `check` that the log still holds `/p/[redacted]`.

- [ ] **Step 6:** Run `bash scripts/nuc/tests/edge-client-ip.test.sh`. Expected: every line `ok`, exit 0.

- [ ] **Step 7:** Commit `apps/api/app/core/redact.py apps/api/app/core/logging.py apps/api/app/middleware/request_id.py apps/api/app/core/errors.py apps/api/tests/unit/test_redact.py deploy/caddy/Caddyfile.tmpl scripts/nuc/tests/edge-client-ip.test.sh design/08-security-privacy.md` with the message `feat(logs): public link tokens never reach a log`.

---

### Task 2: Public links (the foundation)

**Files:**
- Modify: `apps/api/app/models/__init__.py` (`PublicLink`)
- Create: `apps/api/alembic/versions/0022_public_links.py` (revision `"0022"`, down `"0021"`)
- Modify: `apps/api/app/core/security.py` (`encrypt_secret`, `decrypt_secret`)
- Create: `apps/api/app/services/public_links.py`
- Modify: `apps/api/app/middleware/rate_limit.py` (`public_limiter`)
- Create: `apps/api/app/middleware/public_headers.py`
- Modify: `apps/api/app/main.py` (add the middleware)
- Modify: `apps/api/tests/integration/conftest.py` (patch `public_limiter._check_request_limit` like the other two)
- Test: `apps/api/tests/integration/test_public_links.py`, `apps/api/tests/integration/test_migration_0022.py`

**Interfaces:**
- **Model `PublicLink`** (`public_links`):
  - `id` UUID pk;
  - `diary_id` FK `diaries.id` `ondelete=CASCADE`, not null;
  - `kind` String(20), not null, check `kind IN ('wish_list')` (pieces 2–3 widen it);
  - `target_id` UUID not null, no FK, because the target is polymorphic and its own delete removes its links;
  - `token_hash` Text unique not null;
  - `token_encrypted` LargeBinary not null;
  - `revoked_at` timestamptz null;
  - timestamps;
  - an index on `(kind, target_id)`.
- **In `security.py`:** `encrypt_secret(plain: str) -> bytes` and `decrypt_secret(blob: bytes) -> str`, both delegating to the OAuth AES-GCM functions.
- **In `services/public_links.py`:**
  - `LinkKind = Literal["wish_list"]`
  - `async def create_link(db, *, diary_id: UUID, kind: LinkKind, target_id: UUID) -> tuple[PublicLink, str]`: revokes any active link for `(kind, target_id)` first; returns the row and the raw token.
  - `async def revoke_links(db, *, kind: LinkKind, target_id: UUID) -> None`
  - `async def active_link(db, *, kind: LinkKind, target_id: UUID) -> PublicLink | None`
  - `def link_token(link: PublicLink) -> str` (decrypts)
  - `async def resolve_link(db, token: str) -> tuple[PublicLink, Diary]`: raises `HTTPException(404, "link_inactive")` if the link is unknown or revoked, the diary is deleted, or `deletion_state(owner) != "active"`. Target checks (published, date) belong to the caller.
  - `def link_expired(list_date: date, diary_tz: str, now: datetime | None = None) -> bool`: True when the diary's local date is `> list_date + timedelta(days=7)`.
  - `LINK_INACTIVE = "link_inactive"`
- **In `rate_limit.py`:** `public_limiter = Limiter(key_func=get_remote_address)`, plus `PUBLIC_READ_LIMIT = "60/minute"` and `PUBLIC_WRITE_LIMIT = "20/hour"`.
- **`PublicHeadersMiddleware`** (pure ASGI, like `RequestIDMiddleware`): on paths starting `/v1/public/`, it adds `X-Robots-Tag: noindex, nofollow`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store` to every response, errors included.

- [ ] **Step 1: Write the failing tests** in `test_public_links.py`:
  - `test_create_link_stores_hash_and_ciphertext_only`: the raw token is in neither `token_hash` nor `token_encrypted`; `link_token(row) == raw`; `token_hash == hash_token(raw)`.
  - `test_create_link_revokes_previous`: a second `create_link` for the same target leaves the first `revoked_at` set; `resolve_link(old)` raises 404 `link_inactive`.
  - `test_resolve_unknown_revoked_deleted_diary_blocked_owner`: parametrized over the four states; each raises 404 with detail `"link_inactive"`.
  - `test_link_expired_uses_diary_timezone`:
    - for `list_date=2026-03-14` and tz `Pacific/Auckland`: False at `2026-03-21T10:00Z` (local 21 Mar 23:00) and True at `2026-03-21T11:30Z` (local 22 Mar);
    - for tz `America/Los_Angeles`: False at `2026-03-22T06:00Z` (local 21 Mar).
  - `test_public_headers_on_errors`: `GET /v1/public/nope` through the test client (no router yet, so 404) carries all three headers.
- And in `test_migration_0022.py`: upgrade then downgrade, following `test_migration_0021.py`.

- [ ] **Step 2:** Run `.venv/bin/pytest tests/integration/test_public_links.py tests/integration/test_migration_0022.py -v`. Expected: FAIL on import.

- [ ] **Step 3: Implement** the model, the migration, the security wrappers, the service, the limiter, the middleware and the conftest patch. Register `PublicHeadersMiddleware` after `RequestIDMiddleware` in `create_app`.

- [ ] **Step 4:** Run the same command. Expected: PASS.

- [ ] **Step 5:** Commit the files above with the message `feat(api): public links: hashed lookup, encrypted copy, one dead-link answer`.

---

### Task 3: Wish lists: schema and the owner's endpoints

**Files:**
- Modify: `apps/api/app/models/__init__.py` (`WishList`, `WishListItem`, `WishListClaim`; `Diary.wish_lists` relationship)
- Create: `apps/api/alembic/versions/0023_wish_lists.py`
- Create: `apps/api/app/services/wish_lists.py`
- Create: `apps/api/app/routers/v1/wish_lists.py`; register it in `main.py`
- Modify: `apps/api/app/workers/hard_delete.py` (explicit deletes before `delete(Diary)`: claims, items, lists, public links)
- Test: `apps/api/tests/integration/test_wish_lists.py`, `test_migration_0023.py`, a new case in `test_hard_delete.py`

**Interfaces:**
- **`WishList`** (`wish_lists`):
  - `id`; `diary_id` FK CASCADE;
  - `title` String(120);
  - `list_date` Date;
  - `pasted_list_url` Text null;
  - `status` String(10), check `IN ('draft','published')`, default `'draft'`;
  - `published_at` null;
  - `created_by_user_id` FK users SET NULL;
  - timestamps.
- **`WishListItem`** (`wish_list_items`):
  - `id`; `wish_list_id` FK CASCADE;
  - `position` Integer;
  - `name` String(200);
  - `url` Text null;
  - `note` String(500) null;
  - `many` Boolean default false;
  - timestamps.
- **`WishListClaim`** (`wish_list_claims`):
  - `id`; `item_id` FK CASCADE;
  - `user_id` FK users SET NULL, null;
  - `name` String(60) null;
  - `secret_hash` Text null;
  - `created_at`;
  - check `user_id IS NOT NULL OR name IS NOT NULL`;
  - partial unique index `(item_id, user_id) WHERE user_id IS NOT NULL`.
- **Caps in `services/wish_lists.py`:** `MAX_ITEMS = 100`, `MAX_CLAIMS_PER_ITEM = 50`.
- **URL rule:** `def clean_url(v: str | None) -> str | None` trims, accepts only a scheme of `http` or `https` (any case, stored lower-cased) with a host, and raises `ValueError` otherwise. The schemas use it as their validator.
- **Endpoints**, each using `_get_diary_or_404` for the role. "Manager" means the owner or an editor. Every write calls `ensure_diary_writable` except those listed as allowed on read-only.
  - `POST /v1/diaries/{diary_id}/wish-lists` (manager) takes `{title, list_date, pasted_list_url?}` and returns `201 WishListOut`.
  - `GET /v1/diaries/{diary_id}/wish-lists` (any member) returns `list[WishListSummaryOut]`. Viewers get published lists only. Ordered by `list_date`.
  - `GET /v1/wish-lists/{id}` returns `WishListOut`. A viewer asking for a draft gets 404.
  - `PATCH /v1/wish-lists/{id}` (manager) and `DELETE /v1/wish-lists/{id}` (manager; allowed on read-only; also `revoke_links` and deletes the link rows).
  - `POST /v1/wish-lists/{id}/items` (manager): 409 `wish_list_full` at 100. `PATCH` and `DELETE /v1/wish-list-items/{item_id}` (manager).
  - `POST /v1/wish-lists/{id}/publish` (manager): sets `published`, calls `create_link`, and returns `WishListOut` with `link`.
    - If the owner's `display_name` is blank: 409 detail `{"code": "display_name_required", "details": {"caller_is_owner": bool}}`.
  - `POST /v1/wish-lists/{id}/unpublish` (manager; allowed on read-only): back to `draft`, `revoke_links`.
  - `POST /v1/wish-lists/{id}/link/off` (manager; allowed on read-only): `revoke_links`; the list stays published.
  - `POST /v1/wish-lists/{id}/link/new` (manager): 409 `wish_list_not_published` on a draft; else `create_link`.
- **Schemas:**
  - `WishListOut`: `id, diary_id, title, list_date, pasted_list_url, status, items: list[ItemOut], link: LinkOut | None`. `link` goes to managers only and is `{token, path: "/p/{token}"}`, decrypted from `active_link`.
  - `ItemOut`: `id, name, url, note, many, taken: bool, claim_count: int, mine: bool, claims: list[ClaimOut] | None`. `claims` goes to managers only.
  - `ClaimOut`: `{id, name, signed_in: bool, created_at}`. `name` is `person_name(user)` for a signed-in claim, else the typed name.

- [ ] **Step 1: Write the failing tests** in `test_wish_lists.py`, using `sharing_helpers.make_shared_diary`, `invite` and `accept` for a viewer:
  - `test_owner_creates_lists_and_items_viewer_sees_published_only`
  - `test_viewer_cannot_create_edit_publish` (403 or 404 on each manager endpoint)
  - `test_item_cap_100` (the 101st gives 409 `wish_list_full`)
  - `test_bad_urls_refused` (parametrized: `javascript:alert(1)`, `data:text/html,x`, `//shop.example` give 422; `"  HTTPS://Shop.example/x "` is stored trimmed with the scheme lower-cased, as `https://Shop.example/x`)
  - `test_publish_needs_owner_display_name` (409 with `caller_is_owner` true for the owner and false for an editor on Family; succeeds once the name is set)
  - `test_publish_gives_link_new_link_kills_old` (`resolve_link(old)` gives 404 after `link/new`)
  - `test_unpublish_then_publish_gives_fresh_token` (the old token stays dead)
  - `test_read_only_diary_blocks_edits_allows_unpublish_and_link_off` (use `set_tier` to make the diary read-only)
  - `test_link_not_returned_to_viewers`
- In `test_hard_delete.py`: `test_hard_delete_removes_wish_lists_and_links` (no rows remain in any of the four tables for that diary).
- In `test_handover.py`: `test_handover_keeps_wish_lists` (after a hand-over the list is still in the same diary and its active link still resolves). `handover.py` should need no change, because the lists hang off `diary_id`.

- [ ] **Step 2:** Run `.venv/bin/pytest tests/integration/test_wish_lists.py tests/integration/test_migration_0023.py tests/integration/test_hard_delete.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement** the models, the migration, `services/wish_lists.py` (`clean_url`, the role-aware serializers, the caps), the router, and the hard-delete lines.

- [ ] **Step 4:** Run the same command. Expected: PASS.

- [ ] **Step 5:** Commit with the message `feat(api): wish lists: drafts, items, publish and links`.

---

### Task 4: Claims and "Coming up"

**Files:**
- Modify: `apps/api/app/services/wish_lists.py`, `apps/api/app/routers/v1/wish_lists.py`
- Test: `apps/api/tests/integration/test_wish_list_claims.py`

**Interfaces:**
- **`async def claim_item(db, item_id: UUID, *, user: User | None = None, name: str | None = None) -> tuple[WishListClaim, str | None]`**
  - Locks the item row `FOR UPDATE`.
  - 409 `item_taken` if the item isn't `many` and has a claim.
  - 409 `item_claims_full` at 50.
  - 409 `already_claimed` if `user` already has a claim on it.
  - For a public claim (`name` given) it makes a secret with `secrets.token_urlsafe(24)`, stores its hash, and returns the raw secret. For a signed-in claim the secret is `None`.
- **`async def undo_claim(db, claim_id: UUID, *, user: User | None = None, secret: str | None = None) -> None`:** 404 `claim_not_found` unless the user or the secret matches.
- **`async def coming_up(db, diary: Diary, now: datetime | None = None) -> list[WishList]`**
- **Endpoints:**
  - `POST /v1/wish-list-items/{item_id}/claim` (any member, published lists only) returns `ItemOut`.
  - `DELETE /v1/wish-list-items/{item_id}/claim` removes the caller's own claim.
  - `DELETE /v1/wish-list-claims/{claim_id}` (manager) releases any claim.
  - `GET /v1/diaries/{diary_id}/coming-up` returns `{"wish_lists": [WishListSummaryOut]}`, published only, for every member.
  - Claims and undo are allowed on read-only diaries.

- [ ] **Step 1: Write the failing tests:**
  - `test_viewer_claims_single_item_others_see_taken_not_who` (a second viewer sees `taken: true`, `claims: None`; the owner sees `claims[0].name` and `signed_in: true`)
  - `test_many_item_counts_never_taken` (two claims give `taken: false`, `claim_count: 2`)
  - `test_concurrent_claims_one_wins`: two `AsyncSession`s call `claim_item` on the same single item via `asyncio.gather`; exactly one result, and the other raises 409 `item_taken`.
  - `test_claims_cap_50`
  - `test_owner_releases_any_claim_viewer_cannot`
  - `test_claim_on_draft_404`
  - `test_coming_up_window_and_timezone`, with the clock frozen at `2026-03-01T20:00Z` on a `Pacific/Auckland` diary (local 2 Mar):
    - lists dated 1 Mar, 2 Mar, 13 Apr and 14 Apr give `[2 Mar, 13 Apr]` (13 Apr is 2 Mar + 42 days);
    - a draft dated 3 Mar isn't listed.

- [ ] **Step 2:** Run `.venv/bin/pytest tests/integration/test_wish_list_claims.py -v`. Expected: FAIL.

- [ ] **Step 3:** Implement. Freeze the clock the way the existing tests do: look for `now=` parameters in `services/` (as `coming_up` takes one) rather than adding a time library.

- [ ] **Step 4:** Run the same command. Expected: PASS.

- [ ] **Step 5:** Commit with the message `feat(api): claiming wish-list items, and Coming up`.

---

### Task 5: The public API

**Files:**
- Create: `apps/api/app/routers/v1/public.py` (prefix `/public`); register it in `main.py`
- Test: `apps/api/tests/integration/test_public_wish_list.py`, `apps/api/tests/integration/test_public_rate_limit.py`

**Interfaces:**
- **`GET /v1/public/{token}`** (`@public_limiter.limit(PUBLIC_READ_LIMIT)`) returns `PublicPageOut = {kind: "wish_list", wish_list: PublicWishListOut}`.
  - `PublicWishListOut = {title, list_date, pasted_list_url, shared_by: str | None, items: [PublicItemOut]}`.
  - `PublicItemOut = {id, name, url, note, many, taken, claim_count}`.
  - It calls `resolve_link`, then 404 `link_inactive` if the list isn't published or `link_expired(...)`.
- **`POST /v1/public/{token}/items/{item_id}/claims`** (`PUBLIC_WRITE_LIMIT`) takes `{name}` and returns `{claim_id, secret}` with 201. The item must belong to the link's list, otherwise 404 `link_inactive`.
- **`DELETE /v1/public/{token}/claims/{claim_id}`** (`PUBLIC_WRITE_LIMIT`), with header `X-Claim-Secret`: 204, or 404 `claim_not_found`.
- **Other rules:** `name` is trimmed, 1–60 characters, else 422. No `request.state.user` is read: these routes never authenticate, even with a bearer header.

- [ ] **Step 1: Write the failing tests** in `test_public_wish_list.py`:
  - `test_public_page_shows_only_the_list`: the JSON has none of the diary's name, `subject_name`, the owner's email, or any claimer name.
    - Seed a viewer claim whose account display name is "Grandma Jo", and a public claim named "Sam's mum".
    - Neither string appears anywhere in `resp.text`.
  - `test_public_claim_and_undo_with_secret` (a wrong secret gives 404; the right one gives 204, and the item is no longer taken)
  - `test_public_claim_item_from_another_list_404`
  - `test_public_link_dead_states`: parametrized over turned off, unpublished, `list_date` 8 days ago, diary soft-deleted, owner blocked. Each gives 404 `link_inactive` with the three headers.
  - `test_public_read_only_diary_still_works` (GET and claim succeed)
  - `test_public_page_shared_by_never_email`: owner display name set, then cleared after publishing; `shared_by` is `None`, never the email.
  - `test_public_claim_name_validation` (`""`, `"   "` and 61 characters give 422)
- In `test_public_rate_limit.py`, use a local client fixture that leaves `public_limiter` unpatched and calls `public_limiter.reset()` before and after. The limiter keeps its counts in memory, per process.
  - `test_public_reads_limited_per_ip`: 60 reads succeed and the 61st gives 429.
  - A second client from another address still succeeds. Build the transports as `httpx.ASGITransport(app=..., client=("198.51.100.1", 1234))` and `client=("198.51.100.2", 1234)`. The test app has no proxy middleware, so the transport's `client` is the address slowapi sees.

- [ ] **Step 2:** Run `.venv/bin/pytest tests/integration/test_public_wish_list.py tests/integration/test_public_rate_limit.py -v`. Expected: FAIL.

- [ ] **Step 3:** Implement the router.

- [ ] **Step 4:** Run the same command, then `.venv/bin/pytest -q` for the whole API suite. Expected: PASS, nothing else broken.

- [ ] **Step 5:** Commit with the message `feat(api): public wish-list page, claims and undo`.

---

### Task 6: Web: wish lists inside the diary

**Files:**
- Modify: `apps/web/src/lib/api.ts` (types `WishList`, `WishListSummary`, `WishListItem`, `WishListClaim`, `WishListLink`; namespace `api.wishLists` with `list, get, create, update, remove, addItem, updateItem, removeItem, publish, unpublish, linkOff, newLink, claim, unclaim, release, comingUp`)
- Create: `apps/web/src/app/diaries/[diaryId]/wish-lists/page.tsx` (the lists, plus "New wish list" for managers)
- Create: `apps/web/src/app/diaries/[diaryId]/wish-lists/[listId]/page.tsx` (see below)
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx`:
  - a **Wish lists** button beside Share and Settings;
  - a **Coming up** box above the entries, shown only when it isn't empty: each row reads "Wish list · {title} · {formatListDate}" and links to the list.
- Modify: `apps/web/src/lib/date.ts`: `formatListDate(isoDate: string): string` turns `"2026-03-14"` into `"Sat 14 Mar"`. It uses `timeZone: 'UTC'` with `weekday: 'short', day: 'numeric', month: 'short'`, assembled in that order, so the day never shifts.
- Test: `apps/web/src/app/diaries/[diaryId]/wish-lists/__tests__/*.test.tsx`, a case for Coming up in the diary page tests, and `src/lib/__tests__/date.test.ts`

**The list page** (`[listId]/page.tsx`):
- **For managers:**
  - edit the title, date and pasted list;
  - add, edit and delete items;
  - **Publish** / **Unpublish**;
  - a link box with **Copy link** (the full URL `${window.location.origin}/p/{token}`), **Turn link off** and **Make a new link**;
  - each item's claims, with names and a **Release** button.
- **For viewers:**
  - the items, with **I'm getting this**, **You're getting this** (plus Undo), **Taken**, or the counts copy;
  - the pasted-list line.

**Behaviour:**
- **Publish with no name:** a 409 `display_name_required` shows an inline field, "Your name, as people with the link will see it", when `caller_is_owner`. Saving calls `PATCH /v1/auth/me` with `{display_name}`, then publishes again.
- **An editor publishing for an owner with no name** sees "Ask {owner} to add their name in Profile first."
- **A read-only diary** (`read_only` on the diary): manager edit controls are disabled, with the existing read-only notice. Unpublish and Turn link off stay enabled.

- [ ] **Step 1: Write the failing Jest tests:**
  - `formatListDate keeps the day in Los Angeles`: with `process.env.TZ = 'America/Los_Angeles'` set before the import, `formatListDate("2026-03-14") === "Sat 14 Mar"`.
  - `manager sees claims with names; viewer sees Taken without names`
  - `publish without a name asks for it and retries`
  - `copy link copies the /p/ URL`
  - `viewer can claim and undo`
  - `read-only diary disables editing but not Turn link off`
  - `Coming up lists soonest first and is hidden when empty`

- [ ] **Step 2:** Run `cd apps/web && npx jest src/app/diaries src/lib/__tests__/date.test.ts`. Expected: FAIL.

- [ ] **Step 3:** Implement, reusing the button and notice classes the sharing screen uses.

- [ ] **Step 4:** Run the same command, then `npm run typecheck` and `npm run lint`. Expected: PASS, no type or lint errors.

- [ ] **Step 5:** Commit with the message `feat(web): wish lists in the diary, and Coming up`.

---

### Task 7: Web: the public page `/p/[token]`

**Files:**
- Create: `apps/web/src/app/p/[token]/page.tsx` (server component: `generateMetadata` and the first render)
- Create: `apps/web/src/app/p/[token]/PublicWishList.tsx` (`'use client'`: claims, undo, the remembered claims)
- Create: `apps/web/src/lib/publicApi.ts` (server-side fetch helper)
- Modify: `apps/web/next.config.js` (`async headers()` for `source: '/p/:path*'`, with the three headers from Global Constraints)
- Modify: `docker-compose.yml`, web `environment`: `API_INTERNAL_URL: http://api:8000`, with a one-line comment that the server side calls the API inside Docker
- Test: `apps/web/src/app/p/__tests__/*.test.tsx`, `apps/web/src/lib/__tests__/publicApi.test.ts`

**Interfaces:**
- **`fetchPublicPage(token: string, visitorIp: string | null): Promise<PublicPage | null>`** in `publicApi.ts`:
  - `GET ${process.env.API_INTERNAL_URL ?? NEXT_PUBLIC_API_URL}/v1/public/${encodeURIComponent(token)}`, with `cache: 'no-store'`.
  - Sends `X-Forwarded-For: visitorIp` when it isn't null. The API trusts it only from Docker's networks (row 71), so the read counts against the visitor, not the web container.
  - Returns `null` on 404. Wrap it in React `cache()` so `generateMetadata` and the page share one fetch.
- **`visitorIp`** is the first value of the incoming `x-forwarded-for` from `headers()`. Caddy sets it to exactly the visitor's address.
- **Metadata:**
  - `title` as in Global Constraints, with the date from `formatListDate`;
  - `robots: { index: false, follow: false }`;
  - `referrer: 'no-referrer'`;
  - `openGraph: { title }` with no images;
  - when the page is `null`, the title is just "Perfect Day".
- **Remembered claims:** `localStorage` key `pd-wish-claims`, holding `{ [itemId]: { claimId, secret } }`. Every read and write is wrapped in try/catch, and the page works without it.
- **The client's claim calls** go from the browser to `NEXT_PUBLIC_API_URL`, so Cloudflare sees the visitor.
- **The page shows:** the title, date, "Shared by {shared_by}" when present, the pasted-list line ("{title} list on {host}"), and the items, each with a link and note.
  - **Claiming:** **I'm getting this** opens a required "Your name" field (max 60).
  - **A dead link** shows "This link is no longer active" and nothing else.

- [ ] **Step 1: Write the failing tests:**
  - `metadata is title and date only, noindex, no image`
  - `dead link shows the inactive message`
  - `claim asks for a name, then shows You're getting this and Undo`
  - `remembered claim survives reload; broken localStorage still renders`
  - `store links open with noopener noreferrer`
  - `fetchPublicPage forwards the visitor address and returns null on 404`
  - `next.config headers cover /p/`: import the config and assert `headers()` returns the three headers for `/p/:path*`.

- [ ] **Step 2:** Run `npx jest src/app/p src/lib/__tests__/publicApi.test.ts`. Expected: FAIL.

- [ ] **Step 3:** Implement.

- [ ] **Step 4:** Run the same command, plus `npm run typecheck`, `npm run lint` and `npm run build`. Expected: PASS; the build lists `/p/[token]` as dynamic (ƒ).

- [ ] **Step 5:** Commit with the message `feat(web): the public wish-list page`.

---

### Task 8: End to end: the phone check, automated

**Files:**
- Create: `apps/web/e2e/wish-lists.spec.ts` (follow `sharing.spec.ts` for the owner and viewer accounts)

- [ ] **Step 1: Write the test**, the spec's phone check 1 as written:
  1. The owner makes a list dated 10 days ahead, with two items, and publishes it (setting a display name when asked).
  2. The viewer sees it under Coming up and claims item 1.
  3. A fresh browser context with no sign-in opens `/p/{token}`: item 1 shows **Taken**, and the visitor claims item 2 as "Sam's mum".
  4. The owner sees both claims, with the viewer's name and "Sam's mum".
  5. The owner turns the link off; the fresh context reloads and sees "This link is no longer active".
  6. Also assert the `/p/` response headers include `x-robots-tag: noindex, nofollow` and `referrer-policy: no-referrer`.

- [ ] **Step 2:** Run `make test-e2e` (or the single spec through the Makefile's Playwright target). Expected: the new spec passes, and so do the others.

- [ ] **Step 3:** Commit with the message `test(e2e): wish lists, signed in and by link`.

---

### Task 9: Docs and the edge review checklist

**Files:**
- Modify: `deploy/cloudflare.md`: a section "Public pages (`/p/`, `/v1/public/`)", a checklist for Andrew to work through in the dashboard before the deploy.
  - Check the zone's current WAF managed rules and rate-limiting rules against these paths, and record what the free plan offers on the day (I don't know its current rule count; read it in the dashboard).
  - Decide whether to add a rate-limit rule on `/v1/public/` ahead of the API's own limits.
  - Confirm nothing caches `/p/`.
  - Write down what was set.
- Modify: `deploy/nuc.md`: the same for the FortiGate WAF/IPS profile on policy 35 (whether public POSTs of up to about 1 KB pass), recorded the same way.
- Modify: `POC_PHASE2_TODO.md`: row 69 becomes "in progress (piece 1 built, branch `feat/wish-lists`)".
- Modify: `design/08-security-privacy.md`: add link-only pages to the data inventory (lists, claim names, the hashed and encrypted tokens, claim secrets hashed) and to the deletion cascade.

- [ ] **Step 1:** Write the docs.
- [ ] **Step 2:** Run `make test-all`. Expected: green. Then run the dev-stack check (`make up`, `make migrate`, `make down`, `make up`, `make test-smoke`, `make down`). Expected: smoke 17/0.
- [ ] **Step 3:** Commit with the message `docs: wish lists, edge review checklist`.

**After the merge, not in this branch:**
1. Andrew works through the Task 9 checklists.
2. Then, with his OK: a pre-deploy dump, `40-update.sh`, the smoke test, and a check that the edge log shows `/p/[redacted]` and no token.
3. Then the phone check (spec § How we'll know, 1).
4. Then row 69 piece 1 is marked done.
