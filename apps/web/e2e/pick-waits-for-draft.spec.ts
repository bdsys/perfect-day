/**
 * E2E: after "New entry from Google Calendar", the entry page waits for the
 * AI draft (final review I1, item 24).
 *
 * The draft job now works out the entry's place before the AI runs, which
 * changes `updated_at` before there is any text. The page must keep waiting
 * until the draft (or its generation record) is there. All API calls are
 * mocked: the sequence below is what the worker produces.
 */

import { test, expect } from '@playwright/test'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const ENTRY_ID = '00000000-0000-4000-8000-000000000024'

function entry(over: Record<string, unknown>) {
  return {
    id: ENTRY_ID, diary_id: '00000000-0000-4000-8000-000000000001',
    entry_date: '2026-05-16', entry_end_date: null, title: null, body_markdown: null,
    body_source: 'llm', flagged_tokens: null, status: 'draft', created_by: 'manual',
    creation_source: 'calendar_pick', published_at: null, deleted_at: null,
    created_at: '2026-11-12T10:00:00Z', updated_at: '2026-11-12T10:00:00Z',
    events: [], rule_matches: [], last_generation: null, photos: [], enrichments: [],
    suggestion: null, location: null, tz_override: null, can_edit: true,
    ...over,
  }
}

test('the page keeps waiting while the job sets the place, and shows the draft when it lands', async ({ page }) => {
  await page.route(`${API}/v1/auth/refresh`, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ access_token: 't' }) }),
  )
  await page.route(`${API}/v1/auth/me`, (route) =>
    route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ id: 'u', email: 'pick@example.com', display_name: null }),
    }),
  )

  let calls = 0
  await page.route(`${API}/v1/entries/${ENTRY_ID}`, (route) => {
    calls += 1
    const body =
      calls === 1
        ? entry({}) // just created from the calendar event
        : calls <= 3
        ? entry({ // the job has set the place; the AI has not run yet
            updated_at: '2026-11-12T10:00:01Z',
            location: { name: 'Everett, Washington', lat: 47.98, lon: -122.2, source: 'event' },
          })
        : entry({ // the draft is in
            updated_at: '2026-11-12T10:00:05Z',
            title: 'Haircut', body_markdown: 'Sam got a haircut.',
            location: { name: 'Everett, Washington', lat: 47.98, lon: -122.2, source: 'event' },
            last_generation: {
              id: 'g', status: 'success', error: null, created_at: '2026-11-12T10:00:05Z',
              mode: 'events', model: 'claude-opus-5-5',
            },
          })
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })

  await page.goto(`/entries/${ENTRY_ID}?fromPick=1`)

  await expect(page.getByTestId('entry-body')).toHaveText('Sam got a haircut.', { timeout: 20_000 })
})
