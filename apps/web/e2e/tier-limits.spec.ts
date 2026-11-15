/**
 * E2E: Free plan limits (PoC item 18). The test stack has no LLM key, so a
 * generation always fails and releases its reservation; AI usage is therefore
 * seeded straight into the database, as in entry-location.spec.ts.
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { test, expect, request as playwrightRequest, type Page } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const REPO_ROOT = path.resolve(__dirname, '../../..')
const FIXTURE = path.join(__dirname, 'fixtures/sample.jpg')
const password = 'Password1!'

function sql(statement: string) {
  execFileSync(
    'docker',
    ['compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.test.yml', 'exec', '-T',
      'postgres', 'psql', '-U', 'perfectday', '-d', 'perfectday_test', '-v', 'ON_ERROR_STOP=1',
      '-c', statement],
    { cwd: REPO_ROOT, stdio: 'pipe' },
  )
}

type Free = { email: string; id: string; entryId: string }

/** A fresh Free user with one UTC diary and one manual entry. */
async function freeUser(prefix: string): Promise<Free> {
  const ctx = await playwrightRequest.newContext()
  const email = `e2e-${prefix}-${Date.now()}@example.com`
  const reg = await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })
  if (!reg.ok()) throw new Error(`Register failed: ${reg.status()} ${await reg.text()}`)
  const { access_token: token } = (await reg.json()) as { access_token: string }
  const headers = { Authorization: `Bearer ${token}` }
  const me = (await (await ctx.get(`${API}/v1/auth/me`, { headers })).json()) as { id: string }
  const diary = await ctx.post(`${API}/v1/diaries`, { headers, data: { name: 'Tier Diary', timezone: 'UTC' } })
  if (!diary.ok()) throw new Error(`Create diary failed: ${diary.status()} ${await diary.text()}`)
  const { id: diaryId } = (await diary.json()) as { id: string }
  const entry = await ctx.post(`${API}/v1/diaries/${diaryId}/entries`, {
    headers,
    data: { entry_date: '2026-05-16', title: 'A day out', body_markdown: 'We went out.' },
  })
  if (!entry.ok()) throw new Error(`Create entry failed: ${entry.status()} ${await entry.text()}`)
  const { id: entryId } = (await entry.json()) as { id: string }
  await ctx.dispose()
  return { email, id: me.id, entryId }
}

async function login(page: Page, email: string) {
  await page.goto('/login')
  await page.fill('#email', email)
  await page.fill('#password', password)
  await page.click('button[type=submit]')
  await page.waitForURL(/\/diaries/, { timeout: 10_000 })
}

async function openEntry(page: Page, user: Free) {
  await page.goto(`/entries/${user.entryId}`)
  await page.waitForURL(`**/entries/${user.entryId}`, { timeout: 10_000 })
}

test.describe('Free plan limits', () => {
  test('AI limit shows red and grays out the AI button', async ({ page }) => {
    const user = await freeUser('tier-ai')
    const month = new Date().toISOString().slice(0, 7)
    for (let i = 0; i < 5; i++) {
      sql(
        `INSERT INTO ai_draft_spends (id, user_id, entry_id, month, key, created_at) ` +
          `VALUES (gen_random_uuid(), '${user.id}', NULL, '${month}', 'e2e-' || gen_random_uuid(), now())`,
      )
    }
    await login(page, user.email)
    await openEntry(page, user)

    const badge = page.getByText(/AI drafts: 0 of 5 left/)
    await expect(badge).toBeVisible({ timeout: 10_000 })
    await expect(badge).toHaveAttribute('data-color', 'red')
    await expect(page.getByRole('button', { name: /(Regenerate|Generate|Polish) with AI/ })).toBeDisabled()
    await expect(page.getByText(/No AI drafts left this month\. Resets [A-Z][a-z]{2} \d{1,2}\./)).toBeVisible()

    await page.getByRole('link', { name: 'Upgrade now' }).first().click()
    await page.waitForURL('**/plans')
    await expect(page).toHaveURL(/\/plans$/)
    await expect(page.getByText('Paid plans are coming soon.')).toBeVisible()
  })

  test('second photo is blocked', async ({ page }) => {
    const user = await freeUser('tier-photo')
    await login(page, user.email)
    await openEntry(page, user)

    await page.getByRole('button', { name: /add photos/i }).click()
    await page.locator('input[type="file"]').first().setInputFiles(FIXTURE)
    await expect(page.locator('ul.photo-grid li')).toHaveCount(1, { timeout: 30_000 })

    await expect(page.getByRole('button', { name: /add photos/i })).toBeDisabled({ timeout: 10_000 })
    await expect(page.getByText('Free plan: 1 photo per entry.')).toBeVisible()
  })

  test('Plus shows no badge', async ({ page }) => {
    const user = await freeUser('tier-plus')
    await login(page, user.email)
    await openEntry(page, user)
    await expect(page.getByText(/AI drafts: \d of 5 left/)).toBeVisible({ timeout: 10_000 })

    sql(`UPDATE users SET subscription_tier = 'plus' WHERE id = '${user.id}'`)
    await page.reload()
    await expect(page.getByRole('button', { name: /(Regenerate|Generate|Polish) with AI/ })).toBeVisible()
    await expect(page.getByText(/AI drafts:/)).toHaveCount(0)
  })
})
