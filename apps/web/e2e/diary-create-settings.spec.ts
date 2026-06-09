import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const password = 'Password1!'

const sharedState = {
  email: '',
  token: '',
}

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext()
  const email = `e2e-diary-settings-${Date.now()}@example.com`

  const regResp = await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })
  if (!regResp.ok()) throw new Error(`Register failed: ${regResp.status()} ${await regResp.text()}`)
  const { access_token: token } = await regResp.json() as { access_token: string }

  sharedState.email = email
  sharedState.token = token
  await ctx.dispose()
})

async function mockAuthAndGo(page: import('@playwright/test').Page, url: string) {
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

  await page.goto(url)
}

test.describe('Diary create → settings round-trip', () => {
  test('creates a diary via /diaries/new and lands on the diary detail', async ({ page }) => {
    await mockAuthAndGo(page, '/diaries/new')

    // Fill in the form
    await page.getByLabel('Name').fill("Emma's diary")
    await page.getByRole('button', { name: 'My child' }).click()
    await page.getByLabel(/their name/i).fill('Emma')
    await page.getByLabel(/tone/i).fill('warm, narrative')

    // Submit
    await page.getByRole('button', { name: 'Create diary' }).click()

    // Should land on the diary detail page
    await page.waitForURL(/\/diaries\/[^/]+$/, { timeout: 15_000 })
    await expect(page.getByRole('heading', { name: /Emma's diary/i })).toBeVisible()
  })

  test('settings page pre-fills form and saves changes', async ({ page }) => {
    // Create a diary first via API
    const ctx = await playwrightRequest.newContext()
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${sharedState.token}` }
    const diaryResp = await ctx.post(`${API}/v1/diaries`, {
      headers,
      data: {
        name: 'Settings Test Diary',
        timezone: 'UTC',
        subject_relation: 'child',
        subject_name: 'Leo',
        tone_hint: 'warm, narrative',
      },
    })
    if (!diaryResp.ok()) throw new Error(`Create diary failed: ${diaryResp.status()} ${await diaryResp.text()}`)
    const { id: diaryId } = await diaryResp.json() as { id: string }
    await ctx.dispose()

    await mockAuthAndGo(page, `/diaries/${diaryId}/settings`)
    await page.waitForURL(`**/diaries/${diaryId}/settings`, { timeout: 10_000 })

    // Form should be pre-filled
    await expect(page.getByLabel('Name')).toHaveValue('Settings Test Diary')
    await expect(page.getByLabel(/their name/i)).toHaveValue('Leo')
    await expect(page.getByLabel(/tone/i)).toHaveValue('warm, narrative')
    await expect(page.getByRole('button', { name: 'My child' })).toHaveClass(/btn-primary/)

    // Change the tone and save
    await page.getByLabel(/tone/i).fill('nostalgic and warm')
    await page.getByRole('button', { name: 'Save changes' }).click()

    // Should redirect back to diary detail
    await page.waitForURL(`**/diaries/${diaryId}`, { timeout: 10_000 })

    // Re-open settings and verify the change persisted
    await page.getByRole('link', { name: 'Settings' }).click()
    await page.waitForURL(`**/diaries/${diaryId}/settings`, { timeout: 10_000 })
    await expect(page.getByLabel(/tone/i)).toHaveValue('nostalgic and warm')
  })

  test('scan-enabled toggle is visible on settings page', async ({ page }) => {
    // Create a diary via API
    const ctx = await playwrightRequest.newContext()
    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${sharedState.token}` }
    const diaryResp = await ctx.post(`${API}/v1/diaries`, {
      headers,
      data: { name: 'Scan Toggle Test', timezone: 'UTC' },
    })
    if (!diaryResp.ok()) throw new Error(`Create diary failed: ${diaryResp.status()} ${await diaryResp.text()}`)
    const { id: diaryId } = await diaryResp.json() as { id: string }
    await ctx.dispose()

    await mockAuthAndGo(page, `/diaries/${diaryId}/settings`)
    await page.waitForURL(`**/diaries/${diaryId}/settings`, { timeout: 10_000 })

    await expect(page.getByRole('checkbox', { name: /scan enabled/i })).toBeVisible()
    await expect(page.getByRole('checkbox', { name: /scan enabled/i })).toBeChecked()
  })
})
