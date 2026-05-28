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
