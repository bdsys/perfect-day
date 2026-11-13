import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { GooglePhotosPick } from '../GooglePhotosPick'
import { api, ApiError, type Entry } from '../../lib/api'
import { navigateTo } from '../../lib/navigate'

jest.mock('../../lib/navigate', () => ({ navigateTo: jest.fn() }))
jest.mock('../../lib/api', () => {
  const actual = jest.requireActual('../../lib/api')
  return {
    ...actual,
    api: {
      integrations: { list: jest.fn(), getGoogleAuthUrl: jest.fn() },
      photoImports: { create: jest.fn(), get: jest.fn(), cancel: jest.fn() },
    },
  }
})

const list = api.integrations.list as jest.Mock
const authUrl = api.integrations.getGoogleAuthUrl as jest.Mock
const create = api.photoImports.create as jest.Mock
const getImport = api.photoImports.get as jest.Mock
const cancel = api.photoImports.cancel as jest.Mock

const entry = { id: 'e1', entry_date: '2026-05-16' } as Entry
const connected = [{ provider: 'google', scopes_granted: [], revoked: false, expires_at: null, google_email: null, google_name: null, photos: true }]

afterEach(() => {
  jest.clearAllMocks()
  jest.useRealTimers()
})

describe('GooglePhotosPick', () => {
  it('shows the entry date to search for', async () => {
    list.mockResolvedValue(connected)
    render(<GooglePhotosPick entry={entry} onDone={jest.fn()} />)
    expect(await screen.findByRole('button', { name: 'Search Google Photos for 16 May 2026' })).toBeInTheDocument()
  })

  it('not connected starts Google connect with the entry as return path', async () => {
    list.mockResolvedValue([{ ...connected[0], photos: false }])
    authUrl.mockResolvedValue({ url: 'https://accounts.google.com/x' })
    render(<GooglePhotosPick entry={entry} onDone={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Connect Google Photos' }))
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('https://accounts.google.com/x'))
    expect(authUrl).toHaveBeenCalledWith('photos', '/entries/e1')
  })

  it('opens the tab inside the click before any await', async () => {
    list.mockResolvedValue(connected)
    create.mockReturnValue(new Promise(() => {})) // never resolves
    const tab = { location: { href: '' }, close: jest.fn() }
    const open = jest.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    render(<GooglePhotosPick entry={entry} onDone={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /Search Google Photos/ }))
    expect(open).toHaveBeenCalledWith('', '_blank') // synchronously, no await between click and open
    open.mockRestore()
  })

  it('sends the opened tab to the picker, then waits and finishes', async () => {
    list.mockResolvedValue(connected)
    create.mockResolvedValue({ id: 'i1', picker_uri: 'https://photos.google.com/picker/abc', status: 'waiting' })
    const tab = { location: { href: '' }, close: jest.fn() }
    jest.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    jest.useFakeTimers()
    const onDone = jest.fn()
    getImport.mockResolvedValueOnce({ id: 'i1', status: 'copying' }).mockResolvedValue({ id: 'i1', status: 'done' })
    render(<GooglePhotosPick entry={entry} onDone={onDone} />)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: /Search Google Photos/ }))
    await act(async () => {})
    expect(tab.location.href).toBe('https://photos.google.com/picker/abc/autoclose')
    expect(screen.getByText('Waiting for you to finish in Google Photos…')).toBeInTheDocument()
    expect(getImport).toHaveBeenCalledTimes(1) // first check right away: still copying
    expect(onDone).not.toHaveBeenCalled()
    await act(async () => { jest.advanceTimersByTime(3000) }) // next check 3 s later: done
    expect(onDone).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Waiting for you to finish in Google Photos…')).not.toBeInTheDocument()
  })

  it('blocked tab shows a link to open Google Photos', async () => {
    list.mockResolvedValue(connected)
    create.mockResolvedValue({ id: 'i1', picker_uri: 'https://photos.google.com/picker/abc', status: 'waiting' })
    jest.spyOn(window, 'open').mockReturnValue(null)
    getImport.mockResolvedValue({ id: 'i1', status: 'waiting' })
    render(<GooglePhotosPick entry={entry} onDone={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /Search Google Photos/ }))
    const link = await screen.findByRole('link', { name: 'Open Google Photos' })
    expect(link).toHaveAttribute('href', 'https://photos.google.com/picker/abc/autoclose')
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('a failed start closes the blank tab and explains', async () => {
    list.mockResolvedValue(connected)
    create.mockRejectedValue(new ApiError(503, 'queue_unavailable', 'queue_unavailable'))
    const tab = { location: { href: '' }, close: jest.fn() }
    jest.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    render(<GooglePhotosPick entry={entry} onDone={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /Search Google Photos/ }))
    expect(await screen.findByText("Google Photos isn't responding. Try again in a minute.")).toBeInTheDocument()
    expect(tab.close).toHaveBeenCalled()
  })

  it('cancel calls cancel and stops waiting', async () => {
    list.mockResolvedValue(connected)
    create.mockResolvedValue({ id: 'i1', picker_uri: 'https://p', status: 'waiting' })
    cancel.mockResolvedValue(undefined)
    getImport.mockResolvedValue({ id: 'i1', status: 'waiting' })
    jest.spyOn(window, 'open').mockReturnValue({ location: { href: '' }, close: jest.fn() } as unknown as Window)
    render(<GooglePhotosPick entry={entry} onDone={jest.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /Search Google Photos/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('i1'))
    await waitFor(() => expect(screen.queryByText('Waiting for you to finish in Google Photos…')).not.toBeInTheDocument())
  })
})
