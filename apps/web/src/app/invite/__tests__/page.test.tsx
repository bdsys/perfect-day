import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import InvitePage from '../[token]/page'
import { ApiError } from '@/lib/api'

const mockPush = jest.fn()
const mockReplace = jest.fn()
const mockRouter = { push: mockPush, replace: mockReplace }
jest.mock('next/navigation', () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ token: 'tok' }),
}))
const mockLogout = jest.fn()
let mockAuth: { user: unknown; loading: boolean; logout?: () => Promise<void> }
jest.mock('@/lib/auth-context', () => ({ useAuth: () => mockAuth }))
const mockPreview = jest.fn()
const mockAccept = jest.fn()
jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return { ...actual, api: { sharing: { preview: (...a: unknown[]) => mockPreview(...a), accept: (...a: unknown[]) => mockAccept(...a) } } }
})

beforeEach(() => {
  jest.clearAllMocks()
  mockAuth = { user: { id: 'u1' }, loading: false, logout: mockLogout }
  mockLogout.mockResolvedValue(undefined)
})

const pending = { diary_id: 'd1', diary_name: 'Baby Book', owner_name: 'Andrew', role: 'viewer', status: 'pending' }

describe('InvitePage', () => {
  it('signed out: sentence, links, no API call', () => {
    mockAuth = { user: null, loading: false }
    render(<InvitePage />)
    expect(
      screen.getByText("You've been invited to a diary on Perfect Day. Sign in or create an account to see it."),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /sign in/i })).toHaveAttribute('href', '/login?next=%2Finvite%2Ftok')
    expect(screen.getByRole('link', { name: /create an account/i })).toHaveAttribute('href', '/register?next=%2Finvite%2Ftok')
    expect(mockPreview).not.toHaveBeenCalled()
  })

  it('signed in: shows the share and accepts', async () => {
    mockPreview.mockResolvedValue(pending)
    mockAccept.mockResolvedValue({ diary_id: 'd1' })
    render(<InvitePage />)
    expect(await screen.findByText(/Andrew shared/)).toBeInTheDocument()
    expect(screen.getByText('Baby Book')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open diary' }))
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/diaries/d1'))
    expect(mockAccept).toHaveBeenCalledWith('tok')
  })

  it('disables Open diary while accepting', async () => {
    mockPreview.mockResolvedValue(pending)
    mockAccept.mockReturnValue(new Promise(() => {}))
    render(<InvitePage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open diary' }))
    expect(await screen.findByRole('button', { name: 'Opening…' })).toBeDisabled()
  })

  it('shows an error when accept fails', async () => {
    mockPreview.mockResolvedValue(pending)
    mockAccept.mockRejectedValue(new ApiError(500, 'boom'))
    render(<InvitePage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Open diary' }))
    expect(await screen.findByText('boom')).toBeInTheDocument()
  })

  it('wrong email', async () => {
    mockPreview.mockRejectedValue(new ApiError(403, 'x', 'invite_wrong_email', { masked_email: 'a***@x.com', owner_name: 'Andrew' }))
    render(<InvitePage />)
    expect(
      await screen.findByText('This invite is for a***@x.com. Sign in with that address, or ask Andrew for a new link.'),
    ).toBeInTheDocument()
  })

  it('wrong email offers to sign out and use another account', async () => {
    mockPreview.mockRejectedValue(new ApiError(403, 'x', 'invite_wrong_email', { masked_email: 'a***@x.com', owner_name: 'Andrew' }))
    render(<InvitePage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out and use another account' }))
    await waitFor(() => expect(mockLogout).toHaveBeenCalled())
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/login?next=%2Finvite%2Ftok'))
  })

  it('other errors have no switch-account button', async () => {
    mockPreview.mockRejectedValue(new ApiError(410, 'x', 'invite_expired', { owner_name: 'Andrew' }))
    render(<InvitePage />)
    await screen.findByText('This invite has expired. Ask Andrew for a new link.')
    expect(screen.queryByRole('button', { name: 'Sign out and use another account' })).not.toBeInTheDocument()
  })

  it('expired', async () => {
    mockPreview.mockRejectedValue(new ApiError(410, 'x', 'invite_expired', { owner_name: 'Andrew' }))
    render(<InvitePage />)
    expect(await screen.findByText('This invite has expired. Ask Andrew for a new link.')).toBeInTheDocument()
  })

  it('not found', async () => {
    mockPreview.mockRejectedValue(new ApiError(404, 'x', 'invite_not_found'))
    render(<InvitePage />)
    expect(
      await screen.findByText("This invite link doesn't work any more. Ask the person who sent it for a new link."),
    ).toBeInTheDocument()
  })

  it.each(['owner', 'member'])('status %s goes to the diary', async (status) => {
    mockPreview.mockResolvedValue({ ...pending, status })
    render(<InvitePage />)
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/diaries/d1'))
  })
})
