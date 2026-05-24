import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

// Fixed credentials — beforeAll ensures the user exists before each test.
const email = 'e2e-golden-path@example.com'
const password = 'Password1!'

test.describe('Phase 1 golden path', () => {
  test.beforeAll(async () => {
    // Ensure the smoke user exists. Register returns 201 on first run,
    // 409/422 on subsequent runs — both are acceptable here.
    const ctx = await playwrightRequest.newContext()
    await ctx.post(`${API}/v1/auth/register`, {
      data: { email, password },
    })
    await ctx.dispose()
  })

  test('1. Register → land on /diaries', async ({ page }) => {
    // For this test we use the login flow (user already exists from beforeAll).
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries', { timeout: 10_000 })
    await expect(page).toHaveURL(/\/diaries$/)
  })

  test('2. Create a diary', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries', { timeout: 10_000 })

    // Only create if not already present (re-runnable)
    const existing = page.locator('text=My Test Diary')
    if (!(await existing.isVisible({ timeout: 500 }).catch(() => false))) {
      await page.fill('#diary-name', 'My Test Diary')
      await page.click('button[type=submit]')
    }

    await expect(page.locator('text=My Test Diary')).toBeVisible({ timeout: 5_000 })
  })

  test('3. Navigate to diary, mock Google OAuth connection visible', async ({ page }) => {
    await page.route(`${API}/v1/integrations/google/authorize**`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ url: 'http://localhost:3000/diaries?google=connected' }),
      })
    })

    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')

    await page.click('text=My Test Diary')
    await page.waitForURL('**/diaries/**')

    // Connect Google Calendar button should be present
    await expect(page.getByRole('button', { name: 'Connect Google Calendar' })).toBeVisible()
  })

  test('4. Manually create entry — appears on timeline', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')
    await page.click('text=My Test Diary')
    await page.waitForURL('**/diaries/**')

    const url = page.url()
    const diaryId = url.split('/diaries/')[1].split('/')[0]

    // Create an entry via the API directly
    const loginResp = await page.evaluate(
      async ({ api, email, password }) => {
        const r = await fetch(`${api}/v1/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password }),
          credentials: 'include',
        })
        return r.json()
      },
      { api: API, email, password }
    )

    await page.evaluate(
      async ({ api, token, diaryId }) => {
        await fetch(`${api}/v1/diaries/${diaryId}/entries`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            entry_date: new Date().toISOString().slice(0, 10),
            title: 'E2E Test Entry',
            body_markdown: 'Something happened today.',
          }),
        })
      },
      { api: API, token: loginResp.access_token, diaryId }
    )

    await page.reload()
    await expect(page.locator('text=E2E Test Entry').first()).toBeVisible({ timeout: 5_000 })
  })

  test('5. Open entry → edit body → Publish → status badge shows "published"', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')
    await page.click('text=My Test Diary')
    await page.waitForURL('**/diaries/**')

    await page.locator('text=E2E Test Entry').first().click()
    await page.waitForURL('**/entries/**')

    await page.click('button:has-text("Edit")')
    await page.fill('#entry-body', 'Edited body content.')
    await page.click('button:has-text("Save")')
    await expect(page.locator('text=Edited body content.')).toBeVisible({ timeout: 5_000 })

    await page.click('button:has-text("Publish")')
    await expect(page.locator('.status-badge')).toContainText('published', { timeout: 5_000 })
  })
})
