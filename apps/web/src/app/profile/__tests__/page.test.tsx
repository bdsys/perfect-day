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
        logout: jest.fn(),
        deleteAccount: jest.fn(),
        handoverOptions: jest.fn(),
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
let mockSearch = ''
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: jest.fn(), replace: mockReplace }),
  useSearchParams: () => new URLSearchParams(mockSearch),
}))

const a = api as unknown as {
  auth: Record<string, jest.Mock>
  integrations: Record<string, jest.Mock>
}

beforeEach(() => {
  jest.clearAllMocks()
  mockSearch = ''
  mockUser = {
    id: 'u1', email: 'pat@example.com', temperature_unit: 'F', avatar: { kind: 'initial' },
  }
  a.auth.myPlan.mockResolvedValue({ plan_name: 'Plus' })
  a.auth.updateProfile.mockResolvedValue({})
  a.auth.uploadAvatar.mockResolvedValue({})
  a.auth.logoutAll.mockResolvedValue(undefined)
  a.auth.deleteAccount.mockResolvedValue({ deletion_scheduled_for: '2026-11-24T17:04:00Z', handovers: {} })
  a.integrations.list.mockResolvedValue([])
  a.auth.handoverOptions.mockResolvedValue([])
  a.auth.logout.mockResolvedValue(undefined)
  mockLogout.mockResolvedValue(undefined)
})

async function renderPage() {
  render(<ProfilePage />)
  await screen.findByText('Plus')
}

describe('ProfilePage', () => {
  it('lays the page out in the page container so it lines up with the header', async () => {
    const { container } = render(<ProfilePage />)
    await screen.findByText('Plus')
    expect(container.querySelector('.page-container')).toContainElement(screen.getByRole('heading', { name: 'Profile' }))
  })

  it('explains a partial Google grant without naming a button this page lacks', async () => {
    mockSearch = 'google=partial&missing=calendar'
    await renderPage()
    const msg = await screen.findByText(/Calendar access wasn't granted/)
    expect(msg.textContent).not.toMatch(/Connect Google Calendar/)
  })

  it('explains a cancelled Google connection', async () => {
    mockSearch = 'google=cancelled'
    await renderPage()
    expect(await screen.findByText('You cancelled the Google connection. Nothing was changed.')).toBeInTheDocument()
  })

  it('reloads the Google connection after a successful connect', async () => {
    mockSearch = 'google=connected'
    await renderPage()
    expect(await screen.findByText('Google connected.')).toBeInTheDocument()
  })

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
    await waitFor(() => expect(a.auth.handoverOptions).toHaveBeenCalled())
    expect(confirm).toBeDisabled()
    for (const v of ['delete', 'DELET', 'DELETE ']) {
      fireEvent.change(input, { target: { value: v } })
      expect(confirm).toBeDisabled()
    }
    fireEvent.change(input, { target: { value: 'DELETE' } })
    await waitFor(() => expect(confirm).toBeEnabled())
    fireEvent.click(confirm)
    await waitFor(() => expect(a.auth.deleteAccount).toHaveBeenCalledWith(undefined))
    await waitFor(() => expect(a.auth.logout).toHaveBeenCalled())
    await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('/login?deleted=' + encodeURIComponent('2026-11-24T17:04:00Z')))
    // No context logout: it would flip `user` and let the page's own redirect race the notice.
    expect(mockLogout).not.toHaveBeenCalled()
    expect(mockReplace).not.toHaveBeenCalled()
  })

  describe('hand-over choice', () => {
    const options = [
      { diary_id: 'd1', diary_name: 'Ava', members: [{ user_id: 'm1', name: 'Mom' }, { user_id: 'm2', name: 'Dad' }] },
      { diary_id: 'd2', diary_name: 'Trip', members: [{ user_id: 'm3', name: 'Sam' }] },
    ]
    async function open() {
      await renderPage()
      fireEvent.click(screen.getByRole('button', { name: 'Delete account…' }))
      fireEvent.change(screen.getByLabelText('Type DELETE to confirm'), { target: { value: 'DELETE' } })
    }

    it('shows radios with Delete it checked, and sends the picked pairs', async () => {
      a.auth.handoverOptions.mockResolvedValue(options)
      await open()
      expect(await screen.findByText('Ava is shared with Mom, Dad.')).toBeInTheDocument()
      const del = screen.getAllByRole('radio', { name: 'Delete it' })
      expect(del).toHaveLength(2)
      expect(del[0]).toBeChecked()
      fireEvent.click(screen.getByRole('radio', { name: 'Give it to Mom' }))
      expect(del[0]).not.toBeChecked()
      fireEvent.click(screen.getByRole('button', { name: 'Delete my account' }))
      await waitFor(() => expect(a.auth.deleteAccount).toHaveBeenCalledWith({ d1: 'm1' }))
    })

    it('tells the owner when the server did not record the choice', async () => {
      a.auth.handoverOptions.mockResolvedValue(options)
      a.auth.deleteAccount.mockResolvedValue({ deletion_scheduled_for: '2026-11-24T17:04:00Z', handovers: {} })
      await open()
      await screen.findByText('Ava is shared with Mom, Dad.')
      fireEvent.click(screen.getByRole('radio', { name: 'Give it to Mom' }))
      fireEvent.click(screen.getByRole('button', { name: 'Delete my account' }))
      expect(
        await screen.findByText(
          "Your choice of who gets your shared diaries didn't save. Restore your account from the sign-in page and try again.",
        ),
      ).toBeInTheDocument()
      expect(navigateTo).not.toHaveBeenCalledWith(expect.stringContaining('/login?deleted='))
    })

    it('goes on as usual when the server echoes the choice', async () => {
      a.auth.handoverOptions.mockResolvedValue(options)
      a.auth.deleteAccount.mockResolvedValue({ deletion_scheduled_for: '2026-11-24T17:04:00Z', handovers: { d1: 'm1' } })
      await open()
      await screen.findByText('Ava is shared with Mom, Dad.')
      fireEvent.click(screen.getByRole('radio', { name: 'Give it to Mom' }))
      fireEvent.click(screen.getByRole('button', { name: 'Delete my account' }))
      await waitFor(() => expect(navigateTo).toHaveBeenCalledWith('/login?deleted=' + encodeURIComponent('2026-11-24T17:04:00Z')))
    })

    it('reloads the choices when a chosen person no longer has access', async () => {
      a.auth.handoverOptions.mockResolvedValueOnce(options)
      a.auth.deleteAccount.mockRejectedValueOnce(new ApiError(422, 'invalid_handover', 'invalid_handover'))
      await open()
      await screen.findByText('Ava is shared with Mom, Dad.')
      fireEvent.click(screen.getByRole('radio', { name: 'Give it to Mom' }))
      a.auth.handoverOptions.mockResolvedValue([
        { diary_id: 'd1', diary_name: 'Ava', members: [{ user_id: 'm2', name: 'Dad' }] },
      ])
      fireEvent.click(screen.getByRole('button', { name: 'Delete my account' }))
      expect(
        await screen.findByText('Someone you chose no longer has access. Check your choices and try again.'),
      ).toBeInTheDocument()
      await waitFor(() => expect(screen.queryByRole('radio', { name: 'Give it to Mom' })).not.toBeInTheDocument())
      expect(screen.getByRole('radio', { name: 'Give it to Dad' })).toBeInTheDocument()
      expect(screen.getAllByRole('radio', { name: 'Delete it' })[0]).toBeChecked()
      expect(a.auth.handoverOptions).toHaveBeenCalledTimes(2)
    })

    it('sends nothing when every choice stays Delete it', async () => {
      a.auth.handoverOptions.mockResolvedValue(options)
      await open()
      await screen.findByText('Ava is shared with Mom, Dad.')
      fireEvent.click(screen.getByRole('button', { name: 'Delete my account' }))
      await waitFor(() => expect(a.auth.deleteAccount).toHaveBeenCalledWith(undefined))
    })

    it('keeps Delete disabled while loading and when loading fails', async () => {
      a.auth.handoverOptions.mockRejectedValue(new Error('boom'))
      await open()
      expect(screen.getByRole('button', { name: 'Delete my account' })).toBeDisabled()
      expect(await screen.findByText("Couldn't load your shared diaries. Try again.")).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Delete my account' })).toBeDisabled()
      expect(a.auth.deleteAccount).not.toHaveBeenCalled()
    })
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

  it('shows the granted access in plain words', async () => {
    a.integrations.list.mockResolvedValue([
      { provider: 'google', revoked: false, google_email: 'g@x.com', scopes_granted: ['calendar.readonly', 'photospicker.mediaitems.readonly'], photos: true },
    ])
    await renderPage()
    expect(await screen.findByText('Access: Calendar, Google Photos')).toBeInTheDocument()
  })

  it('shows Calendar only when photos are not granted', async () => {
    a.integrations.list.mockResolvedValue([
      { provider: 'google', revoked: false, google_email: 'g@x.com', scopes_granted: ['calendar.readonly', 'mystery'], photos: false },
    ])
    await renderPage()
    expect(await screen.findByText('Access: Calendar')).toBeInTheDocument()
    expect(screen.queryByText(/Google Photos|mystery/)).not.toBeInTheDocument()
  })

  it('says none yet when no known access is granted', async () => {
    a.integrations.list.mockResolvedValue([
      { provider: 'google', revoked: false, google_email: 'g@x.com', scopes_granted: [], photos: false },
    ])
    await renderPage()
    expect(await screen.findByText('Access: none yet')).toBeInTheDocument()
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

describe('ProfilePage, row 61: settings follow the signed-in user', () => {
  it('shows the saved unit and icon on first paint', async () => {
    mockUser = { id: 'u1', email: 'pat@example.com', temperature_unit: 'C', avatar: { kind: 'icon', name: 'panda' } }
    await renderPage()
    expect(screen.getByRole('radio', { name: '° C' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'panda' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('updates when the user is refreshed with new settings', async () => {
    const { rerender } = render(<ProfilePage />)
    await screen.findByText('Plus')
    expect(screen.getByRole('radio', { name: '° F' })).toBeChecked()
    mockUser = { id: 'u1', email: 'pat@example.com', temperature_unit: 'C', avatar: { kind: 'icon', name: 'panda' } }
    rerender(<ProfilePage />)
    expect(screen.getByRole('radio', { name: '° C' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'panda' })).toHaveAttribute('aria-pressed', 'true')
  })
})
