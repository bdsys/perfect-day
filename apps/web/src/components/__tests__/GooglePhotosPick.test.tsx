import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { GooglePhotosPick } from '../GooglePhotosPick'
import { api, type Entry } from '../../lib/api'
import { navigateTo } from '../../lib/navigate'

jest.mock('../../lib/navigate', () => ({ navigateTo: jest.fn() }))
jest.mock('../../lib/api', () => {
  const actual = jest.requireActual('../../lib/api')
  return {
    ...actual,
    api: {
      integrations: { list: jest.fn(), getGoogleAuthUrl: jest.fn() },
      photoImports: { create: jest.fn() },
    },
  }
})

const list = api.integrations.list as jest.Mock
const authUrl = api.integrations.getGoogleAuthUrl as jest.Mock
const create = api.photoImports.create as jest.Mock

const entry = { id: 'e1', entry_date: '2026-05-16' } as Entry
const connected = [{ provider: 'google', scopes_granted: [], revoked: false, expires_at: null, google_email: null, google_name: null, photos: true }]

afterEach(() => {
  jest.clearAllMocks()
})

describe('GooglePhotosPick', () => {
  it('shows the entry date to search for', async () => {
    list.mockResolvedValue(connected)
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} />)
    expect(await screen.findByRole('button', { name: 'Search Google Photos for 16 May 2026' })).toBeInTheDocument()
  })

  it('not connected starts Google connect with the entry as return path', async () => {
    list.mockResolvedValue([{ ...connected[0], photos: false }])
    authUrl.mockResolvedValue({ url: 'https://accounts.google.com/x' })
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Connect Google Photos' }))
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('https://accounts.google.com/x'))
    expect(authUrl).toHaveBeenCalledWith('photos', '/entries/e1')
  })

  it('two quick taps on Connect give one authorize call', async () => {
    list.mockResolvedValue([{ ...connected[0], photos: false }])
    authUrl.mockReturnValue(new Promise(() => {}))
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} />)
    const btn = await screen.findByRole('button', { name: 'Connect Google Photos' })
    fireEvent.click(btn)
    fireEvent.click(btn)
    expect(authUrl).toHaveBeenCalledTimes(1)
    expect(btn).toBeDisabled()
  })

  it('opens our Google Photos page in a new tab inside the click, with no way back', async () => {
    list.mockResolvedValue(connected)
    const open = jest.spyOn(window, 'open').mockReturnValue(null)
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /Search Google Photos/ }))
    // Synchronously in the click: a real address, so every browser opens it (iOS Chrome
    // ignored a blank tab that was sent to Google afterwards). The tab starts the session.
    expect(open).toHaveBeenCalledWith('/google-photos/open?entry=e1', '_blank', 'noopener')
    expect(create).not.toHaveBeenCalled()
    open.mockRestore()
  })

  it('tells the page it started, and says Google Photos is opening with a backup link', async () => {
    list.mockResolvedValue(connected)
    jest.spyOn(window, 'open').mockReturnValue(null)
    const onStarted = jest.fn()
    const onOpeningChange = jest.fn()
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={onStarted} onOpeningChange={onOpeningChange} />)
    fireEvent.click(await screen.findByRole('button', { name: /Search Google Photos/ }))
    expect(onStarted).toHaveBeenCalledTimes(1)
    expect(onOpeningChange).toHaveBeenLastCalledWith(true)
    expect(screen.getByText('Opening Google Photos in a new tab…')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'Open Google Photos' })
    expect(link).toHaveAttribute('href', '/google-photos/open?entry=e1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(screen.queryByRole('button', { name: /Search Google Photos/ })).not.toBeInTheDocument()
  })

  it('two quick taps open one tab', async () => {
    list.mockResolvedValue(connected)
    const open = jest.spyOn(window, 'open').mockReturnValue(null)
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} />)
    const btn = await screen.findByRole('button', { name: /Search Google Photos/ })
    fireEvent.click(btn)
    fireEvent.click(btn)
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('once the page sees the import, it shows progress instead', async () => {
    list.mockResolvedValue(connected)
    jest.spyOn(window, 'open').mockReturnValue(null)
    const onOpeningChange = jest.fn()
    const { rerender } = render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} onOpeningChange={onOpeningChange} />)
    fireEvent.click(await screen.findByRole('button', { name: /Search Google Photos/ }))
    rerender(<GooglePhotosPick entry={entry} inFlight onStarted={jest.fn()} onOpeningChange={onOpeningChange} />)
    expect(await screen.findByText('Google Photos is open. Finish your pick there.')).toBeInTheDocument()
    expect(onOpeningChange).toHaveBeenLastCalledWith(false)
  })

  it('after a minute with no import it offers the button again', async () => {
    list.mockResolvedValue(connected)
    jest.spyOn(window, 'open').mockReturnValue(null)
    const onOpeningChange = jest.fn()
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} onOpeningChange={onOpeningChange} />)
    const btn = await screen.findByRole('button', { name: /Search Google Photos/ })
    jest.useFakeTimers()
    fireEvent.click(btn)
    act(() => { jest.advanceTimersByTime(59_000) })
    expect(screen.getByText('Opening Google Photos in a new tab…')).toBeInTheDocument()
    act(() => { jest.advanceTimersByTime(1_000) })
    expect(screen.getByRole('button', { name: /Search Google Photos/ })).toBeEnabled()
    expect(screen.getByText("Didn't see Google Photos open? Try again.")).toBeInTheDocument()
    expect(onOpeningChange).toHaveBeenLastCalledWith(false)
    jest.useRealTimers()
  })

  it('while an import is in flight it shows progress, not Search', async () => {
    list.mockResolvedValue(connected)
    render(<GooglePhotosPick entry={entry} inFlight onStarted={jest.fn()} />)
    expect(await screen.findByText('Google Photos is open. Finish your pick there.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Search Google Photos/ })).not.toBeInTheDocument()
  })

  it('a failed connection check says so and offers Try again, not Connect', async () => {
    list.mockRejectedValueOnce(new Error('boom')).mockResolvedValueOnce(connected)
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} />)
    expect(await screen.findByText("Couldn't check your Google connection. Try again.")).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Connect Google Photos/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('button', { name: /Search Google Photos/ })).toBeInTheDocument()
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('shows the waiting line only while waiting, and its own line while copying', async () => {
    list.mockResolvedValue(connected)
    const { rerender } = render(<GooglePhotosPick entry={entry} inFlight={true} onStarted={jest.fn()} />)
    expect(await screen.findByText('Google Photos is open. Finish your pick there.')).toBeInTheDocument()
    rerender(<GooglePhotosPick entry={entry} inFlight={true} copying={true} onStarted={jest.fn()} />)
    expect(screen.getByText('Copying your photos from Google Photos…')).toBeInTheDocument()
    expect(screen.queryByText('Google Photos is open. Finish your pick there.')).not.toBeInTheDocument()
  })

  it('Connect works again after Back from Google restores the page from bfcache', async () => {
    list.mockResolvedValue([{ ...connected[0], photos: false }])
    authUrl.mockResolvedValue({ url: 'https://accounts.google.com/x' })
    render(<GooglePhotosPick entry={entry} inFlight={false} onStarted={jest.fn()} />)
    const btn = await screen.findByRole('button', { name: 'Connect Google Photos' })
    fireEvent.click(btn)
    await waitFor(() => expect(navigateTo).toHaveBeenCalled())
    expect(btn).toBeDisabled()
    act(() => { window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: false })) })
    expect(btn).toBeDisabled()
    act(() => { window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })) })
    expect(btn).not.toBeDisabled()
    fireEvent.click(btn)
    await waitFor(() => expect(authUrl).toHaveBeenCalledTimes(2))
  })

})
