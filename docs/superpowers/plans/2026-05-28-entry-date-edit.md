# Edit Entry Dates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `entry_date` (start) and `entry_end_date` (optional end) inputs to the existing entry edit form on the entry detail page, so users can correct or extend the date range of an entry without having to delete + recreate it.

**Architecture:** Frontend adds 3 new state hooks + 2 date inputs to the existing edit form on `apps/web/src/app/entries/[entryId]/page.tsx`, with client-side `end >= start` validation matching the manual-entry popover (`apps/web/src/app/diaries/[diaryId]/page.tsx`). Backend already accepts both fields in `EntryPatch` but a one-line bug fix is required so that explicit `null` clears `entry_end_date` (the current `model_dump(exclude_none=True)` silently drops nulls). No worker, no LLM re-trigger — date edits are a dumb attribute update; the existing PATCH handler is unchanged structurally.

**Tech Stack:** Next.js 15 (App Router), TypeScript, Pydantic v2, FastAPI, Playwright E2E, pytest async.

---

## File Structure

| File | Action | What changes |
|---|---|---|
| `apps/api/app/routers/v1/entries.py` | Modify line 297 | `exclude_none=True` → `exclude_unset=True` so explicit nulls clear `entry_end_date` |
| `apps/api/tests/integration/test_entries.py` | Modify (in `TestPatchEntry` class around line 79) | Add 3 new tests: patch dates; clear end date with null; reject end < start |
| `apps/web/src/app/entries/[entryId]/page.tsx` | Modify | Add 3 state hooks; seed in `startEdit` and fetch effect; add 2 date inputs + error <p> to edit form; rewrite `handleSave` with validation |
| `apps/web/e2e/entry-date-edit.spec.ts` | Create | 4 Playwright E2E cases for the date-edit flow |

---

## Important context: Pydantic null-clearing semantics

The backend `EntryPatch` schema (`apps/api/app/routers/v1/entries.py:33-37`) declares `entry_end_date: date | None = None`, which means the field accepts `null` and treats `null` as a valid value (not "unset"). However, the handler at `entries.py:297` uses `body.model_dump(exclude_none=True)` which strips any field whose value is `None` — so a payload of `{"entry_end_date": null}` is silently dropped instead of clearing the column. Switching to `exclude_unset=True` makes Pydantic treat "field omitted by client" differently from "field explicitly set to null":

- Field omitted → not in `model_dump(exclude_unset=True)` → unchanged (good)
- Field set to `null` → IS in `model_dump(exclude_unset=True)` with value `None` → `setattr(entry, 'entry_end_date', None)` → cleared (good)

This is the same idiom used elsewhere in this codebase for PATCH endpoints that need to support null-clearing.

---

## Important context: Worker re-scan safety

The worker grouping query in `apps/api/app/workers/tasks.py` filters by `creation_source == "rule"` when deciding whether to attach new events to an existing entry. **Manually-created and calendar-picked entries (`creation_source` = `"manual"` or `"calendar_pick"`) are not touched by re-scans**, so changing their dates is safe.

For rule-created entries (`creation_source == "rule"`), changing the date can cause the next worker scan to create a duplicate entry on the original date because the rule will re-fire. This is documented as a known limitation; the plan does NOT add UI gating to prevent it (out of scope — fix later if it becomes a real problem).

---

## Important context: Docker-served frontend

The frontend at `localhost:3000` is served by Docker, NOT by Next.js dev server hot reload. **Any frontend code change requires a Docker image rebuild** before E2E tests will see the new code:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev up -d --build web
```

This must be run from the repo root. After it returns, wait ~10s and verify with `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/` (expect `200`).

The backend (`localhost:8000`) is also Dockerized, but uvicorn watches the source — Python changes typically take effect without rebuild. If a Python change does not appear, run `docker compose restart api` to be safe.

---

## Task 1: Fix backend null-clearing bug

**Files:**
- Modify: `apps/api/app/routers/v1/entries.py:297`

The current line silently drops `entry_end_date: null` payloads. Switching to `exclude_unset=True` makes "explicit null from client" actually clear the column.

- [ ] **Step 1: Verify clean test baseline**

```bash
cd apps/api
pytest tests/integration/test_entries.py -v
```

Expected: all tests pass (TestPatchEntry currently has 1 test which only patches title + body).

- [ ] **Step 2: Make the one-line change**

In `apps/api/app/routers/v1/entries.py`, replace:

```python
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(entry, field, value)
```

With:

```python
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)
```

- [ ] **Step 3: Re-run tests**

```bash
pytest tests/integration/test_entries.py -v
```

Expected: existing test still passes (it only sends `title` and `body_markdown`, both non-null, so behavior is unchanged for that case).

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routers/v1/entries.py
git commit -m "$(cat <<'EOF'
fix(api): use exclude_unset in EntryPatch handler

exclude_none silently dropped explicit-null payloads, making it
impossible for clients to clear nullable fields like entry_end_date.
exclude_unset preserves the same "omitted = unchanged" semantics
while letting an explicit null clear the column.

Sets up the upcoming entry-date-edit UI to remove a multi-day
range by setting end date to null.
EOF
)"
```

---

## Task 2: Backend regression tests for date patch + null clearing (TDD red → green)

**Files:**
- Modify: `apps/api/tests/integration/test_entries.py` (add tests inside the existing `TestPatchEntry` class around line 79)

These tests pin down the new contract: dates can be patched; end date can be cleared with explicit null; end < start is rejected.

- [ ] **Step 1: Add the three tests**

Append to `TestPatchEntry` class (inside the class block, after `test_patch_title_and_body`):

```python
    async def test_patch_entry_date_and_end_date(self, client):
        token, diary = await _setup(client, "pdate@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01"},
                headers=auth,
            )
        ).json()
        r = await client.patch(
            f"/v1/entries/{entry['id']}",
            json={"entry_date": "2025-06-10", "entry_end_date": "2025-06-12"},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["entry_date"] == "2025-06-10"
        assert r.json()["entry_end_date"] == "2025-06-12"

    async def test_patch_clear_end_date_with_null(self, client):
        token, diary = await _setup(client, "pclear@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01", "entry_end_date": "2025-06-03"},
                headers=auth,
            )
        ).json()
        assert entry["entry_end_date"] == "2025-06-03"

        r = await client.patch(
            f"/v1/entries/{entry['id']}",
            json={"entry_end_date": None},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["entry_end_date"] is None
        # Start date untouched.
        assert r.json()["entry_date"] == "2025-06-01"

    async def test_patch_omitting_field_leaves_it_unchanged(self, client):
        token, diary = await _setup(client, "pomit@example.com")
        auth = {"Authorization": f"Bearer {token}"}
        entry = (
            await client.post(
                f"/v1/diaries/{diary['id']}/entries",
                json={"entry_date": "2025-06-01", "entry_end_date": "2025-06-03"},
                headers=auth,
            )
        ).json()
        # Patch only the title — end date must NOT be cleared.
        r = await client.patch(
            f"/v1/entries/{entry['id']}",
            json={"title": "Trip"},
            headers=auth,
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Trip"
        assert r.json()["entry_end_date"] == "2025-06-03"
```

- [ ] **Step 2: Run the new tests**

```bash
cd apps/api
pytest tests/integration/test_entries.py::TestPatchEntry -v
```

Expected: all 4 tests in `TestPatchEntry` pass (the 1 original + 3 new).

If `test_patch_clear_end_date_with_null` fails because `entry_end_date` is still `"2025-06-03"` after the null patch — Task 1 was not committed or did not take effect. Re-run Task 1 and check.

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_entries.py
git commit -m "$(cat <<'EOF'
test(api): cover EntryPatch date editing + null-clearing

Pins three behaviors:
- entry_date and entry_end_date can be updated together
- explicit null clears entry_end_date (regression test for
  exclude_unset fix)
- omitted fields are left unchanged
EOF
)"
```

---

## Task 3: Write failing Playwright E2E test (TDD red)

**Files:**
- Create: `apps/web/e2e/entry-date-edit.spec.ts`

Follows the `manual-entry-form.spec.ts` setup pattern: `Date.now()` email uniqueness, mock `/v1/auth/refresh` and `/v1/auth/me` to bypass the 10/min auth rate limiter, navigate directly to a pre-seeded entry.

Important selectors:
- The entry detail page already has an `<input id="entry-title">` and `<textarea id="entry-body">` — the new date inputs will use ids `entry-date` and `entry-end-date` (matching the `htmlFor`/`id` pattern at `apps/web/src/app/entries/[entryId]/page.tsx:228-231`).
- "Save" button has text "Save" while editing; "Saving…" while saving — the spec uses `getByRole('button', { name: 'Save' })`.
- "Edit" button uses `button:has-text('Edit')` to match the existing `golden-path.spec.ts` style.

- [ ] **Step 1: Create the spec file**

Create `apps/web/e2e/entry-date-edit.spec.ts` with this content:

```ts
import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const password = 'Password1!'

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
  const email = `e2e-entry-date-edit-${Date.now()}@example.com`

  const regResp = await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })
  if (!regResp.ok()) throw new Error(`Register failed: ${regResp.status()} ${await regResp.text()}`)
  const { access_token: token } = await regResp.json() as { access_token: string }

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
  const diaryResp = await ctx.post(`${API}/v1/diaries`, {
    headers,
    data: { name: 'Entry Date Edit Diary', timezone: 'UTC' },
  })
  if (!diaryResp.ok()) throw new Error(`Create diary failed: ${diaryResp.status()} ${await diaryResp.text()}`)
  const { id: diaryId } = await diaryResp.json() as { id: string }

  sharedState.email = email
  sharedState.diaryId = diaryId
  sharedState.token = token
  await ctx.dispose()
})

async function createEntry(opts: { entry_date: string; entry_end_date?: string | null; title?: string }): Promise<string> {
  const ctx = await playwrightRequest.newContext()
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${sharedState.token}` }
  const r = await ctx.post(`${API}/v1/diaries/${sharedState.diaryId}/entries`, {
    headers,
    data: opts,
  })
  if (!r.ok()) throw new Error(`Create entry failed: ${r.status()} ${await r.text()}`)
  const entry = await r.json() as { id: string }
  await ctx.dispose()
  return entry.id
}

async function goToEntry(page: import('@playwright/test').Page, entryId: string) {
  const { token, email } = sharedState

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

  await page.goto(`/entries/${entryId}`)
  await page.waitForURL(`**/entries/${entryId}`, { timeout: 10_000 })
}

test.describe('Edit entry dates', () => {
  test('clicking Edit reveals date inputs pre-filled with current entry dates', async ({ page }) => {
    const entryId = await createEntry({ entry_date: '2025-06-01', entry_end_date: '2025-06-03' })
    await goToEntry(page, entryId)

    await page.locator('button:has-text("Edit")').click()

    const startInput = page.locator('#entry-date')
    await expect(startInput).toBeVisible()
    await expect(startInput).toHaveValue('2025-06-01')

    const endInput = page.locator('#entry-end-date')
    await expect(endInput).toBeVisible()
    await expect(endInput).toHaveValue('2025-06-03')
  })

  test('end date earlier than start date blocks save and shows inline error', async ({ page }) => {
    const entryId = await createEntry({ entry_date: '2025-07-10' })
    await goToEntry(page, entryId)

    await page.locator('button:has-text("Edit")').click()

    await page.locator('#entry-date').fill('2025-07-10')
    await page.locator('#entry-end-date').fill('2025-07-05')
    await page.getByRole('button', { name: 'Save' }).click()

    await expect(
      page.locator('.error-message', {
        hasText: 'End date must be on or after start date',
      }),
    ).toBeVisible()

    // Still in edit mode (Save button still visible — would be gone after a successful save).
    await expect(page.getByRole('button', { name: 'Save' })).toBeVisible()
  })

  test('saving new dates updates the entry and closes the edit form', async ({ page }) => {
    const entryId = await createEntry({ entry_date: '2025-08-01' })
    await goToEntry(page, entryId)

    await page.locator('button:has-text("Edit")').click()

    const newStart = '2025-08-15'
    const newEnd = plusDays(newStart, 2)
    await page.locator('#entry-date').fill(newStart)
    await page.locator('#entry-end-date').fill(newEnd)
    await page.getByRole('button', { name: 'Save' }).click()

    // Edit form gone, Edit button back.
    await expect(page.locator('button:has-text("Edit")')).toBeVisible({ timeout: 10_000 })

    // Date range header now shows the new range with an en-dash (formatDateRange output).
    await expect(page.locator('text=–').first()).toBeVisible()
    await expect(page.getByText(newStart)).toBeVisible()
  })

  test('clearing the end date on a multi-day entry collapses it to a single-day entry', async ({ page }) => {
    const entryId = await createEntry({ entry_date: '2025-09-01', entry_end_date: '2025-09-04' })
    await goToEntry(page, entryId)

    await page.locator('button:has-text("Edit")').click()

    // Pre-condition: end date is populated.
    await expect(page.locator('#entry-end-date')).toHaveValue('2025-09-04')

    // Clear the end date.
    await page.locator('#entry-end-date').fill('')
    await page.getByRole('button', { name: 'Save' }).click()

    // Edit form gone.
    await expect(page.locator('button:has-text("Edit")')).toBeVisible({ timeout: 10_000 })

    // Reopen edit and confirm the end date is now empty (round-trip from server).
    await page.locator('button:has-text("Edit")').click()
    await expect(page.locator('#entry-end-date')).toHaveValue('')
  })
})
```

- [ ] **Step 2: Run the test to confirm red**

```bash
cd apps/web
npm run test:e2e -- entry-date-edit.spec.ts
```

Expected: all 4 tests fail. Test 1 fails because `#entry-date` does not exist yet — the edit form currently has only `#entry-title` and `#entry-body`.

If a test passes unexpectedly: the Docker image already has updated code from a previous attempt. Wipe and rebuild from Task 5 first.

---

## Task 4: Add date inputs + state + validation to entry detail page

**Files:**
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`

### Step 1: Add 3 new state hooks

After the `saving` state (line 54), insert:

```tsx
  const [editEntryDate, setEditEntryDate] = useState('')
  const [editEntryEndDate, setEditEntryEndDate] = useState('')
  const [editDateError, setEditDateError] = useState('')
```

### Step 2: Seed the new states from the fetched entry

In the existing fetch effect (line 68-84), where `setEditTitle` and `setEditBody` are called inside the `.then((e) => { ... })` block (lines 72-74), add seeding for the dates:

Replace:

```tsx
      .then((e) => {
        setEntry(e)
        setEditTitle(e.title ?? '')
        setEditBody(e.body_markdown ?? '')
        // If we arrived from the picker, auto-start polling for LLM body
        if (fromPick && !e.body_markdown) {
          setRegenStartedAt(e.updated_at)
          setRegenStartTime(new Date().toISOString())
          setPollingRegen(true)
        }
      })
```

With:

```tsx
      .then((e) => {
        setEntry(e)
        setEditTitle(e.title ?? '')
        setEditBody(e.body_markdown ?? '')
        setEditEntryDate(e.entry_date)
        setEditEntryEndDate(e.entry_end_date ?? '')
        // If we arrived from the picker, auto-start polling for LLM body
        if (fromPick && !e.body_markdown) {
          setRegenStartedAt(e.updated_at)
          setRegenStartTime(new Date().toISOString())
          setPollingRegen(true)
        }
      })
```

### Step 3: Seed the new states in `startEdit`

Replace `startEdit` at lines 86-91:

```tsx
  function startEdit() {
    if (!entry) return
    setEditTitle(entry.title ?? '')
    setEditBody(entry.body_markdown ?? '')
    setEditing(true)
  }
```

With:

```tsx
  function startEdit() {
    if (!entry) return
    setEditTitle(entry.title ?? '')
    setEditBody(entry.body_markdown ?? '')
    setEditEntryDate(entry.entry_date)
    setEditEntryEndDate(entry.entry_end_date ?? '')
    setEditDateError('')
    setEditing(true)
  }
```

### Step 4: Rewrite `handleSave` for date validation + payload

Replace `handleSave` at lines 93-108:

```tsx
  async function handleSave() {
    if (!entry) return
    setSaving(true)
    try {
      const updated = await api.entries.patch(entry.id, {
        title: editTitle || null,
        body_markdown: editBody || null,
      })
      setEntry(updated)
      setEditing(false)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }
```

With:

```tsx
  async function handleSave() {
    if (!entry) return

    setEditDateError('')

    if (!editEntryDate) {
      setEditDateError('Start date is required')
      return
    }
    if (editEntryEndDate && editEntryEndDate < editEntryDate) {
      setEditDateError('End date must be on or after start date')
      return
    }

    setSaving(true)
    try {
      const updated = await api.entries.patch(entry.id, {
        title: editTitle || null,
        body_markdown: editBody || null,
        entry_date: editEntryDate,
        entry_end_date: editEntryEndDate || null,
      })
      setEntry(updated)
      setEditing(false)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }
```

**Why ISO `<` comparison is safe:** `YYYY-MM-DD` strings sort lexicographically the same as chronologically. No `Date` parsing needed. (Same idiom as the manual-entry popover.)

**Why `entry_end_date: editEntryEndDate || null`:** sends explicit `null` to clear, which only works after Task 1's `exclude_unset` fix.

### Step 5: Add date inputs to the edit form JSX

Replace the edit form section at lines 225-256:

```tsx
        {editing ? (
          <div className="card" style={{ marginTop: '1rem' }}>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-title">Title</label>
              <input
                id="entry-title"
                type="text"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                placeholder="(no title)"
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-body">Body</label>
              <textarea
                id="entry-body"
                value={editBody}
                onChange={(e) => setEditBody(e.target.value)}
                rows={20}
                style={{ width: '100%', fontFamily: 'inherit', fontSize: '0.9rem', resize: 'vertical' }}
                placeholder="Entry body (Markdown)"
              />
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button className="btn btn-secondary" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
```

With:

```tsx
        {editing ? (
          <div className="card" style={{ marginTop: '1rem' }}>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-date">Start date</label>
              <input
                id="entry-date"
                type="date"
                value={editEntryDate}
                onChange={(e) => setEditEntryDate(e.target.value)}
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-end-date">End date (optional)</label>
              <input
                id="entry-end-date"
                type="date"
                value={editEntryEndDate}
                onChange={(e) => setEditEntryEndDate(e.target.value)}
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-title">Title</label>
              <input
                id="entry-title"
                type="text"
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                placeholder="(no title)"
              />
            </div>
            <div className="form-field">
              <label className="form-label" htmlFor="entry-body">Body</label>
              <textarea
                id="entry-body"
                value={editBody}
                onChange={(e) => setEditBody(e.target.value)}
                rows={20}
                style={{ width: '100%', fontFamily: 'inherit', fontSize: '0.9rem', resize: 'vertical' }}
                placeholder="Entry body (Markdown)"
              />
            </div>
            {editDateError && (
              <p className="error-message" style={{ marginBottom: '0.5rem' }}>{editDateError}</p>
            )}
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving || !editEntryDate}>
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button className="btn btn-secondary" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
```

### Step 6: Verify typecheck clean

```bash
cd apps/web
npx tsc --noEmit
```

Expected: no output (clean).

If `Property 'entry_date' does not exist on type 'Partial<Entry>'`: the `Entry` interface in `apps/web/src/lib/api.ts` already has `entry_date: string` and `entry_end_date: string | null` (verified at lines 157-158); no widening needed.

---

## Task 5: Rebuild Docker web image and run E2E (TDD green)

- [ ] **Step 1: Rebuild and restart web container**

```bash
cd /path/to/repo  # repo root
docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile dev up -d --build web
```

Wait ~10s.

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/
```

Expected: `200`

If the curl returns connection refused: the rebuild may have lost port mapping. Re-run the compose command above — note that **both** `-f` flags are required; without `docker-compose.dev.yml` the port mapping (`0.0.0.0:3000->3000/tcp`) is dropped.

- [ ] **Step 2: Run the new E2E tests**

```bash
cd apps/web
npm run test:e2e -- entry-date-edit.spec.ts
```

Expected: all 4 pass.

If `#entry-date` selector times out: Docker is still serving old code. Re-run Step 1.

- [ ] **Step 3: If failures — fix implementation, NOT tests**

---

## Task 6: Run full quality gates

- [ ] **Step 1: Backend tests**

```bash
cd apps/api
pytest -v
```

Expected: all pass.

- [ ] **Step 2: Frontend lint + typecheck**

```bash
cd apps/web
npm run lint
npm run typecheck
```

Expected: lint reports `No ESLint warnings or errors`; typecheck has no output.

- [ ] **Step 3: Full E2E suite**

```bash
npm run test:e2e
```

Expected: all tests pass. Note: `golden-path.spec.ts` and `restore-tier-limit.spec.ts` are pre-existing flaky due to auth rate limiting — if they fail, run the full suite a second time. The existing `golden-path` test #5 uses `button:has-text("Edit")` followed by `#entry-body` — it does NOT touch the new date inputs, so it should keep passing.

- [ ] **Step 4: Top-level integration**

```bash
cd /path/to/repo  # repo root
make test-all
```

Expected: `All checks passed.`

---

## Task 7: Final commit

- [ ] **Step 1: Stage all frontend files**

```bash
git add apps/web/src/app/entries/\[entryId\]/page.tsx \
        apps/web/e2e/entry-date-edit.spec.ts \
        docs/superpowers/plans/2026-05-28-entry-date-edit.md
```

- [ ] **Step 2: Verify**

```bash
git status
```

Expected: only those 3 files staged. (Backend changes from Tasks 1-2 were already committed separately.)

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(web): add start/end date editing to entry edit form

The entry detail page Edit button now exposes Start date (required)
and End date (optional) inputs alongside the existing Title and Body
fields. Validates that end >= start client-side; surfaces the inline
error using the same .error-message pattern as the manual-entry
popover.

Clearing the End date sends explicit null, which the backend now
respects after the exclude_unset fix in entries.py.

Covered by new Playwright E2E in apps/web/e2e/entry-date-edit.spec.ts:
- Edit form pre-fills with current dates
- end < start blocks save with inline error
- Saving new dates persists and rerenders the date range
- Clearing end date collapses a multi-day entry to single-day
EOF
)"
```

---

## Verification (end-to-end manual test)

After Task 6 completes, do a manual smoke test in the browser:

1. **Backend running** (`docker compose ps` shows `api` healthy on 8000), **frontend running** (Docker `web` container on 3000).
2. Open `http://localhost:3000`, log in (or register), open a diary.
3. Click **New entry** → in the popover, fill in Start = today, End = today + 2 days, Title = "Trip" → Create entry.
4. On the entry detail page, the header should render the date range with an en-dash.
5. Click **Edit** — the form should now show Start date / End date / Title / Body, with dates pre-filled.
6. Change Start date to today + 5 days → click **Save** → header re-renders with the new range.
7. Re-open Edit, clear the End date input (delete its value) → click **Save** → header now shows just the start date (en-dash gone).
8. Re-open Edit, set End date earlier than Start date → click **Save** → inline error "End date must be on or after start date" appears, no navigation, form stays open.
9. From repo root: `make test-all` — full backend + frontend test pass.

---

## Risks / Watchpoints

- **`exclude_unset` change is load-bearing for null-clearing** (Task 1). If skipped, Task 4's "clear end date" UX silently does nothing — backend just drops the null. Task 2 catches this with `test_patch_clear_end_date_with_null`.
- **Rule-created entries may duplicate on next worker scan if their date is changed.** Out of scope to gate; documented in the "Worker re-scan safety" context section. If a user reports duplicates, add a check that disables the date inputs when `entry.creation_source === "rule"`.
- **`router.push` is not used in Save** — page does not navigate; we mutate `entry` state via `setEntry(updated)` so the date range header re-renders in place. This matches the existing title/body save behavior.
- **Docker rebuild gotcha:** `docker compose up -d --build web` (without `docker-compose.dev.yml`) drops the port mapping. Always use both compose files together.
- **Pre-existing flaky E2E tests:** `golden-path.spec.ts` and `restore-tier-limit.spec.ts` use real-browser login and can hit the 10/min auth rate limiter. Not caused by this change; re-run the suite if they fail.
- **`#entry-end-date` empty string vs null:** the input emits `''` when cleared; `editEntryEndDate || null` collapses that to `null` for the API, which after Task 1 actually clears the column. Without Task 1, the column would be silently preserved.
