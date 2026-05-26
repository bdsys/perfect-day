import { test, expect } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const password = 'Password1!'

test.describe('Restore tier-limit upgrade CTA', () => {
  test('shows upgrade CTA when restoring a diary at tier limit', async ({ page, request }) => {
    // Unique email per run avoids accumulated state across test runs
    const email = `e2e-restore-tier-${Date.now()}@example.com`

    // 1. Register the user and seed diaries via the request fixture (separate HTTP
    //    client with its own cookie jar). Use the JWT token for all API calls so
    //    the session identity is unambiguous.
    const regResp = await request.post(`${API}/v1/auth/register`, {
      data: { email, password },
    })
    if (!regResp.ok()) throw new Error(`Register failed: ${regResp.status()} ${await regResp.text()}`)
    const { access_token: token } = await regResp.json() as { access_token: string }

    const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }

    // 2. Seed: create Diary A, soft-delete it, then create Diary B
    //    (free-tier cap = 1 active diary).
    const diaryAResp = await request.post(`${API}/v1/diaries`, {
      headers,
      data: { name: 'Diary A', timezone: 'UTC' },
    })
    if (!diaryAResp.ok()) throw new Error(`Create Diary A failed: ${diaryAResp.status()} ${await diaryAResp.text()}`)
    const diaryAJson = await diaryAResp.json() as { id: string; owner_user_id?: string }
    const { id: diaryAId } = diaryAJson
    console.log('CREATE_DIARY_A', JSON.stringify(diaryAJson))

    const delResp = await request.delete(`${API}/v1/diaries/${diaryAId}`, { headers })
    const delText = await delResp.text()
    if (!delResp.ok()) throw new Error(`Delete Diary A failed: ${delResp.status()} id=${diaryAId} body=${delText}`)

    const diaryBResp = await request.post(`${API}/v1/diaries`, {
      headers,
      data: { name: 'Diary B', timezone: 'UTC' },
    })
    if (!diaryBResp.ok()) throw new Error(`Create Diary B failed: ${diaryBResp.status()} ${await diaryBResp.text()}`)

    // 3. Log in via the UI form — sets both the httpOnly refresh cookie and the
    //    in-memory access token used by api.ts. The fresh email ensures this user
    //    only consumes 1 auth rate-limit slot.
    await page.goto('/login')
    await page.fill('#email', email)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL('**/diaries', { timeout: 10_000 })

    // Wait for the diaries list to finish loading before navigating away
    await expect(page.locator('text=Diary B')).toBeVisible({ timeout: 5_000 })

    // 4. Navigate via the client-side Link — preserves the in-memory access token
    //    (a full page.goto would discard it, forcing a SameSite=strict cookie refresh
    //    that is unreliable in the cross-origin Playwright environment).
    await page.getByRole('link', { name: 'Deleted diaries' }).click()
    await page.waitForURL('**/diaries/restore')
    await expect(page.locator('text=Diary A')).toBeVisible({ timeout: 5_000 })

    // 5. Click Restore — tier limit should block it
    await page.getByRole('button', { name: 'Restore' }).first().click()

    // 6. Upgrade CTA banner must appear
    await expect(page.locator('text=free-tier')).toBeVisible({ timeout: 5_000 })
    await expect(page.locator('a[href="/account/upgrade"]')).toBeVisible({ timeout: 5_000 })
  })
})
