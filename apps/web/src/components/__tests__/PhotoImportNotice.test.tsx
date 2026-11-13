import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { PhotoImportNotice } from '../PhotoImportNotice'
import { api, type PhotoImport } from '../../lib/api'
import { navigateTo } from '../../lib/navigate'

jest.mock('../../lib/navigate', () => ({ navigateTo: jest.fn() }))
jest.mock('../../lib/api', () => {
  const actual = jest.requireActual('../../lib/api')
  return {
    ...actual,
    api: {
      integrations: { getGoogleAuthUrl: jest.fn() },
      photoImports: { listForEntry: jest.fn(), dismiss: jest.fn() },
    },
  }
})

const listForEntry = api.photoImports.listForEntry as jest.Mock
const dismiss = api.photoImports.dismiss as jest.Mock
const authUrl = api.integrations.getGoogleAuthUrl as jest.Mock

function imp(over: Partial<PhotoImport> = {}): PhotoImport {
  return { id: 'i1', entry_id: 'e1', status: 'done', added_count: 0, skipped_videos: 0, failed_names: [], ...over }
}

afterEach(() => jest.clearAllMocks())

describe('PhotoImportNotice', () => {
  it('notice wording for added, skipped and failed', async () => {
    listForEntry.mockResolvedValue([imp({ added_count: 12, skipped_videos: 2, failed_names: ['IMG_1234.HEIC'] })])
    render(<PhotoImportNotice entryId="e1" refreshKey={0} onDismiss={jest.fn()} />)
    expect(await screen.findByText(
      "12 photos added from Google Photos · 2 videos skipped · 1 couldn't be copied (IMG_1234.HEIC): pick it again from Google Photos",
    )).toBeInTheDocument()
  })

  it('uses singular wording', async () => {
    listForEntry.mockResolvedValue([imp({ added_count: 1, skipped_videos: 1 })])
    render(<PhotoImportNotice entryId="e1" refreshKey={0} onDismiss={jest.fn()} />)
    expect(await screen.findByText('1 photo added from Google Photos · 1 video skipped')).toBeInTheDocument()
  })

  it('needs_reconnect offers reconnect', async () => {
    listForEntry.mockResolvedValue([imp({ status: 'needs_reconnect' })])
    authUrl.mockResolvedValue({ url: 'https://accounts.google.com/y' })
    render(<PhotoImportNotice entryId="e1" refreshKey={0} onDismiss={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Reconnect Google Photos' }))
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('https://accounts.google.com/y'))
    expect(authUrl).toHaveBeenCalledWith('photos', '/entries/e1')
  })

  it('dismiss hides the notice', async () => {
    listForEntry.mockResolvedValue([imp({ added_count: 3 })])
    dismiss.mockResolvedValue(undefined)
    const onDismiss = jest.fn()
    render(<PhotoImportNotice entryId="e1" refreshKey={0} onDismiss={onDismiss} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss' }))
    await waitFor(() => expect(screen.queryByText(/photos added/)).not.toBeInTheDocument())
    expect(dismiss).toHaveBeenCalledWith('i1')
    expect(onDismiss).toHaveBeenCalled()
  })
})
