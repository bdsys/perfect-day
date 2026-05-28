import { test, expect, type APIRequestContext, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const password = 'Password1!'

// ---------------------------------------------------------------------------
// Shared test state — one user + diary, set up once for the whole suite.
// This avoids per-test register calls that hit the auth rate limiter (10/min).
// ---------------------------------------------------------------------------

const sharedState = {
  email: '',
  diaryId: '',
  token: '',
  headers: {} as Record<string, string>,
}

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext()
  const email = `e2e-backfill-${Date.now()}@example.com`

  const regResp = await ctx.post(`${API}/v1/auth/register`, {
    data: { email, password },
  })
  if (!regResp.ok()) throw new Error(`Register failed: ${regResp.status()} ${await regResp.text()}`)
  const { access_token: token } = await regResp.json() as { access_token: string }

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }
  const diaryResp = await ctx.post(`${API}/v1/diaries`, {
    headers,
    data: { name: 'Backfill Test Diary', timezone: 'UTC' },
  })
  if (!diaryResp.ok()) throw new Error(`Create diary failed: ${diaryResp.status()} ${await diaryResp.text()}`)
  const { id: diaryId } = await diaryResp.json() as { id: string }

  sharedState.email = email
  sharedState.diaryId = diaryId
  sharedState.token = token
  sharedState.headers = headers
  await ctx.dispose()
})

// Bypass the browser login flow entirely by mocking the /refresh and /me
// endpoints that AuthContext calls on mount. This prevents hitting the
// hardcoded 10/min rate limit on POST /v1/auth/login.
async function loginAndNavigateToDiary(page: import('@playwright/test').Page) {
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

// ---------------------------------------------------------------------------
// Group 1 — UI rendering and validation
// ---------------------------------------------------------------------------

test.describe('Backfill UI', () => {
  test('T1: Backfill button is visible on diary page', async ({ page }) => {
    await loginAndNavigateToDiary(page)
    await expect(page.getByRole('button', { name: 'Backfill' })).toBeVisible()
  })

  test('T2: Clicking Backfill opens popover with date inputs', async ({ page }) => {
    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()

    await expect(page.getByLabel('From date')).toBeVisible()
    await expect(page.getByLabel('To date')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Start backfill' })).toBeVisible()
  })

  test('T3: Validation: empty dates show error in popover', async ({ page }) => {
    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // Popover stays open and shows error
    await expect(page.getByText('Both dates are required.')).toBeVisible()
    await expect(page.getByLabel('From date')).toBeVisible()
  })

  test('T4: Validation: from_date > to_date shows error in popover', async ({ page }) => {
    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-05-10')
    await page.getByLabel('To date').fill('2026-05-01')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    await expect(page.getByText('Start date must be before end date.')).toBeVisible()
    await expect(page.getByLabel('From date')).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // Group 2 — Submission with mocked backend
  // ---------------------------------------------------------------------------

  test('T5: Valid submission closes popover and shows status panel', async ({ page }) => {
    const { diaryId } = sharedState

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 'aaaaaaaa-0000-0000-0000-000000000001',
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'pending',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    // GET poll → completed immediately so polling stops
    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/aaaaaaaa-0000-0000-0000-000000000001`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 'aaaaaaaa-0000-0000-0000-000000000001',
          diary_id: diaryId,
          from_date: '2026-04-01',
          to_date: '2026-04-30',
          sources: ['google_calendar'],
          status: 'completed',
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          events_ingested: 5,
          entries_created: 3,
          error: null,
        }),
      })
    })

    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // Popover closes after successful POST
    await expect(page.getByLabel('From date')).not.toBeVisible({ timeout: 3_000 })

    // StatusPanel appears (pending→running or already completed depending on poll timing)
    await expect(
      page.locator('.status-panel').filter({ hasText: /Backfill/ })
    ).toBeVisible({ timeout: 5_000 })
  })

  test('T6: 409 scan-in-progress keeps popover open with error message', async ({ page }) => {
    const { diaryId } = sharedState

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'scan_in_progress' }),
          headers: { 'Retry-After': '60' },
        })
      } else {
        await route.continue()
      }
    })

    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // Popover stays open, error visible
    await expect(page.getByText('A scan or backfill is already running.')).toBeVisible({ timeout: 3_000 })
    await expect(page.getByLabel('From date')).toBeVisible()
  })

  test('T7: 422 backend validation error surfaces in popover', async ({ page }) => {
    const { diaryId } = sharedState

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'range must be ≤ 365 days' }),
        })
      } else {
        await route.continue()
      }
    })

    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2024-01-01')
    await page.getByLabel('To date').fill('2026-05-01')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // Popover stays open, backend error message visible
    await expect(page.getByText('range must be ≤ 365 days')).toBeVisible({ timeout: 3_000 })
    await expect(page.getByLabel('From date')).toBeVisible()
  })

  // ---------------------------------------------------------------------------
  // Group 3 — Polling lifecycle
  // ---------------------------------------------------------------------------

  test('T8: Polling progresses through pending → running → completed', async ({ page }) => {
    const { diaryId } = sharedState
    const runId = 'cccccccc-0000-0000-0000-000000000003'
    let pollCount = 0

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'pending',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/${runId}`, async (route) => {
      if (route.request().method() !== 'GET') { await route.continue(); return }
      pollCount++
      const status = pollCount === 1 ? 'pending' : pollCount === 2 ? 'running' : 'completed'
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: runId,
          diary_id: diaryId,
          from_date: '2026-04-01',
          to_date: '2026-04-30',
          sources: ['google_calendar'],
          status,
          started_at: new Date().toISOString(),
          completed_at: status === 'completed' ? new Date().toISOString() : null,
          events_ingested: status === 'completed' ? 7 : 0,
          entries_created: status === 'completed' ? 2 : 0,
          error: null,
        }),
      })
    })

    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // StatusPanel eventually shows "Backfill complete" with counts
    await expect(
      page.locator('.status-panel').filter({ hasText: 'Backfill complete' })
    ).toBeVisible({ timeout: 15_000 })
    await expect(page.locator('.status-panel').filter({ hasText: '7 events' })).toBeVisible()
  })

  test('T9: Polling stops on failed and shows error message', async ({ page }) => {
    const { diaryId } = sharedState
    const runId = 'dddddddd-0000-0000-0000-000000000004'

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/${runId}`, async (route) => {
      if (route.request().method() !== 'GET') { await route.continue(); return }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: runId,
          diary_id: diaryId,
          from_date: '2026-04-01',
          to_date: '2026-04-30',
          sources: ['google_calendar'],
          status: 'failed',
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          events_ingested: 0,
          entries_created: 0,
          error: 'API quota exceeded',
        }),
      })
    })

    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // StatusPanel shows "Backfill failed" and the error detail
    await expect(
      page.locator('.status-panel').filter({ hasText: 'Backfill failed' })
    ).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('.status-panel').filter({ hasText: 'API quota exceeded' })).toBeVisible()
  })

  test('T10: Entry list refreshes after completed backfill', async ({ page, request }) => {
    const { diaryId, headers } = sharedState

    // Pre-create an entry so we can verify the list is non-empty on page load
    const today = new Date().toISOString().slice(0, 10)
    await request.post(`${API}/v1/diaries/${diaryId}/entries`, {
      headers,
      data: { entry_date: today, title: 'Pre-existing entry' },
    })

    const runId = 'eeeeeeee-0000-0000-0000-000000000005'

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/${runId}`, async (route) => {
      if (route.request().method() !== 'GET') { await route.continue(); return }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: runId,
          diary_id: diaryId,
          from_date: '2026-04-01',
          to_date: '2026-04-30',
          sources: ['google_calendar'],
          status: 'completed',
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          events_ingested: 0,
          entries_created: 0,
          error: null,
        }),
      })
    })

    await loginAndNavigateToDiary(page)

    // Wait for the initial entries load before registering the refresh watcher
    await expect(page.locator('.entry-card')).toBeVisible({ timeout: 5_000 })

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')

    // Register before submit so we capture the refresh triggered by completed status
    const entriesRefreshPromise = page.waitForRequest(
      (req) => req.url().includes(`/v1/diaries/${diaryId}/entries`) && req.method() === 'GET',
    )

    await page.getByRole('button', { name: 'Start backfill' }).click()

    // Wait for status panel to show complete
    await expect(
      page.locator('.status-panel').filter({ hasText: 'Backfill complete' })
    ).toBeVisible({ timeout: 10_000 })

    // Entries list was re-fetched after completion
    await entriesRefreshPromise
  })

  test('T11: StatusPanel auto-dismisses 8s after completed', async ({ page }) => {
    const { diaryId } = sharedState
    const runId = 'ffffffff-0000-0000-0000-000000000006'

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/${runId}`, async (route) => {
      if (route.request().method() !== 'GET') { await route.continue(); return }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: runId,
          diary_id: diaryId,
          from_date: '2026-04-01',
          to_date: '2026-04-30',
          sources: ['google_calendar'],
          status: 'completed',
          started_at: new Date().toISOString(),
          completed_at: new Date().toISOString(),
          events_ingested: 0,
          entries_created: 0,
          error: null,
        }),
      })
    })

    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // StatusPanel appears
    await expect(
      page.locator('.status-panel').filter({ hasText: 'Backfill complete' })
    ).toBeVisible({ timeout: 10_000 })

    // Auto-dismisses after 8s (StatusPanel.tsx has 8s setTimeout for 'success' state)
    await expect(
      page.locator('.status-panel').filter({ hasText: 'Backfill complete' })
    ).not.toBeVisible({ timeout: 11_000 })
  })

  // ---------------------------------------------------------------------------
  // Group 4 — Cancellation
  // ---------------------------------------------------------------------------

  test('T12: Cancel backfill button appears while running and cancels on click', async ({ page }) => {
    const { diaryId } = sharedState
    const runId = 'bbbbbbbb-0000-0000-0000-000000000002'
    let cancelCalled = false

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/${runId}`, async (route) => {
      if (route.request().method() === 'DELETE') {
        cancelCalled = true
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'cancelled',
            started_at: new Date().toISOString(),
            completed_at: new Date().toISOString(),
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        // GET poll — return running so cancel button stays visible
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      }
    })

    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // Cancel button appears while status is running
    await expect(page.getByRole('button', { name: 'Cancel backfill' })).toBeVisible({ timeout: 5_000 })

    await page.getByRole('button', { name: 'Cancel backfill' }).click()

    expect(cancelCalled).toBe(true)
    // Cancel button disappears after cancellation
    await expect(page.getByRole('button', { name: /Cancel/ })).not.toBeVisible({ timeout: 5_000 })
  })

  test('T13: Cancelled state shows neutral styling, not red failure', async ({ page }) => {
    const { diaryId } = sharedState
    const runId = '11111111-0000-0000-0000-000000000007'

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/${runId}`, async (route) => {
      if (route.request().method() === 'DELETE') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'cancelled',
            started_at: new Date().toISOString(),
            completed_at: new Date().toISOString(),
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      }
    })

    await loginAndNavigateToDiary(page)
    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    await expect(page.getByRole('button', { name: 'Cancel backfill' })).toBeVisible({ timeout: 5_000 })
    await page.getByRole('button', { name: 'Cancel backfill' }).click()

    // Panel shows "Backfill cancelled" headline
    await expect(page.locator('.status-panel').filter({ hasText: 'Backfill cancelled' })).toBeVisible({ timeout: 5_000 })
    // Panel MUST NOT have the 'failed' CSS class (red ✕ styling)
    await expect(page.locator('.status-panel.failed').filter({ hasText: 'Backfill cancelled' })).not.toBeVisible()
  })

  test('T14: Cancel button is disabled while DELETE is in flight', async ({ page }) => {
    const { diaryId } = sharedState
    const runId = '22222222-0000-0000-0000-000000000008'

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/${runId}`, async (route) => {
      if (route.request().method() === 'DELETE') {
        // Slow DELETE to observe in-flight state
        await new Promise((r) => setTimeout(r, 1200))
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'cancelled',
            started_at: new Date().toISOString(),
            completed_at: new Date().toISOString(),
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      }
    })

    await loginAndNavigateToDiary(page)
    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    await expect(page.getByRole('button', { name: 'Cancel backfill' })).toBeVisible({ timeout: 5_000 })
    await page.getByRole('button', { name: 'Cancel backfill' }).click()

    // During the 1.2s delay the button should show "Cancelling…" and be disabled
    await expect(page.getByRole('button', { name: 'Cancelling…' })).toBeDisabled({ timeout: 1_000 })

    // After the DELETE completes, the cancel button goes away
    await expect(page.getByRole('button', { name: /Cancel/ })).not.toBeVisible({ timeout: 5_000 })
  })

  // ---------------------------------------------------------------------------
  // Group 5 — Full-stack smoke test (real backend)
  // ---------------------------------------------------------------------------

  test('T15: Real backfill POST reaches the API and returns a valid run', async ({ page }) => {
    await loginAndNavigateToDiary(page)

    const today = new Date().toISOString().slice(0, 10)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill(today)
    await page.getByLabel('To date').fill(today)
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // Popover closes — POST succeeded with a real run
    await expect(page.getByLabel('From date')).not.toBeVisible({ timeout: 5_000 })

    // StatusPanel appears with any "Backfill" headline
    await expect(
      page.locator('.status-panel').filter({ hasText: /Backfill/ })
    ).toBeVisible({ timeout: 10_000 })
  })

  test('T16: Persistent poll failures surface as failed state and stop polling', async ({ page }) => {
    const { diaryId } = sharedState
    const runId = '33333333-0000-0000-0000-000000000009'
    let pollAttempts = 0

    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill`, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 202,
          contentType: 'application/json',
          body: JSON.stringify({
            id: runId,
            diary_id: diaryId,
            from_date: '2026-04-01',
            to_date: '2026-04-30',
            sources: ['google_calendar'],
            status: 'running',
            started_at: new Date().toISOString(),
            completed_at: null,
            events_ingested: 0,
            entries_created: 0,
            error: null,
          }),
        })
      } else {
        await route.continue()
      }
    })

    // Every GET poll fails with 500
    await page.route(`${API}/v1/diaries/${diaryId}/scan/backfill/${runId}`, async (route) => {
      if (route.request().method() !== 'GET') { await route.continue(); return }
      pollAttempts++
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'internal_server_error' }),
      })
    })

    await loginAndNavigateToDiary(page)

    await page.getByRole('button', { name: 'Backfill' }).click()
    await page.getByLabel('From date').fill('2026-04-01')
    await page.getByLabel('To date').fill('2026-04-30')
    await page.getByRole('button', { name: 'Start backfill' }).click()

    // After ~5 consecutive failures (~15s at 3s poll interval) the UI should
    // surface "Connection lost" via the failed state.
    await expect(
      page.locator('.status-panel').filter({ hasText: 'Connection lost' })
    ).toBeVisible({ timeout: 25_000 })

    // Polling stopped — no more attempts after the threshold trip.
    const stoppedAt = pollAttempts
    await page.waitForTimeout(4_000)
    expect(pollAttempts).toBeLessThanOrEqual(stoppedAt + 1)
  })
})
