import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DeletionNotice from '../DeletionNotice'
import { api } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return { ...actual, api: { sharing: { decline: jest.fn() } } }
})

const DATE = new Date('2026-11-29T12:00:00Z').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })

beforeEach(() => jest.clearAllMocks())

describe('DeletionNotice', () => {
  it('says the diary will be deleted when it is not handed over', () => {
    const { container } = render(
      <DeletionNotice
        diaryId="d1"
        diaryName="Emma diary"
        notice={{ owner_name: 'Sam', deletes_on: '2026-11-29T12:00:00Z', becomes_yours: false }}
        onDeclined={jest.fn()}
      />,
    )
    expect(container).toHaveTextContent(`Sam is deleting their account. Emma diary will be deleted on ${DATE}.`)
    expect(screen.queryByRole('button', { name: 'Decline' })).not.toBeInTheDocument()
  })

  it('says the diary stays shared when it goes to someone else', () => {
    const { container } = render(
      <DeletionNotice
        diaryId="d1"
        diaryName="Emma diary"
        notice={{ owner_name: 'Sam', deletes_on: '2026-11-29T12:00:00Z', becomes_yours: false, goes_to_name: 'Mom' }}
        onDeclined={jest.fn()}
      />,
    )
    expect(container).toHaveTextContent(
      `Sam is deleting their account. Emma diary stays shared with you; Mom will look after it from ${DATE}.`,
    )
    expect(container).not.toHaveTextContent('will be deleted')
    expect(screen.queryByRole('button', { name: 'Decline' })).not.toBeInTheDocument()
  })

  it('says the diary becomes yours and Decline calls the API after a confirm', async () => {
    ;(api.sharing.decline as jest.Mock).mockResolvedValue(undefined)
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true)
    const onDeclined = jest.fn()
    const { container } = render(
      <DeletionNotice
        diaryId="d1"
        diaryName="Emma diary"
        notice={{ owner_name: 'Sam', deletes_on: '2026-11-29T12:00:00Z', becomes_yours: true }}
        onDeclined={onDeclined}
      />,
    )
    expect(container).toHaveTextContent(`Sam is deleting their account. Emma diary becomes yours on ${DATE}.`)
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }))
    expect(confirm).toHaveBeenCalledWith(
      `Decline? Emma diary will be deleted with Sam's account on ${DATE}.`,
    )
    await waitFor(() => expect(api.sharing.decline).toHaveBeenCalledWith('d1'))
    await waitFor(() => expect(onDeclined).toHaveBeenCalled())
    confirm.mockRestore()
  })

  it('does nothing when the confirm is refused', () => {
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(false)
    render(
      <DeletionNotice
        diaryId="d1"
        diaryName="Emma diary"
        notice={{ owner_name: 'Sam', deletes_on: '2026-11-29T12:00:00Z', becomes_yours: true }}
        onDeclined={jest.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Decline' }))
    expect(api.sharing.decline).not.toHaveBeenCalled()
    confirm.mockRestore()
  })
})
