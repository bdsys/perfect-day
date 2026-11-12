/**
 * E2E: the diary page when Google Calendar is not connected (C-18, C-19).
 *
 * Scan and Backfill read the diary OWNER's Google Calendar, and only the
 * owner may start them. When they can't be used they are switched off and the
 * page says why in visible text (a hover tooltip alone is what made
 * Regenerate look broken, C-20). A lost connection gets a banner.
 */

import { test, expect, request as playwrightRequest, type Page } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const HINT = 'Connect Google Calendar to scan for new events and backfill past ones.'
// Next.js adds its own role="alert" route announcer, so the banner is found by its text.
const BANNER = /Google Calendar is disconnected/
const OWNER_ONLY = 'Only the diary owner can scan for new events.'

const shared = { email: '', token: '', diaryId: '' }

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext()
  const email = `e2e-calendar-${Date.now()}@example.com`
  const reg = await ctx.post(`${API}/v1/auth/register`, { data: { email, password: 'Password1!' } })
  if (!reg.ok()) throw new Error(`Register failed: ${reg.status()} ${await reg.text()}`)
  const { access_token: token } = (await reg.json()) as { access_token: string }
  const diary = await ctx.post(`${API}/v1/diaries`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: 'Calendar Connection Diary', timezone: 'UTC' },
  })
  if (!diary.ok()) throw new Error(`Create diary failed: ${diary.status()} ${await diary.text()}`)
  shared.email = email
  shared.token = token
  shared.diaryId = ((await diary.json()) as { id: string }).id
  await ctx.dispose()
})

type Calendar = 'connected' | 'lost' | 'none'

/**
 * Open the diary page. *calendar* is the diary OWNER's connection, as the
 * scan config reports it; *canScan* is whether the viewer is the owner.
 */
async function openDiary(page: Page, calendar: Calendar, canScan = true) {
  await page.route(`${API}/v1/auth/refresh`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ access_token: shared.token }) }),
  )
  await page.route(`${API}/v1/auth/me`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'test-user', email: shared.email, display_name: null }),
    }),
  )
  await page.route(`${API}/v1/diaries/${shared.diaryId}/scan`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        can_scan: canScan, calendar, scan_enabled: true, scan_interval_minutes: 60,
        last_scan_status: null, next_scan_after: null, consecutive_failures: 0,
      }),
    }),
  )
  await page.goto(`/diaries/${shared.diaryId}`)
  await page.waitForURL(`**/diaries/${shared.diaryId}`, { timeout: 10_000 })
}

test.describe('Owner without a working Google Calendar', () => {
  test('never connected: Scan and Backfill are switched off and the page says how to turn them on', async ({ page }) => {
    await openDiary(page, 'none')

    await expect(page.getByRole('button', { name: 'Scan now' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Scan options' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Backfill' })).toBeDisabled()
    await expect(page.getByText(HINT)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Scan now' })).toHaveAccessibleDescription(HINT)
    await expect(page.getByRole('button', { name: 'Connect Google Calendar' })).toBeVisible()
    await expect(page.getByText(BANNER)).toHaveCount(0)
  })

  test('connection lost: a banner says so and offers Reconnect', async ({ page }) => {
    await openDiary(page, 'lost')

    const banner = page.getByRole('alert').filter({ hasText: BANNER })
    await expect(banner).toContainText('Google Calendar is disconnected')
    await expect(banner.getByRole('button', { name: 'Reconnect Google Calendar' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Scan now' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Backfill' })).toBeDisabled()
  })

  test('connected: Scan and Backfill are on and there is no hint', async ({ page }) => {
    await openDiary(page, 'connected')

    await expect(page.getByRole('button', { name: 'Scan now' })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Backfill' })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'New entry from Google Calendar' })).toBeVisible()
    await expect(page.getByText(HINT)).toHaveCount(0)
    await expect(page.getByText(BANNER)).toHaveCount(0)
  })
})

test.describe('Co-parent (scanning is owner-only, decision 2026-11-12)', () => {
  test('Scan and Backfill are switched off and the page says only the owner can scan', async ({ page }) => {
    await openDiary(page, 'connected', false)

    await expect(page.getByRole('button', { name: 'Scan now' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Backfill' })).toBeDisabled()
    await expect(page.getByText(OWNER_ONLY)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Scan now' })).toHaveAccessibleDescription(OWNER_ONLY)
    await expect(page.getByRole('button', { name: /Connect Google Calendar/ })).toHaveCount(0)
    // The synced events are the owner's own calendar (S-11).
    await expect(page.getByRole('button', { name: 'New entry from Google Calendar' })).toHaveCount(0)
  })

  test("the owner's lost connection: a banner asks them to tell the owner, with no Reconnect button", async ({ page }) => {
    await openDiary(page, 'lost', false)

    const banner = page.getByRole('alert').filter({ hasText: BANNER })
    await expect(banner).toContainText("The diary owner's Google Calendar is disconnected")
    await expect(banner).toContainText('Ask them to reconnect it')
    await expect(page.getByRole('button', { name: /Reconnect/ })).toHaveCount(0)
  })
})

test.describe('Coming back from Google', () => {
  test('calendar not ticked tells the parent to tick it', async ({ page }) => {
    await openDiary(page, 'none')
    await page.goto('/diaries?google=partial&missing=calendar')

    await expect(page.getByText('tick the Calendar checkbox')).toBeVisible()
  })

  test('Cancel and failure read differently', async ({ page }) => {
    await openDiary(page, 'none')
    await page.goto('/diaries?google=cancelled')
    await expect(page.getByText('You cancelled the Google connection. Nothing was changed.')).toBeVisible()

    await page.goto('/diaries?google=failed&reason=token_exchange_failed')
    await expect(page.getByText('Google connection failed. Please try again.')).toBeVisible()
  })
})
