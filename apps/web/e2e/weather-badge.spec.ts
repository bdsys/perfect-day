/**
 * E2E: WeatherBadge visibility on the entry detail page.
 *
 * The WeatherBadge component renders `aria-label="Weather"` only when
 * `entry.enrichments` contains at least one item with `kind='weather'`.
 * Enrichments are written exclusively by background workers (the weather
 * enrichment worker calls an external API and INSERTs rows directly into the
 * `enrichments` table).  There is no public HTTP endpoint to seed enrichment
 * rows for test purposes.
 *
 * TODO: unblock this test by implementing one of:
 *   (a) A POST /v1/test/seed-enrichment endpoint (guarded by ENV=test) that
 *       inserts a row into `enrichments` for a given entry_id, OR
 *   (b) A direct psql/asyncpg seeding step in the Playwright globalSetup that
 *       connects to the test database and inserts the row before the browser
 *       opens.
 *
 * Until then the test is skipped but the full structure is in place so it can
 * be activated by simply removing the `test.skip()` call and adding the seed
 * step.
 */

import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const email = 'e2e-weather-badge@example.com'
const password = 'Password1!'
const diaryName = 'E2E Weather Badge Diary'

const sharedState = {
  diaryId: '',
  entryId: '',
  token: '',
}

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext()

  // Register — 201 first run, 409 on re-runs, both acceptable.
  await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })

  // Login to obtain a token for subsequent API calls.
  const loginRes = await ctx.post(`${API}/v1/auth/login`, {
    data: { email, password },
  })
  const { access_token } = (await loginRes.json()) as { access_token: string }
  sharedState.token = access_token

  const headers = { Authorization: `Bearer ${access_token}` }

  // Reuse an existing diary or create one.
  const listRes = await ctx.get(`${API}/v1/diaries`, { headers })
  const diaries = (await listRes.json()) as Array<{ id: string; name: string }>
  const existing = diaries.find((d) => d.name === diaryName)

  let diaryId: string
  if (existing) {
    diaryId = existing.id
  } else {
    const createRes = await ctx.post(`${API}/v1/diaries`, {
      headers,
      data: { name: diaryName, timezone: 'UTC' },
    })
    const created = (await createRes.json()) as { id: string }
    diaryId = created.id
  }
  sharedState.diaryId = diaryId

  // Create a fresh entry so we have a clean target for enrichment seeding.
  const entryRes = await ctx.post(`${API}/v1/diaries/${diaryId}/entries`, {
    headers,
    data: {
      entry_date: new Date().toISOString().slice(0, 10),
      title: `E2E Weather Badge Entry ${Date.now()}`,
      body_markdown: 'A sunny day.',
    },
  })
  const entry = (await entryRes.json()) as { id: string }
  sharedState.entryId = entry.id

  await ctx.dispose()
})

/** Log in via the real login page and wait for redirect. */
async function loginViaUI(page: import('@playwright/test').Page) {
  await page.goto('/login')
  await page.fill('#email', email)
  await page.fill('#password', password)
  await page.click('button[type=submit]')
  await page.waitForURL(/\/diaries/, { timeout: 10_000 })
}

test.describe('WeatherBadge on entry detail', () => {
  test(
    'weather badge is visible when entry has a weather enrichment',
    async ({ page }) => {
      // SKIP until enrichment seeding infrastructure is available.
      //
      // To activate: add a seeding step here that inserts a row into the
      // `enrichments` table for `sharedState.entryId` with kind='weather' and
      // a suitable payload, then remove the test.skip() call.
      //
      // Example seed payload:
      //   { weathercode: 1, temperature_min_c: 10, temperature_max_c: 22 }
      //
      // Required infrastructure (choose one):
      //   (a) POST /v1/test/seed-enrichment  { entry_id, kind, payload }
      //       — only exposed when API_ENV=test
      //   (b) Direct DB INSERT in Playwright globalSetup via asyncpg/psycopg2
      //
      // See top-of-file comment for details.
      test.skip(
        true,
        'requires POST /v1/test/seed-enrichment endpoint or direct DB seeding — ' +
          'enrichments are written only by background workers and cannot be created via the public API',
      )

      // --- Steps below run once seeding is wired up ---

      // 1. Seed a weather enrichment for sharedState.entryId  ← ADD THIS STEP

      // 2. Navigate to the entry detail page.
      await loginViaUI(page)
      await page.goto(`/entries/${sharedState.entryId}`)
      await page.waitForURL(`**/entries/${sharedState.entryId}`, { timeout: 10_000 })

      // 3. The WeatherBadge div should be present in the DOM with the expected
      //    aria-label.  It only renders when enrichments.filter(e =>
      //    e.kind === 'weather').length > 0.
      const weatherBadge = page.getByLabel('Weather')
      await expect(weatherBadge).toBeVisible({ timeout: 5_000 })
      await expect(weatherBadge).toContainText('°')
    },
  )
})
