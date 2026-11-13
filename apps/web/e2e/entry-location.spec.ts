/**
 * E2E: an entry's place, the city search, the Open-Meteo credit and the
 * advanced time zone (PoC item 24).
 *
 * The city search is mocked with page.route so no test calls Open-Meteo.
 * Places, events and shares are seeded straight into the test database, as
 * in suggestion-box.spec.ts: the worker that fills places in would otherwise
 * call the real geocoder.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { test, expect, request as playwrightRequest, type Page } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const REPO_ROOT = path.resolve(__dirname, '../../..')
const password = 'Password1!'

const MUKILTEO = {
  name: 'Mukilteo', admin1: 'Washington', country: 'United States',
  lat: 47.94454, lon: -122.30458, timezone: 'America/Los_Angeles',
  label: 'Mukilteo, Washington, United States',
}

type Who = { email: string; token: string; id: string }
const shared = { owner: {} as Who, viewer: {} as Who, diaryId: '' }

async function register(prefix: string): Promise<Who> {
  const ctx = await playwrightRequest.newContext()
  const email = `e2e-${prefix}-${Date.now()}@example.com`
  const reg = await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })
  if (!reg.ok()) throw new Error(`Register failed: ${reg.status()} ${await reg.text()}`)
  const { access_token: token } = (await reg.json()) as { access_token: string }
  const me = await ctx.get(`${API}/v1/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
  const { id } = (await me.json()) as { id: string }
  await ctx.dispose()
  return { email, token, id }
}

test.beforeAll(async () => {
  shared.owner = await register('place-owner')
  shared.viewer = await register('place-viewer')
  const ctx = await playwrightRequest.newContext()
  const diary = await ctx.post(`${API}/v1/diaries`, {
    headers: { Authorization: `Bearer ${shared.owner.token}` },
    data: { name: 'Place Diary', timezone: 'UTC' },
  })
  if (!diary.ok()) throw new Error(`Create diary failed: ${diary.status()} ${await diary.text()}`)
  shared.diaryId = ((await diary.json()) as { id: string }).id
  await ctx.dispose()
  // This spec makes more entries than the free tier's 5.
  sql(`UPDATE users SET subscription_tier = 'tier1' WHERE id = '${shared.owner.id}'`)
  sql(
    `INSERT INTO diary_permissions (id, diary_id, user_id, role) ` +
      `VALUES (gen_random_uuid(), '${shared.diaryId}', '${shared.viewer.id}', 'viewer')`,
  )
})

function sql(statement: string) {
  execFileSync(
    'docker',
    ['compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.test.yml', 'exec', '-T',
      'postgres', 'psql', '-U', 'perfectday', '-d', 'perfectday_test', '-v', 'ON_ERROR_STOP=1',
      '-c', statement],
    { cwd: REPO_ROOT, stdio: 'pipe' },
  )
}

async function newEntry(): Promise<string> {
  const ctx = await playwrightRequest.newContext()
  const r = await ctx.post(`${API}/v1/diaries/${shared.diaryId}/entries`, {
    headers: { Authorization: `Bearer ${shared.owner.token}` },
    data: { entry_date: '2026-05-16', title: 'A day out', body_markdown: 'We went out.' },
  })
  if (!r.ok()) throw new Error(`Create entry failed: ${r.status()} ${await r.text()}`)
  const { id } = (await r.json()) as { id: string }
  await ctx.dispose()
  return id
}

function seedPlace(entryId: string, name: string | null, source: string) {
  const label = name === null ? 'NULL' : `'${name}'`
  sql(
    `UPDATE entries SET location_name = ${label}, lat = 47.94454, lon = -122.30458, ` +
      `location_source = '${source}' WHERE id = '${entryId}'`,
  )
}

async function openAs(page: Page, who: Who, url: string) {
  await page.route(`${API}/v1/auth/refresh`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ access_token: who.token }) }),
  )
  await page.route(`${API}/v1/auth/me`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: who.id, email: who.email, display_name: null }),
    }),
  )
  await page.route(`${API}/v1/geocode?**`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([MUKILTEO]) }),
  )
  await page.goto(url)
  await page.waitForURL(`**${url}`, { timeout: 10_000 })
}

function patchBody(page: Page, urlPart: string) {
  return page
    .waitForRequest((req) => req.method() === 'PATCH' && req.url().includes(urlPart))
    .then((req) => req.postDataJSON() as Record<string, unknown>)
}

test.describe('Entry place', () => {
  test('shows the place and the Open-Meteo credit', async ({ page }) => {
    const id = await newEntry()
    seedPlace(id, 'Mukilteo, Washington', 'event')
    await openAs(page, shared.owner, `/entries/${id}`)

    await expect(page.getByTestId('entry-place')).toHaveText(/Mukilteo, Washington/)
    await expect(page.getByRole('link', { name: 'Location and weather: Open-Meteo' })).toHaveAttribute(
      'href', 'https://open-meteo.com/',
    )
  })

  test('a place from a photo says so', async ({ page }) => {
    const id = await newEntry()
    seedPlace(id, null, 'photo')
    await openAs(page, shared.owner, `/entries/${id}`)

    await expect(page.getByTestId('entry-place')).toHaveText(/From a photo/)
  })

  test('the parent picks a place with the city search', async ({ page }) => {
    const id = await newEntry()
    await openAs(page, shared.owner, `/entries/${id}`)

    await page.getByRole('button', { name: 'Edit location' }).click()
    await page.getByRole('searchbox', { name: 'Location' }).fill('Muk')
    const sent = patchBody(page, `/v1/entries/${id}`)
    await page.getByRole('button', { name: MUKILTEO.label }).click()

    expect((await sent).location).toEqual({ name: 'Mukilteo, Washington', lat: MUKILTEO.lat, lon: MUKILTEO.lon })
    await expect(page.getByTestId('entry-place')).toHaveText(/Mukilteo, Washington/)
  })

  test('"Use automatic location" hands the place back to the app', async ({ page }) => {
    const id = await newEntry()
    seedPlace(id, 'Mukilteo, Washington', 'parent')
    await openAs(page, shared.owner, `/entries/${id}`)

    await page.getByRole('button', { name: 'Edit location' }).click()
    const sent = patchBody(page, `/v1/entries/${id}`)
    await page.getByRole('button', { name: 'Use automatic location' }).click()

    expect((await sent).location).toBeNull()
  })

  test('a viewer sees the place but cannot change it', async ({ page }) => {
    const id = await newEntry()
    seedPlace(id, 'Mukilteo, Washington', 'event')
    sql(`UPDATE entries SET status = 'published', published_at = now() WHERE id = '${id}'`)
    await openAs(page, shared.viewer, `/entries/${id}`)

    await expect(page.getByTestId('entry-place')).toHaveText(/Mukilteo, Washington/)
    await expect(page.getByRole('button', { name: 'Edit location' })).toHaveCount(0)
  })
})

test.describe('Advanced time zone', () => {
  test('is tucked away, and shows event times in the chosen zone', async ({ page }) => {
    const id = await newEntry()
    sql(
      `INSERT INTO events (id, diary_id, entry_id, source, external_id, occurred_at, payload) VALUES ` +
        `(gen_random_uuid(), '${shared.diaryId}', '${id}', 'google_calendar', 'tz-${id}', ` +
        `'2026-05-17T00:00:00Z', '{"summary": "Breakfast in Tokyo", ` +
        `"start": {"dateTime": "2026-05-17T09:00:00+09:00"}, ` +
        `"end": {"dateTime": "2026-05-17T10:00:00+09:00"}}')`,
    )
    await openAs(page, shared.owner, `/entries/${id}`)

    const zone = page.getByLabel('Time zone')
    await expect(zone).toBeHidden()
    await page.getByText('Advanced', { exact: true }).click()
    await expect(zone).toBeVisible()

    const sent = patchBody(page, `/v1/entries/${id}`)
    await zone.selectOption('Asia/Tokyo')

    expect((await sent).tz_override).toBe('Asia/Tokyo')
    await expect(page.getByText(/09:00.*Breakfast in Tokyo/)).toBeVisible()
  })
})

test.describe('Diary home location', () => {
  test('is set from diary settings with the same search', async ({ page }) => {
    await openAs(page, shared.owner, `/diaries/${shared.diaryId}/settings`)

    await page.getByRole('searchbox', { name: 'Home location' }).fill('Muk')
    await page.getByRole('button', { name: MUKILTEO.label }).click()
    const sent = patchBody(page, `/v1/diaries/${shared.diaryId}`)
    await page.getByRole('button', { name: /^Save/ }).click()

    expect(await sent).toMatchObject({
      location_name: 'Mukilteo, Washington', lat: MUKILTEO.lat, lon: MUKILTEO.lon,
    })
  })
})
