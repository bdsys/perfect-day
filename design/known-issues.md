# Known Issues & Flagged Technical Debt

Issues identified during Phase 1 build that did not block PoC completion but need to be addressed in later phases. Date-stamped so old entries can be aged out as they're resolved.

---

## Open

### `require_reauth` async-loop bug — `app/core/auth.py:59`
**Flagged:** 2026-05-25. **Will break:** Phase 2 admin endpoints.

Calls `loop.run_until_complete()` inside a running async loop. Not currently triggered (no Phase 1 endpoint uses it), but any Phase 2 admin endpoint that requires re-auth will hit it.

**Fix:** Refactor to `await` the inner coroutine directly; remove the `run_until_complete` call. Add a regression test that exercises the path under an async test client.

---

### Web UI soft-delete restore flows
**Flagged:** 2026-05-25. **Resolved:** 2026-05-27.

Restore page (`/diaries/restore`) ships with the grace-period countdown UI. Closing this entry once verified end-to-end against the live stack.

---

## Deferred by design (not a bug — captured so it's findable)

### Google Photos integration
Calendar only for Phase 1. Photos integration is Phase 2 (`design/09-poc-scope.md` item 14).

### LLM draft generation requires `ANTHROPIC_API_KEY`
LLM does not run in test mode. Use `make test-live` to exercise it manually once `ANTHROPIC_API_KEY` is set in `apps/api/.env`. CI uses recorded cassettes (`tests/cassettes/llm_draft_simple.yaml`).
