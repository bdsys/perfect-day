import { render, screen } from '@testing-library/react'
import ComingUp from '../ComingUp'
import { api } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return { ...actual, api: { wishLists: { comingUp: jest.fn() } } }
})

const row = (id: string, title: string, list_date: string) => ({
  id, diary_id: 'diary-1', title, list_date, status: 'published', item_count: 1,
})

beforeEach(() => jest.resetAllMocks())

describe('ComingUp', () => {
  it('Coming up lists soonest first and is hidden when empty', async () => {
    ;(api.wishLists.comingUp as jest.Mock).mockResolvedValueOnce({
      wish_lists: [row('b', 'Christmas', '2026-12-25'), row('a', "Emma's 6th birthday", '2026-03-14')],
    })
    const { container, unmount } = render(<ComingUp diaryId="diary-1" />)
    const links = await screen.findAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual([
      "Wish list · Emma's 6th birthday · Sat 14 Mar",
      'Wish list · Christmas · Fri 25 Dec',
    ])
    expect(links[0]).toHaveAttribute('href', '/diaries/diary-1/wish-lists/a')
    unmount()

    ;(api.wishLists.comingUp as jest.Mock).mockResolvedValueOnce({ wish_lists: [] })
    const empty = render(<ComingUp diaryId="diary-1" />)
    await new Promise((r) => setTimeout(r))
    expect(api.wishLists.comingUp).toHaveBeenCalledTimes(2)
    expect(empty.container).toBeEmptyDOMElement()
    expect(container).toBeEmptyDOMElement()
  })

  it('stays hidden when the lookup fails', async () => {
    ;(api.wishLists.comingUp as jest.Mock).mockRejectedValue(new Error('boom'))
    const { container } = render(<ComingUp diaryId="diary-1" />)
    await new Promise((r) => setTimeout(r))
    expect(container).toBeEmptyDOMElement()
  })
})
