/**
 * E2E: diary sharing (PoC item 19). The owner makes an invite link on the
 * Sharing screen; a second person opens it signed out, creates an account,
 * lands back on the invite and reads the diary (published entries only).
 *
 * The invite link is read from the page: the Sharing screen renders it in a
 * read-only text input labelled "Invite link", so nothing touches the clipboard.
 */

import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const password = 'Password1!'

test.describe('Diary sharing', () => {
  test('invite, accept and read a shared diary', async ({ browser, page }) => {
    const stamp = Date.now()
    const ownerEmail = `e2e-share-owner-${stamp}@example.com`
    const momEmail = `e2e-share-mom-${stamp}@example.com`

    // Owner, diary, one published entry and one draft, all through the API.
    const ctx = await playwrightRequest.newContext()
    const reg = await ctx.post(`${API}/v1/auth/register`, {
      data: { email: ownerEmail, password, display_name: 'Andrew' },
    })
    if (!reg.ok()) throw new Error(`Register failed: ${reg.status()} ${await reg.text()}`)
    const { access_token: token } = (await reg.json()) as { access_token: string }
    const headers = { Authorization: `Bearer ${token}` }
    const diary = await ctx.post(`${API}/v1/diaries`, { headers, data: { name: 'Family Diary', timezone: 'UTC' } })
    if (!diary.ok()) throw new Error(`Create diary failed: ${diary.status()} ${await diary.text()}`)
    const { id: diaryId } = (await diary.json()) as { id: string }

    async function makeEntry(title: string, entry_date: string): Promise<string> {
      const r = await ctx.post(`${API}/v1/diaries/${diaryId}/entries`, {
        headers,
        data: { entry_date, title, body_markdown: `${title} body.` },
      })
      if (!r.ok()) throw new Error(`Create entry failed: ${r.status()} ${await r.text()}`)
      return ((await r.json()) as { id: string }).id
    }
    const parkId = await makeEntry('Park day', '2026-05-16')
    await makeEntry('Secret draft', '2026-05-17')
    const pub = await ctx.post(`${API}/v1/entries/${parkId}/publish`, { headers })
    if (!pub.ok()) throw new Error(`Publish failed: ${pub.status()} ${await pub.text()}`)
    await ctx.dispose()

    // Owner: diary -> Share -> Make invite link.
    await page.goto('/login')
    await page.fill('#email', ownerEmail)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL(/\/diaries/, { timeout: 10_000 })
    await page.goto(`/diaries/${diaryId}`)
    await page.getByRole('link', { name: 'Share', exact: true }).click()
    await page.waitForURL(`**/diaries/${diaryId}/sharing`)
    await page.getByLabel('Email').fill(momEmail)
    await page.getByRole('button', { name: 'Make invite link' }).click()
    await expect(page.getByText(`Invite link for ${momEmail}. It works once, for 7 days.`)).toBeVisible({
      timeout: 10_000,
    })
    const inviteLink = await page.getByLabel('Invite link').inputValue()
    expect(inviteLink).toMatch(/\/invite\/[^/]+$/)

    // Mom, in her own browser context, signed out.
    const momCtx = await browser.newContext()
    const mom = await momCtx.newPage()
    await mom.goto(inviteLink)
    await expect(
      mom.getByText("You've been invited to a diary on Perfect Day. Sign in or create an account to see it."),
    ).toBeVisible({ timeout: 10_000 })
    await mom.getByRole('link', { name: 'Create an account' }).click()
    await mom.waitForURL(/\/register\?next=/)
    await mom.fill('#email', momEmail)
    await mom.fill('#password', password)
    await mom.getByRole('button', { name: 'Create account' }).click()
    // Matches only because register honours next (URI-encoded /invite/<token>) and returns here.
    await mom.waitForURL(/\/invite\//, { timeout: 10_000 })
    await mom.getByRole('button', { name: 'Open diary' }).click()
    await mom.waitForURL(`**/diaries/${diaryId}`, { timeout: 10_000 })

    // She sees the published entry, not the draft, and cannot scan.
    await expect(mom.getByText('Park day').first()).toBeVisible({ timeout: 10_000 })
    await expect(mom.getByText('Secret draft')).toHaveCount(0)
    await expect(mom.getByRole('button', { name: 'Scan now' })).toHaveCount(0)

    // Her list says whose diary it is.
    await mom.goto('/diaries')
    await expect(mom.getByText('Shared by Andrew')).toBeVisible({ timeout: 10_000 })
    await momCtx.close()

    // Back as the owner: the counter.
    await page.goto(`/diaries/${diaryId}/sharing`)
    await expect(page.getByText('Shared with 1 of 3 people')).toBeVisible({ timeout: 10_000 })
  })
})
