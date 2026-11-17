import { test, expect, request as playwrightRequest, type Page } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const password = 'Password1!'

type Account = { email: string; token: string }

async function register(tag: string): Promise<Account> {
  const ctx = await playwrightRequest.newContext()
  const email = `e2e-profile-${tag}-${Date.now()}@example.com`
  const res = await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })
  if (!res.ok()) throw new Error(`Register failed: ${res.status()} ${await res.text()}`)
  const { access_token: token } = (await res.json()) as { access_token: string }
  await ctx.dispose()
  return { email, token }
}

async function apiPost<T>(token: string, path: string, data: unknown): Promise<T> {
  const ctx = await playwrightRequest.newContext()
  const res = await ctx.post(`${API}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
    data,
  })
  if (!res.ok()) throw new Error(`POST ${path} failed: ${res.status()} ${await res.text()}`)
  const body = (await res.json()) as T
  await ctx.dispose()
  return body
}

async function loginViaUI(page: Page, email: string) {
  await page.goto('/login')
  await page.fill('#email', email)
  await page.fill('#password', password)
  await page.click('button[type=submit]')
}

test.describe('Profile screen', () => {
  test('picking an icon shows it in the header on another page', async ({ page }) => {
    const { email } = await register('icon')
    await loginViaUI(page, email)
    await page.waitForURL(/\/diaries/, { timeout: 10_000 })

    await page.goto('/profile')
    const panda = page.getByRole('button', { name: 'panda', exact: true })
    await panda.click()
    await expect(panda).toHaveAttribute('aria-pressed', 'true')

    await page.goto('/diaries')
    const avatar = page.getByRole('button', { name: 'Account menu' }).locator('[data-icon]')
    await expect(avatar).toHaveAttribute('data-icon', 'panda')
  })

  test('switching to °C shows °C on an entry badge', async ({ page }) => {
    const { email, token } = await register('unit')
    const diary = await apiPost<{ id: string }>(token, '/v1/diaries', {
      name: 'Unit Diary',
      timezone: 'UTC',
    })
    const entry = await apiPost<{ id: string }>(token, `/v1/diaries/${diary.id}/entries`, {
      entry_date: new Date().toISOString().slice(0, 10),
      title: 'Unit entry',
      body_markdown: 'A sunny day.',
    })

    await loginViaUI(page, email)
    await page.waitForURL(/\/diaries/, { timeout: 10_000 })

    await page.goto('/profile')
    await page.getByLabel('° C').click()
    await expect(page.getByLabel('° C')).toBeChecked()

    // Enrichments are written only by background workers, so add one to the
    // real response (which keeps the saved temperature_unit).
    await page.route(`${API}/v1/entries/${entry.id}`, async (route) => {
      if (route.request().method() !== 'GET') return route.continue()
      const response = await route.fetch()
      const json = (await response.json()) as Record<string, unknown>
      json.enrichments = [
        ...((json.enrichments as unknown[]) ?? []),
        { kind: 'weather', payload: { temperature_min_c: 16, temperature_max_c: 21 } },
      ]
      await route.fulfill({ response, json })
    })

    await page.goto(`/entries/${entry.id}`)
    const badge = page.getByLabel('Weather')
    await expect(badge).toContainText('16°C')
    await expect(badge).toContainText('21°C')
  })

  test('delete, log in, restore, diaries are back', async ({ page }) => {
    const { email, token } = await register('delete')
    await apiPost(token, '/v1/diaries', { name: 'Restore Me Diary', timezone: 'UTC' })

    await loginViaUI(page, email)
    await page.waitForURL(/\/diaries/, { timeout: 10_000 })

    await page.goto('/profile')
    await page.getByRole('button', { name: 'Delete account…' }).click()
    await page.getByLabel('Type DELETE to confirm').fill('DELETE')
    await page.getByRole('button', { name: 'Delete my account' }).click()

    await page.waitForURL(/\/login\?deleted=/, { timeout: 10_000 })
    await expect(page.getByText(/Your account will be deleted on/)).toBeVisible()
    await expect(page.getByText(/Log in before then to restore it/)).toBeVisible()

    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')

    await expect(page.getByText(/Your account will be deleted on/)).toBeVisible({ timeout: 10_000 })
    await page.getByRole('button', { name: 'Restore my account' }).click()

    await page.goto('/diaries')
    await expect(page.getByText('Restore Me Diary')).toBeVisible({ timeout: 10_000 })
  })
})
