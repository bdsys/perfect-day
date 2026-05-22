import { test, expect } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const timestamp = Date.now()

test.describe('Phase 1 golden path', () => {
  const email = `smoke+${timestamp}@example.com`
  const password = 'Password1!'

  test('1. Register → land on /diaries', async ({ page }) => {
    await page.goto('/register')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries', { timeout: 10_000 })
    await expect(page).toHaveURL(/\/diaries$/)
  })

  test('2. Create a diary', async ({ page }) => {
    // Login first
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries', { timeout: 10_000 })

    await page.fill('#diary-name', 'My Test Diary')
    await page.click('button[type=submit]')

    // The diary card should appear
    await expect(page.locator('text=My Test Diary')).toBeVisible({ timeout: 5_000 })
  })

  test('3. Navigate to diary, mock Google OAuth connection visible', async ({ page }) => {
    // Mock the /v1/integrations/google/authorize endpoint so the redirect is captured
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

    // Click into the diary
    await page.click('text=My Test Diary')
    await page.waitForURL('**/diaries/**')

    // Connect Google Calendar button should be present
    await expect(page.locator('text=Connect Google Calendar')).toBeVisible()
  })

  test('4. Manually create entry — appears on timeline', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')
    await page.click('text=My Test Diary')
    await page.waitForURL('**/diaries/**')

    // Get diary ID from URL
    const url = page.url()
    const diaryId = url.split('/diaries/')[1].split('/')[0]

    // Create an entry via the API directly (simulates what scan would produce)
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

    // Reload timeline
    await page.reload()
    await expect(page.locator('text=E2E Test Entry')).toBeVisible({ timeout: 5_000 })
  })

  test('5. Open entry → edit body → Publish → status badge shows "published"', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')
    await page.click('text=My Test Diary')
    await page.waitForURL('**/diaries/**')

    // Click the entry card
    await page.click('text=E2E Test Entry')
    await page.waitForURL('**/entries/**')

    // Edit body
    await page.click('button:has-text("Edit")')
    await page.fill('#entry-body', 'Edited body content.')
    await page.click('button:has-text("Save")')
    await expect(page.locator('text=Edited body content.')).toBeVisible({ timeout: 5_000 })

    // Publish
    await page.click('button:has-text("Publish")')
    await expect(page.locator('.status-badge')).toContainText('published', { timeout: 5_000 })
  })
})
