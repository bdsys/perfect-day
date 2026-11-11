# Known Issues & Flagged Technical Debt

Issues identified during Phase 1 build that did not block PoC completion but need to be addressed in later phases. Date-stamped so old entries can be aged out as they're resolved.

---

## Open

### Code review, November 2026
**Flagged:** 2026-11-11.

Open findings from the whole-codebase review are tracked in [`code-review-2026-11.md`](code-review-2026-11.md) (rows marked **Open**), together with six decisions that need an owner.

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

### `require_reauth` async-loop bug — `app/core/auth.py`
**Flagged:** 2026-05-25. **Resolved:** 2026-11-11.

Rewritten as an async dependency that awaits the Redis check. Covered by `tests/unit/test_require_reauth.py`.
