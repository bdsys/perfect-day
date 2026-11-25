/**
 * E2E: wish lists (phone check 1). The owner makes a list 10 days ahead with two
 * items and publishes it (giving a display name when asked); a viewer sees it under
 * Coming up and claims item 1; a visitor with no sign-in opens the public link, sees
 * item 1 Taken and claims item 2; the owner sees both claims, turns the link off and
 * the visitor sees the link is dead.
 *
 * The link path is read from the API so nothing touches the clipboard.
 */

import { test, expect, request as playwrightRequest } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const password = 'Password1!'

test.describe('Wish lists', () => {
  test('publish, claim signed in, claim by link, turn the link off', async ({ browser, page }) => {
    const stamp = Date.now()
    const ownerEmail = `e2e-wish-owner-${stamp}@example.com`
    const viewerEmail = `e2e-wish-viewer-${stamp}@example.com`
    const viewerName = 'Grandma Jo'
    const listTitle = `Birthday ${stamp}`
    const listDate = new Date(Date.now() + 10 * 86_400_000).toISOString().slice(0, 10) // diary timezone is UTC

    // Owner (no display name yet), diary, viewer account and invitation, through the API.
    const ctx = await playwrightRequest.newContext()
    const reg = await ctx.post(`${API}/v1/auth/register`, { data: { email: ownerEmail, password } })
    if (!reg.ok()) throw new Error(`Register failed: ${reg.status()} ${await reg.text()}`)
    const headers = { Authorization: `Bearer ${((await reg.json()) as { access_token: string }).access_token}` }
    const diary = await ctx.post(`${API}/v1/diaries`, { headers, data: { name: 'Family Diary', timezone: 'UTC' } })
    if (!diary.ok()) throw new Error(`Create diary failed: ${diary.status()} ${await diary.text()}`)
    const { id: diaryId } = (await diary.json()) as { id: string }

    const inv = await ctx.post(`${API}/v1/diaries/${diaryId}/invitations`, {
      headers,
      data: { email: viewerEmail, role: 'viewer' },
    })
    if (!inv.ok()) throw new Error(`Invite failed: ${inv.status()} ${await inv.text()}`)
    const { token: inviteToken } = (await inv.json()) as { token: string }
    const vreg = await ctx.post(`${API}/v1/auth/register`, {
      data: { email: viewerEmail, password, display_name: viewerName },
    })
    if (!vreg.ok()) throw new Error(`Viewer register failed: ${vreg.status()} ${await vreg.text()}`)
    const vheaders = { Authorization: `Bearer ${((await vreg.json()) as { access_token: string }).access_token}` }
    const acc = await ctx.post(`${API}/v1/invitations/${inviteToken}/accept`, { headers: vheaders })
    if (!acc.ok()) throw new Error(`Accept failed: ${acc.status()} ${await acc.text()}`)

    // 1. Owner: new list, two items, publish (asked for a name).
    await page.goto('/login')
    await page.fill('#email', ownerEmail)
    await page.fill('#password', password)
    await page.click('button[type=submit]')
    await page.waitForURL(/\/diaries/, { timeout: 10_000 })
    await page.goto(`/diaries/${diaryId}`)
    await page.getByRole('link', { name: 'Wish lists' }).click()
    await page.waitForURL(`**/diaries/${diaryId}/wish-lists`)
    await page.getByRole('button', { name: 'New wish list' }).click()
    await page.getByLabel('Title').fill(listTitle)
    await page.getByLabel('Date').fill(listDate)
    await page.getByRole('button', { name: 'Make list' }).click()
    await page.waitForURL(/\/wish-lists\/[0-9a-f-]{36}$/, { timeout: 10_000 })
    const listId = page.url().split('/').pop() as string
    await expect(page.getByRole('heading', { name: listTitle })).toBeVisible()

    const addItem = page.getByRole('form', { name: 'Add item' })
    for (const name of ['Soft blanket', 'Picture book']) {
      await addItem.getByLabel('Item').fill(name)
      await addItem.getByRole('button', { name: 'Add item' }).click()
      await expect(page.getByRole('listitem').filter({ hasText: name }).first()).toBeVisible({ timeout: 10_000 })
    }

    await page.getByRole('button', { name: 'Publish', exact: true }).click()
    await page.getByLabel('Your name, as people with the link will see it').fill('Andrew')
    await page.getByRole('button', { name: 'Save and publish' }).click()
    await expect(page.getByRole('button', { name: 'Turn link off' })).toBeVisible({ timeout: 10_000 })

    // Link path from the API (no clipboard).
    const got = await playwrightRequest.newContext()
    const detail = await got.get(`${API}/v1/wish-lists/${listId}`, { headers })
    const linkPath = ((await detail.json()) as { link: { path: string } }).link.path
    expect(linkPath).toMatch(/^\/p\/.+/)

    // 2. Viewer: sees it under Coming up, claims item 1.
    const viewerCtx = await browser.newContext()
    const viewer = await viewerCtx.newPage()
    await viewer.goto('/login')
    await viewer.fill('#email', viewerEmail)
    await viewer.fill('#password', password)
    await viewer.click('button[type=submit]')
    await viewer.waitForURL(/\/diaries/, { timeout: 10_000 })
    await viewer.goto(`/diaries/${diaryId}`)
    const comingUp = viewer.getByRole('region', { name: 'Coming up' })
    await expect(comingUp).toBeVisible({ timeout: 10_000 })
    await comingUp.getByRole('link', { name: new RegExp(listTitle) }).click()
    await viewer.waitForURL(`**/wish-lists/${listId}`)
    const vItem1 = viewer.getByRole('listitem').filter({ hasText: 'Soft blanket' })
    await vItem1.getByRole('button', { name: "I'm getting this" }).click()
    await expect(vItem1.getByText("You're getting this")).toBeVisible({ timeout: 10_000 })

    // 3. A fresh context, no sign-in, opens the link.
    const strangerCtx = await browser.newContext()
    const stranger = await strangerCtx.newPage()
    const resp = await stranger.goto(linkPath)
    const h = resp!.headers()
    expect(h['x-robots-tag']).toBe('noindex, nofollow')
    expect(h['referrer-policy']).toBe('no-referrer')
    expect(h['cache-control']).toContain('no-store')
    await expect(stranger.getByRole('heading', { name: listTitle })).toBeVisible()
    await expect(stranger.getByRole('listitem').filter({ hasText: 'Soft blanket' }).getByText('Taken')).toBeVisible()
    const sItem2 = stranger.getByRole('listitem').filter({ hasText: 'Picture book' })
    await sItem2.getByRole('button', { name: "I'm getting this" }).click()
    await stranger.getByLabel('Your name').fill("Sam's mum")
    await stranger.getByRole('button', { name: 'Save' }).click()
    await expect(sItem2.getByText("You're getting this")).toBeVisible({ timeout: 10_000 })

    // 4. Owner sees both claims with names.
    await page.reload()
    await expect(
      page.getByRole('listitem').filter({ hasText: 'Soft blanket' }).getByText(viewerName, { exact: true }),
    ).toBeVisible({ timeout: 10_000 })
    await expect(
      page.getByRole('listitem').filter({ hasText: 'Picture book' }).getByText("Sam's mum", { exact: true }),
    ).toBeVisible()

    // 5. Owner turns the link off; the visitor sees it is dead.
    await page.getByRole('button', { name: 'Turn link off' }).click()
    await expect(page.getByText('The link is off.')).toBeVisible({ timeout: 10_000 })
    await stranger.reload()
    await expect(stranger.getByText('This link is no longer active')).toBeVisible({ timeout: 10_000 })

    await viewerCtx.close()
    await strangerCtx.close()
    await got.dispose()
    await ctx.dispose()
  })
})
