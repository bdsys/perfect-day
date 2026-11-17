import { StrictMode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import GooglePhotosOpenPage from '../page'
import { api, ApiError } from '@/lib/api'
import { replaceLocation } from '@/lib/navigate'

jest.mock('@/lib/navigate', () => ({ navigateTo: jest.fn(), replaceLocation: jest.fn() }))
jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return { ...actual, api: { photoImports: { create: jest.fn() } } }
})
const mockAuth = { user: { id: 'u1' } as Record<string, unknown> | null, loading: false }
jest.mock('@/lib/auth-context', () => ({ useAuth: () => mockAuth }))
let mockSearch = new URLSearchParams('entry=6abf71ad-2897-4845-99a4-917cdd768beb')
jest.mock('next/navigation', () => ({ useSearchParams: () => mockSearch }))

const create = api.photoImports.create as jest.Mock
const ENTRY = '6abf71ad-2897-4845-99a4-917cdd768beb'

beforeEach(() => {
  mockAuth.user = { id: 'u1' }
  mockAuth.loading = false
  mockSearch = new URLSearchParams(`entry=${ENTRY}`)
})
afterEach(() => jest.clearAllMocks())

describe('Google Photos opener tab', () => {
  it("creates a picker session and replaces itself with Google's picker", async () => {
    create.mockResolvedValue({ id: 'i1', picker_uri: 'https://photos.google.com/picker/abc', status: 'waiting' })
    render(<GooglePhotosOpenPage />)
    expect(screen.getByText('Opening Google Photos…')).toBeInTheDocument()
    await waitFor(() => expect(replaceLocation).toHaveBeenCalledWith('https://photos.google.com/picker/abc/autoclose'))
    expect(create).toHaveBeenCalledWith(ENTRY)
  })

  it('creates one session even when React runs the effect twice', async () => {
    create.mockResolvedValue({ id: 'i1', picker_uri: 'https://p', status: 'waiting' })
    render(<StrictMode><GooglePhotosOpenPage /></StrictMode>)
    await waitFor(() => expect(replaceLocation).toHaveBeenCalledTimes(1))
    expect(create).toHaveBeenCalledTimes(1)
  })

  it('waits for sign-in before asking for a session', () => {
    mockAuth.loading = true
    mockAuth.user = null
    render(<GooglePhotosOpenPage />)
    expect(create).not.toHaveBeenCalled()
    expect(screen.getByText('Opening Google Photos…')).toBeInTheDocument()
  })

  it('signed out: says to sign in, and asks for nothing', () => {
    mockAuth.user = null
    render(<GooglePhotosOpenPage />)
    expect(screen.getByRole('alert')).toHaveTextContent('Sign in to Perfect Day in this browser, then try again.')
    expect(create).not.toHaveBeenCalled()
  })

  it('a missing or malformed entry asks for nothing', () => {
    mockSearch = new URLSearchParams('entry=not-a-uuid')
    render(<GooglePhotosOpenPage />)
    expect(screen.getByRole('alert')).toHaveTextContent("This link doesn't say which entry to add photos to. Close this tab and try again.")
    expect(create).not.toHaveBeenCalled()
  })

  it.each([
    [new ApiError(409, 'not connected', 'google_photos_not_connected'),
      "Google Photos isn't connected yet. Close this tab and use Connect Google Photos on the entry."],
    [new ApiError(503, 'x', 'google_photos_not_enabled'),
      "Google Photos isn't set up for this app yet. The app's owner needs to turn on the Photos Picker API in Google Cloud."],
    [new ApiError(503, 'x', 'queue_unavailable'), "Google Photos isn't responding. Close this tab and try again in a minute."],
    [new Error('boom'), "Couldn't start Google Photos. Close this tab and try again."],
  ])('explains a failed start (%s)', async (err, message) => {
    create.mockRejectedValue(err)
    render(<GooglePhotosOpenPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(replaceLocation).not.toHaveBeenCalled()
  })
})
