/**
 * E2E: the AI suggestion box on the entry page (follow-up 25.3).
 *
 * When an entry already has text, the AI's new version waits in a box under
 * the text, with Accept and Dismiss. The suggestion is seeded straight into
 * the test database: the e2e worker has real model keys, so clicking
 * Regenerate here would bill every run and give different text each time.
 * Regenerate producing a suggestion is covered by the API integration tests
 * (apps/api/tests/integration/test_suggestions.py).
 */

import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { test, expect, request as playwrightRequest, type Page } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const REPO_ROOT = path.resolve(__dirname, '../../..')
const password = 'Password1!'

const OWN_TEXT = 'Sam and Grandma had a picnic by the pond.'
const SUGGESTED_TITLE = 'Picnic at Riverside Park'
const SUGGESTED_BODY = 'Sam and Grandma spent the afternoon at Riverside Park. Uncle Ben stopped by.'

const shared = { email: '', token: '', diaryId: '' }

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext()
  const email = `e2e-suggestion-${Date.now()}@example.com`
  const reg = await ctx.post(`${API}/v1/auth/register`, { data: { email, password } })
  if (!reg.ok()) throw new Error(`Register failed: ${reg.status()} ${await reg.text()}`)
  const { access_token: token } = (await reg.json()) as { access_token: string }
  const diary = await ctx.post(`${API}/v1/diaries`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name: 'Suggestion Box Diary', timezone: 'UTC' },
  })
  if (!diary.ok()) throw new Error(`Create diary failed: ${diary.status()} ${await diary.text()}`)
  shared.email = email
  shared.token = token
  shared.diaryId = ((await diary.json()) as { id: string }).id
  await ctx.dispose()
})

/** An entry with the parent's own text and a pending AI suggestion. */
async function entryWithSuggestion(): Promise<string> {
  const ctx = await playwrightRequest.newContext()
  const r = await ctx.post(`${API}/v1/diaries/${shared.diaryId}/entries`, {
    headers: { Authorization: `Bearer ${shared.token}` },
    data: { entry_date: '2026-05-16', title: 'Picnic with Grandma', body_markdown: OWN_TEXT },
  })
  if (!r.ok()) throw new Error(`Create entry failed: ${r.status()} ${await r.text()}`)
  const { id } = (await r.json()) as { id: string }
  await ctx.dispose()

  const sql =
    `UPDATE entries SET suggested_title = '${SUGGESTED_TITLE}', ` +
    `suggested_body_markdown = '${SUGGESTED_BODY}', ` +
    `suggested_flagged_tokens = ARRAY['Uncle','Ben'], suggested_body_source = 'llm_hybrid', ` +
    `suggested_at = now() WHERE id = '${id}'`
  execFileSync(
    'docker',
    ['compose', '-f', 'docker-compose.yml', '-f', 'docker-compose.test.yml', 'exec', '-T',
      'postgres', 'psql', '-U', 'perfectday', '-d', 'perfectday_test', '-v', 'ON_ERROR_STOP=1',
      '-c', sql],
    { cwd: REPO_ROOT, stdio: 'pipe' },
  )
  return id
}

async function openEntry(page: Page, entryId: string) {
  await page.route(`${API}/v1/auth/refresh`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: shared.token }),
    }),
  )
  await page.route(`${API}/v1/auth/me`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'test-user', email: shared.email, display_name: null }),
    }),
  )
  await page.goto(`/entries/${entryId}`)
  await page.waitForURL(`**/entries/${entryId}`, { timeout: 10_000 })
}

test.describe('AI suggestion box', () => {
  test('shows the suggestion with its own warning, lets you select it, and Accept replaces the text', async ({ page }) => {
    const entryId = await entryWithSuggestion()
    await openEntry(page, entryId)

    const box = page.getByRole('region', { name: 'The AI suggests a new version' })
    await expect(box).toBeVisible()
    await expect(box).toContainText('Uncle')
    await expect(page.getByTestId('entry-body')).toHaveText(OWN_TEXT)

    const suggestedBody = box.getByTestId('suggestion-body')
    await suggestedBody.selectText()
    const selected = await page.evaluate(() => window.getSelection()?.toString() ?? '')
    expect(selected).toBe(SUGGESTED_BODY)

    await box.getByRole('button', { name: 'Accept' }).click()

    await expect(box).toBeHidden()
    await expect(page.getByTestId('entry-body')).toHaveText(SUGGESTED_BODY)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(SUGGESTED_TITLE)
    await expect(page.getByText('Verify before publishing')).toBeVisible()
  })

  test('Dismiss hides the suggestion and leaves the text alone', async ({ page }) => {
    const entryId = await entryWithSuggestion()
    await openEntry(page, entryId)

    const box = page.getByRole('region', { name: 'The AI suggests a new version' })
    await box.getByRole('button', { name: 'Dismiss' }).click()

    await expect(box).toBeHidden()
    await expect(page.getByTestId('entry-body')).toHaveText(OWN_TEXT)

    await page.reload()
    await expect(page.getByTestId('entry-body')).toHaveText(OWN_TEXT)
    await expect(page.getByRole('region', { name: 'The AI suggests a new version' })).toHaveCount(0)
  })

  test('on a published entry, Regenerate looks switched off and says why', async ({ page }) => {
    const ctx = await playwrightRequest.newContext()
    const headers = { Authorization: `Bearer ${shared.token}` }
    const r = await ctx.post(`${API}/v1/diaries/${shared.diaryId}/entries`, {
      headers,
      data: { entry_date: '2026-05-16', title: 'Published day', body_markdown: OWN_TEXT },
    })
    const { id } = (await r.json()) as { id: string }
    await ctx.post(`${API}/v1/entries/${id}/publish`, { headers })
    await ctx.dispose()
    await openEntry(page, id)

    const regenerate = page.getByRole('button', { name: /Regenerate|Polish|Generate/ })
    await expect(regenerate).toBeDisabled()
    await expect(regenerate).toHaveCSS('cursor', 'not-allowed')
    expect(Number(await regenerate.evaluate((el) => getComputedStyle(el).opacity))).toBeLessThan(1)
    await expect(page.getByText('Unpublish to ask the AI for a new version.')).toBeVisible()
  })

  test('the box is hidden while editing', async ({ page }) => {
    const entryId = await entryWithSuggestion()
    await openEntry(page, entryId)

    await page.getByRole('button', { name: 'Edit' }).click()

    await expect(page.getByRole('region', { name: 'The AI suggests a new version' })).toHaveCount(0)
  })
})
