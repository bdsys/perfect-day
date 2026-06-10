# Known Issues & Flagged Technical Debt

Issues identified during Phase 1 build that did not block PoC completion but need to be addressed in later phases. Date-stamped so old entries can be aged out as they're resolved.

---

## Open

### Flaky e2e test — `apps/web/e2e/photo-upload.spec.ts:116` "delete photo from library → thumbnail disappears"
**Flagged:** 2026-06-09. **Will break:** nothing yet — passes on Playwright's automatic retry, so `make test-e2e` / `make test-all` exit 0. But it has failed identically on two consecutive clean `make test-e2e` runs (first attempt fails, retry passes), so it's a reproducible bug, not random flakiness.

**Symptom:** After clicking "Delete photo", `ul.photo-grid > li` count goes `1` (countBefore) → briefly `2` → settles at `1` for the rest of the 10s timeout, never reaching the expected `0`.

**Hypothesis:** Test 1 ("upload → thumbnail appears → lightbox → escape", same describe block) asserts the thumbnail is visible based on an optimistic client-side preview and ends the test before the server-side `finalize` call is guaranteed to have completed. Test 2 then sees an empty grid (test 1's photo not yet persisted), uploads its *own* photo (`countBefore=1`), and while waiting for its delete to land, test 1's photo finishes finalizing and appears too (→ 2 momentarily), then settles at 1 (test 1's leftover photo) once test 2's own photo is deleted — landing on 1, not 0.

**Fix sketch:** Make test 1 confirm server-side persistence (e.g. `page.reload()` + re-assert thumbnail visible) before ending, and/or make test 2 fully self-contained by deleting all existing photos for the user via API at the start of the test (not just when the grid *appears* empty).

---

### `require_reauth` async-loop bug — `app/core/auth.py:59`
**Flagged:** 2026-05-25. **Will break:** Phase 2 admin endpoints.

Calls `loop.run_until_complete()` inside a running async loop. Not currently triggered (no Phase 1 endpoint uses it), but any Phase 2 admin endpoint that requires re-auth will hit it.

**Fix:** Refactor to `await` the inner coroutine directly; remove the `run_until_complete` call. Add a regression test that exercises the path under an async test client.

---

## Deferred by design (not a bug — captured so it's findable)

### Google Photos integration
Calendar only for Phase 1. Photos integration is Phase 2 (`design/09-poc-scope.md` item 14).

### LLM draft generation requires `ANTHROPIC_API_KEY`
LLM does not run in test mode. Use `make test-live` to exercise it manually once `ANTHROPIC_API_KEY` is set in `apps/api/.env`. CI uses recorded cassettes (`tests/cassettes/llm_draft_simple.yaml`).

---

## Resolved

### Web UI soft-delete restore flows
**Flagged:** 2026-05-25. **Resolved:** 2026-05-27.

Restore page (`/diaries/restore`) ships with the grace-period countdown UI. Verified end-to-end against the live stack.
