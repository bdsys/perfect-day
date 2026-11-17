import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import ProfilePage from '../page'
import { api, ApiError } from '@/lib/api'
import { navigateTo } from '@/lib/navigate'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      auth: {
        updateProfile: jest.fn(),
        uploadAvatar: jest.fn(),
        deleteAvatar: jest.fn(),
        logoutAll: jest.fn(),
        deleteAccount: jest.fn(),
        myPlan: jest.fn(),
      },
      integrations: { list: jest.fn(), getGoogleAuthUrl: jest.fn(), disconnectGoogle: jest.fn() },
    },
  }
})
jest.mock('@/lib/navigate', () => ({ navigateTo: jest.fn(), replaceLocation: jest.fn() }))

const mockRefreshUser = jest.fn()
const mockLogout = jest.fn()
let mockUser: Record<string, unknown> | null
jest.mock('@/lib/auth-context', () => ({
  useAuth: () => ({ user: mockUser, loading: false, avatarUrl: null, refreshUser: mockRefreshUser, logout: mockLogout }),
}))
const mockReplace = jest.fn()
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: jest.fn(), replace: mockReplace }),
}))

const a = api as unknown as {
  auth: Record<string, jest.Mock>
  integrations: Record<string, jest.Mock>
}

beforeEach(() => {
  jest.clearAllMocks()
  mockUser = {
    id: 'u1', email: 'pat@example.com', temperature_unit: 'F', avatar: { kind: 'initial' },
  }
  a.auth.myPlan.mockResolvedValue({ plan_name: 'Plus' })
  a.auth.updateProfile.mockResolvedValue({})
  a.auth.uploadAvatar.mockResolvedValue({})
  a.auth.logoutAll.mockResolvedValue(undefined)
  a.auth.deleteAccount.mockResolvedValue({ deletion_scheduled_for: '2026-11-24T17:04:00Z' })
  a.integrations.list.mockResolvedValue([])
  mockLogout.mockResolvedValue(undefined)
})

async function renderPage() {
  render(<ProfilePage />)
  await screen.findByText('Plus')
}

describe('ProfilePage', () => {
  it('shows who you are and your plan', async () => {
    await renderPage()
    expect(screen.getByRole('heading', { name: 'Profile' })).toBeInTheDocument()
    expect(screen.getByText(/pat@example\.com/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /plans/i })).toHaveAttribute('href', '/plans')
  })

  it('saves the unit on change', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('radio', { name: '° C' }))
    await waitFor(() => expect(a.auth.updateProfile).toHaveBeenCalledWith({ temperature_unit: 'C' }))
    await waitFor(() => expect(mockRefreshUser).toHaveBeenCalled())
    expect(screen.getByText('Weather on your entries and in new AI drafts.')).toBeInTheDocument()
  })

  it('saves an icon on click and marks it', async () => {
    await renderPage()
    expect(screen.getAllByRole('button', { pressed: false }).filter((b) => b.hasAttribute('aria-pressed'))).toHaveLength(16)
    const panda = screen.getByRole('button', { name: 'panda' })
    fireEvent.click(panda)
    await waitFor(() => expect(a.auth.updateProfile).toHaveBeenCalledWith({ avatar_icon: 'panda' }))
    await waitFor(() => expect(mockRefreshUser).toHaveBeenCalled())
    await waitFor(() => expect(panda).toHaveAttribute('aria-pressed', 'true'))
  })

  it('Use my initial', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Use my initial' }))
    await waitFor(() => expect(a.auth.updateProfile).toHaveBeenCalledWith({ avatar_icon: null }))
    await waitFor(() => expect(mockRefreshUser).toHaveBeenCalled())
  })

  it('uploads a photo and refreshes', async () => {
    await renderPage()
    const file = new File(['x'], 'me.png', { type: 'image/png' })
    fireEvent.change(screen.getByLabelText('Upload a photo'), { target: { files: [file] } })
    await waitFor(() => expect(a.auth.uploadAvatar).toHaveBeenCalledWith(file))
    await waitFor(() => expect(mockRefreshUser).toHaveBeenCalled())
  })

  it.each([
    [413, 'That photo is too large.'],
    [415, "That file isn't a photo we can use."],
    [500, "Couldn't save your icon. Try again."],
  ])('upload errors say what went wrong (%s)', async (status, message) => {
    a.auth.uploadAvatar.mockRejectedValue(new ApiError(status, 'x'))
    await renderPage()
    fireEvent.change(screen.getByLabelText('Upload a photo'), {
      target: { files: [new File(['x'], 'me.png', { type: 'image/png' })] },
    })
    expect(await screen.findByText(message)).toBeInTheDocument()
  })

  it('delete needs the word DELETE', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Delete account…' }))
    expect(
      screen.getByText('Your diaries, entries and photos are deleted after 7 days. Log in during those 7 days to undo it.'),
    ).toBeInTheDocument()
    const input = screen.getByLabelText('Type DELETE to confirm')
    const confirm = screen.getByRole('button', { name: 'Delete my account' })
    expect(confirm).toBeDisabled()
    for (const v of ['delete', 'DELET', 'DELETE ']) {
      fireEvent.change(input, { target: { value: v } })
      expect(confirm).toBeDisabled()
    }
    fireEvent.change(input, { target: { value: 'DELETE' } })
    expect(confirm).toBeEnabled()
    fireEvent.click(confirm)
    await waitFor(() => expect(a.auth.deleteAccount).toHaveBeenCalled())
    await waitFor(() => expect(mockLogout).toHaveBeenCalled())
    expect(navigateTo).toHaveBeenCalledWith('/login?deleted=' + encodeURIComponent('2026-11-24T17:04:00Z'))
  })

  it('Cancel closes the delete panel', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Delete account…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByLabelText('Type DELETE to confirm')).not.toBeInTheDocument()
  })

  it('log out on all devices', async () => {
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Log out on all devices' }))
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('/login'))
    expect(a.auth.logoutAll.mock.invocationCallOrder[0]).toBeLessThan(mockLogout.mock.invocationCallOrder[0])
  })

  it('Google connect asks for calendar and returns to /profile', async () => {
    a.integrations.getGoogleAuthUrl.mockResolvedValue({ url: 'https://google.example/auth' })
    await renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Connect Google' }))
    await waitFor(() => expect(a.integrations.getGoogleAuthUrl).toHaveBeenCalledWith('calendar', '/profile'))
    expect(navigateTo).toHaveBeenCalledWith('https://google.example/auth')
  })

  it('Google disconnect reloads the list', async () => {
    a.integrations.list
      .mockResolvedValueOnce([{ provider: 'google', revoked: false, google_email: 'g@x.com', scopes_granted: [] }])
      .mockResolvedValue([])
    a.integrations.disconnectGoogle.mockResolvedValue(undefined)
    await renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Disconnect Google' }))
    await waitFor(() => expect(a.integrations.disconnectGoogle).toHaveBeenCalled())
    expect(await screen.findByRole('button', { name: 'Connect Google' })).toBeInTheDocument()
  })

  it('sends a signed-out visitor to /login', async () => {
    mockUser = null
    render(<ProfilePage />)
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/login'))
  })
})
