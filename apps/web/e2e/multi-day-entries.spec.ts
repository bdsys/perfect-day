import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

const email = 'e2e-multi-day@example.com'
const password = 'Password1!'

test.describe('Multi-day entry date range', () => {
  let diaryId = ''
  let entryId = ''

  test.beforeAll(async () => {
    const ctx = await playwrightRequest.newContext()

    // Register or accept conflict (user may already exist from previous run).
    await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })

    const loginRes = await ctx.post(`${API}/v1/auth/login`, {
      data: { email, password },
    })
    const { access_token } = await loginRes.json()
    const auth = { Authorization: `Bearer ${access_token}` }

    // Create diary — reuse existing one if tier limit is hit on re-runs.
    const diaryRes = await ctx.post(`${API}/v1/diaries`, {
      headers: auth,
      data: { name: 'Multi-Day Diary', timezone: 'UTC' },
    })
    const diaryBody = await diaryRes.json()
    if (diaryBody.id) {
      diaryId = diaryBody.id
    } else {
      // Tier limit hit — find the existing diary by name.
      const listRes = await ctx.get(`${API}/v1/diaries`, { headers: auth })
      const diaries = await listRes.json()
      const existing = diaries.find((d: { id: string; name: string }) => d.name === 'Multi-Day Diary')
      diaryId = existing?.id ?? diaries[0]?.id ?? ''
    }

    // Create entry — reuse existing multi-day entry if tier limit hit on re-runs.
    const entryRes = await ctx.post(`${API}/v1/diaries/${diaryId}/entries`, {
      headers: auth,
      data: { entry_date: '2026-06-01', entry_end_date: '2026-06-03' },
    })
    const entryBody = await entryRes.json()
    if (entryBody.id) {
      entryId = entryBody.id
    } else {
      // Tier limit hit — find the existing multi-day entry.
      const listRes = await ctx.get(`${API}/v1/diaries/${diaryId}/entries`, { headers: auth })
      const entries = await listRes.json()
      const existing = entries.find(
        (e: { id: string; entry_date: string; entry_end_date: string | null }) =>
          e.entry_date === '2026-06-01' && e.entry_end_date === '2026-06-03',
      )
      entryId = existing?.id ?? entries[0]?.id ?? ''
    }

    await ctx.dispose()
  })

  test('diary timeline shows date range', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')

    await page.goto(`/diaries/${diaryId}`)

    const card = page.locator('.entry-card').first()
    await expect(card).toBeVisible()
    await expect(card).toContainText('2026')
    // En-dash separator between start and end.
    await expect(card).toContainText('–')
  })

  test('entry detail page shows date range in header', async ({ page }) => {
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries')

    await page.goto(`/entries/${entryId}`)

    await expect(page.locator('text=–').first()).toBeVisible()
    await expect(page.getByText(/2026/).first()).toBeVisible()
  })
})
