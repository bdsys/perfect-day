# Testing

## Test pyramid

```
        [E2E — Playwright]         smoke only, PR-gated
       [Integration — pytest]      real services, every push
      [Unit — pytest]              pure functions, every push
```

### Unit tests (pytest)

Fast, no external dependencies. Target: most **coverage** here.

What to test at unit level:
- Prompt builder (assembles system + user message from events)
- Citation validator (`facts_used` and `title_facts_used` reference valid event indices)
- Photo grouping algorithm (deterministic ordering, edge cases)
- Tier enforcement checks (pure count-comparison logic)
- AES-256-GCM chunked encrypt/decrypt (no network, just `cryptography` library)
- JWT encoding/decoding (no DB)
- Date/timezone conversion utilities (per `design/time-and-tz.md`)
- `group_events_into_entries` logic

### Integration tests (pytest + testcontainers)

Real Postgres, Redis, and MinIO via testcontainers. No mocks for these services — mock/prod divergence has caused production incidents in analogous projects.

Target: most **behaviour** coverage here.

What to test at integration level:
- Full scan loop: `scan_diary` → `ingest_calendar_event` → `group_events_into_entries` (happy path + boundary conditions)
- `ensure_fresh_access_token` with the token-refresh advisory lock
- Photo upload + download round-trip (upload → finalize → decrypt-and-stream)
- AES-256-GCM round-trip with every chunk-size boundary (see below)
- Account-linking `link_required` flow (social login against existing email)
- Soft-delete → restore → hard-delete cascade (diary + entries + photos + MinIO objects)
- Refresh-token family revocation: normal rotation, grace-window retry, theft-signal revocation
- Auth middleware rejection of soft-deleted accounts
- Tier enforcement end-to-end (create diary, hit limit, get 403)
- `process_hard_deletes` Celery beat task
- Notification coalescing (two scan events → one notification)

### End-to-end tests (Playwright)

Smoke-level. Requires a running compose stack (local or CI service container).

Flows to cover:
1. Register → verify email → log in
2. Connect Google Calendar (OAuth flow via test credentials)
3. Trigger on-demand scan → draft entry appears
4. Edit draft title → publish entry
5. Delete diary (30-day grace path: mark deleted, restore, mark deleted again)

**External dependencies:** mock Google OAuth with a dedicated test Google Cloud project (real credentials, test data only). Anthropic is mocked via HTTP cassettes (no live API calls in CI).

### Mobile (Detox / Maestro) — Phase 2

Deferred. Stub the directory now: `apps/mobile/e2e/placeholder.test.ts`. Add one failing test named "TODO: Expo e2e setup" so the CI matrix can include the job and fail visibly, rather than silently skip it.

## Mocking policy

| Service | Integration tests | Unit tests |
|---|---|---|
| Postgres | Real (testcontainers) | Not used |
| Redis | Real (testcontainers) | Not used |
| MinIO | Real (testcontainers, `minio/minio` image) | Not used |
| Google Calendar / Photos API | `responses` library (HTTP mock) | `responses` or pure stubs |
| Anthropic API | `vcrpy` cassette or custom HTTP mock | Inline stub |
| SendGrid | `responses` library | Inline stub |
| Expo Push | `responses` library | Inline stub |
| AES-GCM | **Never mocked** | Real `cryptography` library |
| JWT signing | **Never mocked** | Real `python-jose` / `cryptography` |
| Password hashing | **Never mocked** | Real `argon2-cffi` |

**Google OAuth in Playwright E2E:** use real credentials in a dedicated test Google Cloud project. The test account has synthetic calendar data. Do not use production credentials in CI.

## Fixture strategy

**Factory functions** (not fixtures-as-files) with sensible defaults + override kwargs:

```python
def make_user(email="test@example.com", tier="free", **kwargs) -> User: ...
def make_diary(owner: User, timezone="UTC", **kwargs) -> Diary: ...
def make_entry(diary: Diary, status="draft", **kwargs) -> Entry: ...
def make_calendar_event(diary: Diary, title="Test event", **kwargs) -> Event: ...
```

**Database reset between tests:** `TRUNCATE users, diaries, ... CASCADE` (in reverse FK order) at the start of each test via a pytest autouse fixture. Faster than dropping and recreating the schema.

**MinIO isolation:** each test that touches MinIO uses a unique bucket prefix (`test-{uuid4()}`). Cleanup on teardown.

**Timezone fixture:** at least one `make_diary(timezone="America/Los_Angeles")` variant is used in all scan + date-assignment tests to catch the off-by-a-day regressions identified in `design/time-and-tz.md`.

## LLM / prompt tests

Two separate test paths:

1. **Snapshot tests (CI):** test the prompt-builder output as a string. No API call. Fail if the assembled prompt changes unexpectedly. Update the snapshot intentionally when prompts change.
2. **Live golden tests (manual):** `make test-live` sends a real prompt to Anthropic and validates the response structure. Never runs in CI. Re-run when prompts change significantly; commit updated cassettes.

Use `vcrpy` (or a thin custom cassette wrapper) to record real API responses once and replay them in integration tests. Cassettes are committed to the repo under `tests/cassettes/`.

## Photo encryption tests

Round-trip tests at every boundary:

| Case | Why |
|---|---|
| 0-byte photo | Edge case: empty input |
| Exactly 1 MiB | Single full chunk |
| 1 MiB + 1 byte | Boundary: 1 full chunk + 1-byte remainder |
| 100 × 1 MiB | Multi-chunk happy path |
| Tampered byte in chunk 1 | GCM tag must reject; no plaintext emitted |
| Tampered byte in chunk 50 | Partial-stream rejection; chunks 1–49 already streamed — test that the stream terminates cleanly |

The "tampered ciphertext must not emit plaintext" property is non-negotiable. Test that the streaming response ends with an error, not a truncated decrypted stream.

## Timezone tests

Per `design/time-and-tz.md`, minimum required timezone test cases:

| Case | Setup | Expected |
|---|---|---|
| Event at 11 PM UTC, diary in PT | Google event `start: 2024-10-03T23:00:00Z` | `entry_date = 2024-10-03` (PT date) |
| Event at 00:30 AM UTC, diary in PT | Google event `start: 2024-10-04T00:30:00Z` | `entry_date = 2024-10-03` (still Oct 3 in PT) |
| DST fall-back night event | Google event on Nov 3, 2024 in PT | Correct date, no duplication |
| Floating-time event | Google event with `date: 2024-10-03` (no time) | `entry_date = 2024-10-03` |
| Diary timezone change | Update `diary.timezone`, re-read entry | `entry_date` unchanged; user sees updated display |

## Coverage targets

| Scope | Target | How to treat |
|---|---|---|
| API package (all modules) | 80% line coverage | Smoke signal — not a hard gate |
| Auth middleware | 100% | Hard gate — must not merge if < 100% |
| Encryption (chunked AES-GCM) | 100% | Hard gate |
| Deletion cascades | 100% | Hard gate |
| Tier enforcement | 100% | Hard gate |

Run `pytest --cov=apps/api --cov-report=term-missing`. CI fails if hard-gate paths drop below 100%.

## Test data privacy

- Never use real user data in test fixtures.
- Photos in tests use a fixed set of public-domain images committed to `tests/fixtures/photos/` (e.g. Unsplash CC0 images, small resolution).
- Synthetic calendar events with innocuous titles like "Team lunch", "Soccer practice". Not personal.
- Test email addresses use `@example.com` domain (RFC 5737).

## Local development workflow

| Command | What it runs | When to use |
|---|---|---|
| `make test` | Unit + integration (testcontainers) | Before every commit |
| `make test-fast` | Unit only | Fast iteration during feature work |
| `make test-e2e` | Playwright (requires running compose stack) | Before opening a PR |
| `make test-smoke` | Curl walkthrough against a running stack | After deploy or stack changes |
| `make test-live` | Live LLM golden tests | After prompt changes |
| `make test-coverage` | Unit + integration with coverage report | Pre-PR |

Pre-commit hook runs `make test-fast` (unit only). Integration tests are fast enough (~5 min) to run on every push in CI but too slow for a pre-commit hook.

## CI integration

See `design/ci-cd.md` for the full GitHub Actions matrix. Summary:

| Job | Trigger | Time budget |
|---|---|---|
| Lint + unit tests | Every push | ~2 min |
| Integration tests | Every push | ~5 min |
| E2E tests (Playwright) | PR open + `run-e2e` label | ~15 min |
| Security scan (`pip-audit`, `pnpm audit`, Trivy) | Every push | ~3 min |
