# Manual Entry Creation Form Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the immediate-create "New Entry" button with a popover form letting the user set entry_date (required, defaults to today), entry_end_date (optional, for multi-day entries), and title (optional).

**Architecture:** Frontend-only — Next.js client component on the diary detail page. Reuses the existing `.popover` CSS class and Backfill popover pattern (`apps/web/src/app/diaries/[diaryId]/page.tsx:325-364`). No new modal component, no backend changes (backend already accepts all three fields).

**Tech Stack:** Next.js 15 (App Router), TypeScript, Playwright E2E.

---

## File Structure

| File | Action | What changes |
|---|---|---|
| `apps/web/src/lib/api.ts` | Modify line 354 | Add `entry_end_date?: string \| null` to `entries.create` data type |
| `apps/web/src/app/diaries/[diaryId]/page.tsx` | Modify | Add 5 state hooks; replace button with popover wrapper; rewrite `handleNewEntry` |
| `apps/web/e2e/manual-entry-form.spec.ts` | Create | 4 Playwright E2E cases for the popover flow |

---

## Task 1: Widen `entries.create` type to accept `entry_end_date`

**Files:**
- Modify: `apps/web/src/lib/api.ts:354-356`

The backend (`apps/api/app/routers/v1/entries.py:28`) already accepts `entry_end_date: date | None = None` in `EntryCreate`. The frontend client type just needs widening.

- [ ] **Step 1: Verify clean typecheck baseline**

```bash
cd apps/web
npx tsc --noEmit
```

Expected: no output (clean).

- [ ] **Step 2: Widen the type**

In `apps/web/src/lib/api.ts`, replace:

```ts
    async create(diaryId: string, data: { entry_date: string; title?: string | null; body_markdown?: string | null }): Promise<Entry> {
      return apiFetch(`/v1/diaries/${diaryId}/entries`, { method: 'POST', body: JSON.stringify(data) })
    },
```

With:

```ts
    async create(
      diaryId: string,
      data: {
        entry_date: string
        entry_end_date?: string | null
        title?: string | null
        body_markdown?: string | null
      },
    ): Promise<Entry> {
      return apiFetch(`/v1/diaries/${diaryId}/entries`, { method: 'POST', body: JSON.stringify(data) })
    },
```

- [ ] **Step 3: Verify typecheck still clean**

```bash
npx tsc --noEmit
```

Expected: clean — existing call site `{ entry_date: today }` still typechecks because `entry_end_date` is optional.

- [ ] **Step 4: Commit**

```bash
cd /path/to/repo
git add apps/web/src/lib/api.ts
git commit -m "$(cat <<'EOF'
Widen entries.create() type to accept entry_end_date

Backend already supports entry_end_date in EntryCreate. Surfacing
it in the typed client so the upcoming popover form can pass it.
EOF
)"
```

---

## Task 2: Write failing Playwright E2E test (TDD red)

**Files:**
- Create: `apps/web/e2e/manual-entry-form.spec.ts`

Follows the `multi-day-entries.spec.ts` / `backfill.spec.ts` setup pattern:
- `Date.now()` in email for uniqueness across runs
- Mock `/v1/auth/refresh` and `/v1/auth/me` to bypass rate limiter
- Navigate directly to `/diaries/${diaryId}` with mocked auth

**Important:** `getByRole('button', { name: 'New entry', exact: true })` — must use `exact: true` to avoid matching "New entry from Google Calendar".

- [ ] **Step 1: Create the spec file**

Create `apps/web/e2e/manual-entry-form.spec.ts` with this content:

```ts
import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const password = 'Password1!'

const today = () => new Date().toISOString().slice(0, 10)

function plusDays(iso: string, n: number): string {
  const d = new Date(iso + 'T00:00:00Z')
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

const sharedState = {
  email: '',
  diaryId: '',
  token: '',
}

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext()
  const email = `e2e-manual-entry-${Date.now()}@example.com`

  const regResp = await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })
  if (!regResp.ok()) throw new Error(`Register failed: ${regResp.status()} ${await regResp.text()}`)
  const { access_token: token } = await regResp.json() as { access_token: string }

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
  const diaryResp = await ctx.post(`${API}/v1/diaries`, {
    headers,
    data: { name: 'Manual Entry Form Diary', timezone: 'UTC' },
  })
  if (!diaryResp.ok()) throw new Error(`Create diary failed: ${diaryResp.status()} ${await diaryResp.text()}`)
  const { id: diaryId } = await diaryResp.json() as { id: string }

  sharedState.email = email
  sharedState.diaryId = diaryId
  sharedState.token = token
  await ctx.dispose()
})

async function goToDiary(page: import('@playwright/test').Page) {
  const { token, diaryId, email } = sharedState

  await page.route(`${API}/v1/auth/refresh`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: token }),
    }),
  )

  await page.route(`${API}/v1/auth/me`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'test-user', email, display_name: null }),
    }),
  )

  await page.goto(`/diaries/${diaryId}`)
  await page.waitForURL(`**/diaries/${diaryId}`, { timeout: 10_000 })
}

test.describe('Manual entry creation popover', () => {
  test('clicking "New entry" opens the popover with start date pre-filled to today', async ({ page }) => {
    await goToDiary(page)

    await page.getByRole('button', { name: 'New entry', exact: true }).click()

    const startInput = page.getByLabel('Start date')
    await expect(startInput).toBeVisible()
    await expect(startInput).toHaveValue(today())

    await expect(page.getByLabel('End date (optional)')).toBeVisible()
    await expect(page.getByLabel('Title (optional)')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Create entry' })).toBeVisible()
  })

  test('end date earlier than start date blocks submit and shows inline error', async ({ page }) => {
    await goToDiary(page)

    await page.getByRole('button', { name: 'New entry', exact: true }).click()

    const start = today()
    const earlier = plusDays(start, -2)
    await page.getByLabel('Start date').fill(start)
    await page.getByLabel('End date (optional)').fill(earlier)
    await page.getByRole('button', { name: 'Create entry' }).click()

    await expect(
      page.locator('.popover .error-message', {
        hasText: 'End date must be on or after start date',
      }),
    ).toBeVisible()

    await expect(page).toHaveURL(new RegExp(`/diaries/${sharedState.diaryId}$`))
  })

  test('submitting with date + title navigates to the new entry page', async ({ page }) => {
    await goToDiary(page)

    await page.getByRole('button', { name: 'New entry', exact: true }).click()

    const uniqueTitle = `Popover single-day ${Date.now()}`
    await page.getByLabel('Title (optional)').fill(uniqueTitle)
    await page.getByRole('button', { name: 'Create entry' }).click()

    await page.waitForURL(/\/entries\/[0-9a-f-]{36}$/, { timeout: 15_000 })
    await expect(page.getByText(uniqueTitle).first()).toBeVisible()
  })

  test('submitting with multi-day range + title navigates and entry shows date range', async ({ page }) => {
    await goToDiary(page)

    await page.getByRole('button', { name: 'New entry', exact: true }).click()

    const start = today()
    const end = plusDays(start, 2)
    const uniqueTitle = `Popover multi-day ${Date.now()}`

    await page.getByLabel('Start date').fill(start)
    await page.getByLabel('End date (optional)').fill(end)
    await page.getByLabel('Title (optional)').fill(uniqueTitle)
    await page.getByRole('button', { name: 'Create entry' }).click()

    await page.waitForURL(/\/entries\/[0-9a-f-]{36}$/, { timeout: 15_000 })
    // En-dash separator is rendered by formatDateRange when entry_end_date is set.
    await expect(page.locator('text=–').first()).toBeVisible()
    await expect(page.getByText(uniqueTitle).first()).toBeVisible()
  })
})
```

- [ ] **Step 2: Run the test to confirm red**

```bash
cd apps/web
npm run test:e2e -- manual-entry-form.spec.ts
```

Expected: 4 tests fail. Test 1 fails because clicking "New entry" creates an entry immediately (navigates away) rather than opening a popover — `getByLabel('Start date')` never finds an element.

---

## Task 3: Add state hooks + popover JSX to `page.tsx`

**Files:**
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx`

### Step 1: Add 5 new state hooks

After the `backfillError` state (line 60), before `cancelling`, insert:

```tsx
  const [showNewEntryOptions, setShowNewEntryOptions] = useState(false)
  const [newEntryDate, setNewEntryDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [newEntryEndDate, setNewEntryEndDate] = useState('')
  const [newEntryTitle, setNewEntryTitle] = useState('')
  const [newEntryError, setNewEntryError] = useState('')
```

### Step 2: Replace the single "New entry" button (lines 365-367)

Replace:

```tsx
            <button className="btn btn-primary" onClick={handleNewEntry} disabled={creating}>
              {creating ? 'Creating…' : 'New entry'}
            </button>
```

With:

```tsx
            <div style={{ position: 'relative', display: 'inline-block' }}>
              <button
                className="btn btn-primary"
                onClick={() => {
                  setNewEntryError('')
                  setShowNewEntryOptions((v) => !v)
                }}
                disabled={creating}
              >
                {creating ? 'Creating…' : 'New entry'}
              </button>
              {showNewEntryOptions && (
                <div className="popover" style={{ top: '100%', right: 0, marginTop: '0.4rem' }}>
                  <label>
                    Start date
                    <input
                      type="date"
                      value={newEntryDate}
                      onChange={(e) => setNewEntryDate(e.target.value)}
                    />
                  </label>
                  <label>
                    End date (optional)
                    <input
                      type="date"
                      value={newEntryEndDate}
                      onChange={(e) => setNewEntryEndDate(e.target.value)}
                    />
                  </label>
                  <label>
                    Title (optional)
                    <input
                      type="text"
                      value={newEntryTitle}
                      onChange={(e) => setNewEntryTitle(e.target.value)}
                    />
                  </label>
                  {newEntryError && (
                    <p className="error-message" style={{ marginBottom: '0.5rem' }}>{newEntryError}</p>
                  )}
                  <button
                    className="btn btn-primary"
                    style={{ width: '100%' }}
                    onClick={handleNewEntry}
                    disabled={creating || !newEntryDate}
                  >
                    {creating ? 'Creating…' : 'Create entry'}
                  </button>
                </div>
              )}
            </div>
```

- [ ] **Step 3: Verify typecheck clean**

```bash
cd apps/web && npx tsc --noEmit
```

Expected: clean (`handleNewEntry` still uses old logic at this step — that's fine).

---

## Task 4: Rewrite `handleNewEntry` for form state + validation

**Files:**
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx:235-245`

- [ ] **Step 1: Replace `handleNewEntry`**

Replace the current function body:

```tsx
  async function handleNewEntry() {
    setCreating(true)
    try {
      const today = new Date().toISOString().slice(0, 10)
      const entry = await api.entries.create(diaryId, { entry_date: today })
      router.push(`/entries/${entry.id}`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create entry')
      setCreating(false)
    }
  }
```

With:

```tsx
  async function handleNewEntry() {
    setNewEntryError('')

    if (!newEntryDate) {
      setNewEntryError('Start date is required')
      return
    }
    if (newEntryEndDate && newEntryEndDate < newEntryDate) {
      setNewEntryError('End date must be on or after start date')
      return
    }

    const trimmedTitle = newEntryTitle.trim()

    setCreating(true)
    try {
      const entry = await api.entries.create(diaryId, {
        entry_date: newEntryDate,
        entry_end_date: newEntryEndDate || null,
        title: trimmedTitle || null,
      })
      router.push(`/entries/${entry.id}`)
    } catch (e: unknown) {
      setNewEntryError(e instanceof Error ? e.message : 'Failed to create entry')
      setCreating(false)
    }
  }
```

**Why:** ISO `YYYY-MM-DD` strings sort lexicographically the same as chronologically, so `<` comparison is safe without `Date` parsing. Errors route to `newEntryError` (popover-local), matching the `backfillError` convention at lines 351-353.

- [ ] **Step 2: Verify typecheck clean**

```bash
cd apps/web && npx tsc --noEmit
```

Expected: clean.

---

## Task 5: Rebuild Docker web image and run E2E (TDD green)

**Important context:** The E2E tests target the Docker-served Next.js app at `localhost:3000`. Next.js hot-reload does NOT apply to the Docker container. You must rebuild the web image after code changes.

- [ ] **Step 1: Rebuild and restart web container**

```bash
cd /path/to/repo
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev up -d --build web
```

Wait ~10s for the container to start (Next.js production build inside Docker starts quickly once built).

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/
```

Expected: `200`

- [ ] **Step 2: Run the new E2E tests**

```bash
cd apps/web
npm run test:e2e -- manual-entry-form.spec.ts
```

Expected: all 4 pass.

If `getByLabel('Start date')` times out: the button click is navigating away (old code is still serving). Re-run the Docker build step.

- [ ] **Step 3: If failures — fix implementation, NOT tests**

---

## Task 6: Run full quality gates

- [ ] `npm run lint` — expect: `No ESLint warnings or errors`
- [ ] `npm run typecheck` — expect: no output (clean)
- [ ] `npm run test:e2e` — expect: all 28 tests pass (note: `golden-path.spec.ts` and `restore-tier-limit.spec.ts` are pre-existing flaky due to auth rate limiting — run suite a second time if they fail)
- [ ] From repo root: `make test-all` — expect: `All checks passed.`

---

## Task 7: Update Phase 2 tracker + write plan file

**Files:**
- Modify: `POC_PHASE2_TODO.md`

- [ ] Add to Wave B table:
  ```
  | 23 | Manual entry creation form (popover) | **done** |
  ```

- [ ] Add to per-item scaffold table (after item 19 row):
  ```
  | 23 | Manual entry form | `"New entry"` button + `handleNewEntry` (hardcodes today); `.popover` CSS class; backend `EntryCreate` accepts `entry_end_date` | **done** — Widen `entries.create` type; popover form with date/end_date/title inputs; client-side end>=start validation; new Playwright spec. Plan: `docs/superpowers/plans/2026-05-28-manual-entry-form.md` |
  ```

- [ ] Write this plan file to `docs/superpowers/plans/2026-05-28-manual-entry-form.md` (already done if you are reading this)

---

## Task 8: Final commit

- [ ] Stage all changed files:

```bash
git add apps/web/src/app/diaries/[diaryId]/page.tsx \
        apps/web/e2e/manual-entry-form.spec.ts \
        POC_PHASE2_TODO.md \
        docs/superpowers/plans/2026-05-28-manual-entry-form.md
```

- [ ] Verify staged files: `git status` — confirm only those 4 files

- [ ] Commit:

```bash
git commit -m "$(cat <<'EOF'
Replace New entry button with popover form (Phase 2 #23)

Adds a popover (matching the Backfill pattern) with three inputs:
start date (required, defaults to today), end date (optional, for
multi-day entries / trips), and title (optional). Validates that
end >= start client-side and surfaces API errors inline. Empty/
whitespace title is sent as null. Navigation behavior unchanged
(router.push to /entries/<id> on success).

Covered by new Playwright E2E in apps/web/e2e/manual-entry-form.spec.ts.
EOF
)"
```

---

## Risks / Watchpoints

- **UTC default date:** `new Date().toISOString().slice(0, 10)` is UTC-day, not local-day. Existing code at line 238 used the exact same idiom — preserved, not introduced.
- **`router.push` doesn't reset `creating`:** matches existing behavior; page unmounts on navigation. Errors reset it in the catch block.
- **Form state persistence across popover toggles:** intentionally not reset — reopening shows previous inputs. If product requests reset, add `setNewEntryDate(today())`, `setNewEntryEndDate('')`, etc., inside the toggle callback.
- **Docker dev workflow:** E2E tests target the Docker container. After any code change, rebuild with `docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev up -d --build web` before running tests.
- **`exact: true` on role selector:** required because the page has both `"New entry"` and `"New entry from Google Calendar"` buttons; without `exact: true`, Playwright's strict mode throws an error.
