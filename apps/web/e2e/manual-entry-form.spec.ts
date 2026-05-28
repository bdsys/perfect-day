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

    // Did NOT navigate.
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
