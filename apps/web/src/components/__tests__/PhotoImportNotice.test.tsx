import { useState } from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { PhotoImportNotice } from '../PhotoImportNotice'
import { api, type PhotoImport } from '../../lib/api'
import { GooglePhotosPick } from '../GooglePhotosPick'
import { navigateTo } from '../../lib/navigate'

jest.mock('../../lib/navigate', () => ({ navigateTo: jest.fn() }))
jest.mock('../../lib/api', () => {
  const actual = jest.requireActual('../../lib/api')
  return {
    ...actual,
    api: {
      photoImports: { listForEntry: jest.fn(), dismiss: jest.fn(), cancel: jest.fn(), create: jest.fn() },
      integrations: { getGoogleAuthUrl: jest.fn(), list: jest.fn() },
    },
  }
})

const listForEntry = api.photoImports.listForEntry as jest.Mock
const dismiss = api.photoImports.dismiss as jest.Mock
const authUrl = api.integrations.getGoogleAuthUrl as jest.Mock

function imp(over: Partial<PhotoImport> = {}): PhotoImport {
  return { id: 'i1', entry_id: 'e1', status: 'done', added_count: 0, skipped_videos: 0, failed_names: [], ...over }
}

afterEach(() => {
  jest.clearAllMocks()
  jest.useRealTimers()
})

describe('PhotoImportNotice', () => {
  it('notice wording for added, skipped and failed', async () => {
    listForEntry.mockResolvedValue([imp({ added_count: 12, skipped_videos: 2, failed_names: ['IMG_1234.HEIC'] })])
    render(<PhotoImportNotice entryId="e1" onFinished={jest.fn()} onDismiss={jest.fn()} />)
    expect(await screen.findByText(
      "12 photos added from Google Photos · 2 videos skipped · 1 couldn't be copied (IMG_1234.HEIC): pick it again from Google Photos",
    )).toBeInTheDocument()
  })

  it('uses singular wording', async () => {
    listForEntry.mockResolvedValue([imp({ added_count: 1, skipped_videos: 1 })])
    render(<PhotoImportNotice entryId="e1" onFinished={jest.fn()} onDismiss={jest.fn()} />)
    expect(await screen.findByText('1 photo added from Google Photos · 1 video skipped')).toBeInTheDocument()
  })

  it('needs_reconnect offers reconnect', async () => {
    listForEntry.mockResolvedValue([imp({ status: 'needs_reconnect' })])
    authUrl.mockResolvedValue({ url: 'https://accounts.google.com/y' })
    render(<PhotoImportNotice entryId="e1" onFinished={jest.fn()} onDismiss={jest.fn()} />)
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

  it('on load with a waiting import, shows the waiting line and polls every 3 s', async () => {
    jest.useFakeTimers()
    listForEntry.mockResolvedValue([imp({ status: 'waiting' })])
    render(<PhotoImportNotice entryId="e1" onFinished={jest.fn()} />)
    await act(async () => {})
    expect(screen.getByText('Waiting for you to finish in Google Photos…')).toBeInTheDocument()
    expect(listForEntry).toHaveBeenCalledTimes(1)
    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(listForEntry).toHaveBeenCalledTimes(2)
  })

  it('copying shows its own line', async () => {
    listForEntry.mockResolvedValue([imp({ status: 'copying' })])
    render(<PhotoImportNotice entryId="e1" onFinished={jest.fn()} />)
    expect(await screen.findByText('Copying photos from Google Photos…')).toBeInTheDocument()
  })

  it('cancel on the waiting line calls cancel', async () => {
    listForEntry.mockResolvedValue([imp({ status: 'waiting' })])
    ;(api.photoImports.cancel as jest.Mock).mockResolvedValue(undefined)
    render(<PhotoImportNotice entryId="e1" onFinished={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(api.photoImports.cancel).toHaveBeenCalledWith('i1'))
  })

  it('polling stops once nothing is in flight, and onFinished fires once', async () => {
    jest.useFakeTimers()
    const onFinished = jest.fn()
    listForEntry
      .mockResolvedValueOnce([imp({ status: 'copying' })])
      .mockResolvedValue([imp({ status: 'done', added_count: 2 })])
    render(<PhotoImportNotice entryId="e1" onFinished={onFinished} />)
    await act(async () => {})
    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(onFinished).toHaveBeenCalledTimes(1)
    expect(screen.getByText('2 photos added from Google Photos')).toBeInTheDocument()
    const calls = listForEntry.mock.calls.length
    await act(async () => { jest.advanceTimersByTime(9000) })
    expect(listForEntry).toHaveBeenCalledTimes(calls)
  })

  it('closing the panel during the wait still ends in the outcome notice and a refresh', async () => {
    jest.useFakeTimers()
    ;(api.integrations.list as jest.Mock).mockResolvedValue([
      { provider: 'google', scopes_granted: [], revoked: false, expires_at: null, google_email: null, google_name: null, photos: true },
    ])
    ;(api.photoImports.create as jest.Mock).mockResolvedValue({ id: 'i1', picker_uri: 'https://p', status: 'waiting' })
    jest.spyOn(window, 'open').mockReturnValue({ location: { href: '' }, close: jest.fn() } as unknown as Window)
    listForEntry
      .mockResolvedValueOnce([])
      .mockResolvedValueOnce([imp({ status: 'waiting' })])
      .mockResolvedValue([imp({ status: 'done', added_count: 4 })])
    const onFinished = jest.fn()
    function Page() {
      const [panel, setPanel] = useState(true)
      const [inFlight, setInFlight] = useState(false)
      const [key, setKey] = useState(0)
      return (
        <>
          <button onClick={() => setPanel(false)}>close panel</button>
          {panel && (
            <GooglePhotosPick
              entry={{ id: 'e1', entry_date: '2026-05-16' } as never}
              inFlight={inFlight}
              onStarted={() => setKey((k) => k + 1)}
            />
          )}
          <PhotoImportNotice entryId="e1" refreshKey={key} onFinished={onFinished} onInFlightChange={setInFlight} />
        </>
      )
    }
    render(<Page />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /Search Google Photos/ }))
    await act(async () => {})
    expect(screen.getByText('Waiting for you to finish in Google Photos…')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'close panel' }))
    await act(async () => { jest.advanceTimersByTime(3000) })
    expect(onFinished).toHaveBeenCalledTimes(1)
    expect(screen.getByText('4 photos added from Google Photos')).toBeInTheDocument()
  })
})
