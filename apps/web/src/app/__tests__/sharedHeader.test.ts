import fs from 'fs'
import path from 'path'

const PAGES = [
  'diaries/page.tsx',
  'diaries/new/page.tsx',
  'diaries/restore/page.tsx',
  'diaries/[diaryId]/page.tsx',
  'diaries/[diaryId]/settings/page.tsx',
  'diaries/[diaryId]/rules/page.tsx',
  'diaries/[diaryId]/rules/new/page.tsx',
  'diaries/[diaryId]/restore/page.tsx',
  'diaries/[diaryId]/calendar-pick/page.tsx',
  'entries/[entryId]/page.tsx',
  'rules/[ruleId]/page.tsx',
  'photos/page.tsx',
  'plans/page.tsx',
]

it.each(PAGES)('%s renders AppHeader and no className="nav"', (p) => {
  const src = fs.readFileSync(path.join(__dirname, '..', p), 'utf8')
  expect(src).toContain('<AppHeader')
  expect(src).not.toContain('className="nav"')
})
