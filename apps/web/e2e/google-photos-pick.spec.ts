/**
 * E2E: add photos from Google Photos (item 14). All API calls are mocked.
 *
 * The new tab is stubbed with `page.addInitScript` (window.open returns a fake
 * tab that records where it was sent) rather than waiting for a real popup,
 * so the test can check the exact URL without a second page.
 */

import { test, expect, type Page } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const ENTRY_ID = '00000000-0000-4000-8000-000000000014'
const IMPORT_ID = '00000000-0000-4000-8000-0000000000aa'
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

const json = (body: unknown, status = 200) => ({
  status, contentType: 'application/json', body: JSON.stringify(body),
})

function entry(photoIds: string[]) {
  return {
    id: ENTRY_ID, diary_id: '00000000-0000-4000-8000-000000000001',
    entry_date: '2026-05-16', entry_end_date: null, title: 'Park day', body_markdown: 'We went to the park.',
    body_source: 'user', flagged_tokens: null, status: 'draft', created_by: 'manual',
    creation_source: 'manual', published_at: null, deleted_at: null,
    created_at: '2026-11-12T10:00:00Z', updated_at: '2026-11-12T10:00:00Z',
    events: [], rule_matches: [], last_generation: null, enrichments: [],
    suggestion: null, location: null, tz_override: null, can_edit: true,
    photos: photoIds.map((id) => ({
      id, mime_type: 'image/png', bytes: 70, taken_at: null, lat: null, lon: null, source: 'google_photos',
      finalized_at: '2026-11-12T10:01:00Z', created_at: '2026-11-12T10:01:00Z', deleted_at: null, has_thumbnail: true,
    })),
  }
}

async function mockCommon(page: Page) {
  await page.route(`${API}/v1/auth/refresh`, (r) => r.fulfill(json({ access_token: 't' })))
  await page.route(`${API}/v1/auth/me`, (r) => r.fulfill(json({ id: 'u', email: 'p@example.com', display_name: null })))
  await page.route(/\/v1\/photos(\/|\?|$)/, (r) =>
    r.request().url().includes('?near=') || r.request().url().endsWith('/v1/photos')
      ? r.fulfill(json([]))
      : r.fulfill({ status: 200, contentType: 'image/png', body: PNG }),
  )
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as { __tab?: { location: { href: string }; close: () => void }; __opened: number }
    w.__opened = 0
    window.open = () => {
      w.__opened += 1
      w.__tab = { location: { href: '' }, close() {} }
      return w.__tab as unknown as Window
    }
  })
})

test('pick, wait, see the notice and photos, dismiss, and it stays gone after reload', async ({ page }) => {
  await mockCommon(page)
  await page.route(`${API}/v1/integrations`, (r) =>
    r.fulfill(json([{ provider: 'google', scopes_granted: [], revoked: false, expires_at: null,
      google_email: 'a@b.com', google_name: null, photos: true }])),
  )

  let pickDone = false
  let dismissed = false
  let started = false
  let listGets = 0
  await page.route(`${API}/v1/entries/${ENTRY_ID}`, (r) =>
    r.fulfill(json(entry(pickDone ? ['p1', 'p2'] : []))),
  )
  await page.route(`${API}/v1/entries/${ENTRY_ID}/photo-imports`, (r) => {
    if (r.request().method() === 'POST') {
      started = true
      return r.fulfill(json({ id: IMPORT_ID, picker_uri: 'https://photos.google.com/picker/abc', status: 'waiting' }, 201))
    }
    // The page polls this list. After the start it reports waiting, then copying, then done.
    if (started) {
      listGets += 1
      if (listGets >= 3) pickDone = true
    }
    const status = pickDone ? 'done' : listGets >= 2 ? 'copying' : 'waiting'
    return r.fulfill(json(
      started && !dismissed
        ? [{ id: IMPORT_ID, entry_id: ENTRY_ID, status,
             added_count: pickDone ? 2 : 0, skipped_videos: pickDone ? 1 : 0, failed_names: [] }]
        : [],
    ))
  })
  await page.route(`${API}/v1/photo-imports/${IMPORT_ID}/dismiss`, (r) => {
    dismissed = true
    return r.fulfill(json({}))
  })

  await page.goto(`/entries/${ENTRY_ID}`)
  await page.getByRole('button', { name: 'Add photos' }).click()
  await page.getByRole('button', { name: 'Search Google Photos for 16 May 2026' }).click()

  await expect(page.getByText('Waiting for you to finish in Google Photos…')).toBeVisible()
  expect(await page.evaluate(() => (window as unknown as { __tab: { location: { href: string } } }).__tab.location.href))
    .toBe('https://photos.google.com/picker/abc/autoclose')

  // Close the panel mid-wait: the page still tracks the import and shows the outcome.
  await page.getByRole('button', { name: 'Add photos' }).click()
  await expect(page.getByText('Waiting for you to finish in Google Photos…')).toBeVisible()

  await expect(page.getByText('2 photos added from Google Photos · 1 video skipped')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('button', { name: 'Remove photo from entry' })).toHaveCount(2)

  await page.getByRole('button', { name: 'Dismiss' }).click()
  await expect(page.getByText(/photos added from Google Photos/)).toHaveCount(0)

  await page.reload()
  await expect(page.getByRole('button', { name: 'Remove photo from entry' })).toHaveCount(2)
  await expect(page.getByText(/photos added from Google Photos/)).toHaveCount(0)
})

test('wrong_account on return shows the account message', async ({ page }) => {
  await mockCommon(page)
  await page.route(`${API}/v1/integrations`, (r) => r.fulfill(json([])))
  await page.route(`${API}/v1/entries/${ENTRY_ID}`, (r) => r.fulfill(json(entry([]))))
  await page.route(`${API}/v1/entries/${ENTRY_ID}/photo-imports`, (r) => r.fulfill(json([])))

  await page.goto(`/entries/${ENTRY_ID}?google=wrong_account&expected=a%40b.com`)
  await expect(page.getByText('Use the Google account you connected for Calendar (a@b.com).')).toBeVisible()
})
