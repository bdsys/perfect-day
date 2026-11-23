import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import SharingPage from '../page'
import { api, ApiError } from '@/lib/api'

jest.mock('@/lib/api', () => {
  const actual = jest.requireActual('@/lib/api')
  return {
    ...actual,
    api: {
      diaries: { get: jest.fn() },
      auth: { myPlan: jest.fn() },
      sharing: {
        invite: jest.fn(),
        invitations: jest.fn(),
        revoke: jest.fn(),
        newLink: jest.fn(),
        members: jest.fn(),
        setRole: jest.fn(),
        remove: jest.fn(),
      },
    },
  }
})

jest.mock('@/lib/auth-context', () => {
  const value = { user: { id: 'u1', email: 'a@b.com' }, loading: false }
  return { useAuth: () => value }
})

const mockReplace = jest.fn()
const mockPush = jest.fn()
const mockRouter = { push: mockPush, replace: mockReplace }
jest.mock('next/navigation', () => ({
  useRouter: () => mockRouter,
  useParams: () => ({ diaryId: 'diary-1' }),
}))

const PLAN = { plan_name: 'Free', people_per_diary: 3, editors: false }
const DIARY = {
  id: 'diary-1', name: 'Emma diary', role: 'owner', owner_name: 'Me', people: { used: 2, limit: 3 },
}
const PENDING = {
  id: 'inv-1', invited_email: 'gran@x.com', role: 'viewer', status: 'pending',
  expires_at: '2026-11-29T12:00:00Z', token: null,
}
const EXPIRED = { ...PENDING, id: 'inv-2', invited_email: 'old@x.com', status: 'expired' }
const MEMBER = { user_id: 'u2', name: 'Mom', email: 'mom@x.com', role: 'viewer', effective_role: 'viewer' }

function setup(over: { diary?: object; plan?: object; invitations?: object[]; members?: object[] } = {}) {
  ;(api.diaries.get as jest.Mock).mockResolvedValue({ ...DIARY, ...over.diary })
  ;(api.auth.myPlan as jest.Mock).mockResolvedValue({ ...PLAN, ...over.plan })
  ;(api.sharing.invitations as jest.Mock).mockResolvedValue(over.invitations ?? [PENDING, EXPIRED])
  ;(api.sharing.members as jest.Mock).mockResolvedValue(over.members ?? [MEMBER])
}

beforeEach(() => {
  jest.clearAllMocks()
  setup()
})

describe('SharingPage', () => {
  it('shows the heading and counter', async () => {
    render(<SharingPage />)
    expect(await screen.findByRole('heading', { name: 'Sharing' })).toBeInTheDocument()
    expect(screen.getByText('Shared with 2 of 3 people')).toBeInTheDocument()
  })

  it('shows the counter without a limit', async () => {
    setup({ diary: { people: { used: 1, limit: null } }, plan: { people_per_diary: null, editors: true } })
    render(<SharingPage />)
    expect(await screen.findByText('Shared with 1 person')).toBeInTheDocument()
  })

  it('sends a non-owner to the diary', async () => {
    setup({ diary: { role: 'editor' } })
    render(<SharingPage />)
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/diaries/diary-1'))
  })

  it('replaces the form with the limit note at the limit', async () => {
    setup({ diary: { people: { used: 3, limit: 3 } } })
    render(<SharingPage />)
    expect(await screen.findByText('Free plan: 3 people per diary.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Upgrade/ })).toHaveAttribute('href', '/plans')
    expect(screen.queryByRole('button', { name: 'Make invite link' })).not.toBeInTheDocument()
  })

  it('hides the Role select off Family and shows it on Family', async () => {
    const { unmount } = render(<SharingPage />)
    await screen.findByLabelText('Email')
    expect(screen.queryByLabelText('Role')).not.toBeInTheDocument()
    unmount()
    setup({ plan: { plan_name: 'Family', people_per_diary: null, editors: true } })
    render(<SharingPage />)
    expect(await screen.findByLabelText('Role')).toBeInTheDocument()
  })

  it('makes an invite link and copies it', async () => {
    ;(api.sharing.invite as jest.Mock).mockResolvedValue({ ...PENDING, invited_email: 'new@x.com', token: 'tok123' })
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<SharingPage />)
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'new@x.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Make invite link' }))
    expect(await screen.findByText('Invite link for new@x.com. It works once, for 7 days.')).toBeInTheDocument()
    expect(api.sharing.invite).toHaveBeenCalledWith('diary-1', 'new@x.com', 'viewer')
    expect(api.sharing.invitations).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByRole('button', { name: 'Copy link' }))
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/invite/tok123`)
  })

  it('offers Share only when the browser can share', async () => {
    ;(api.sharing.invite as jest.Mock).mockResolvedValue({ ...PENDING, token: 'tok123' })
    const share = jest.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'share', { value: share, configurable: true })
    render(<SharingPage />)
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'gran@x.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Make invite link' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Share…' }))
    expect(share).toHaveBeenCalledWith({ url: `${window.location.origin}/invite/tok123` })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (navigator as any).share
  })

  it.each([
    [new ApiError(409, 'm', 'already_invited'), 'That person already has an invite. Use New link to send it again.'],
    [new ApiError(409, 'm', 'already_member'), 'That person already has access.'],
    [new ApiError(400, 'm', 'cannot_invite_self'), "That's your own address."],
    [new ApiError(403, 'm', 'tier_limit', { source: 'people', limit: 3 }), 'Free plan: 3 people per diary.'],
    [new ApiError(500, 'Boom'), 'Boom'],
  ])('shows the error copy', async (err, copy) => {
    ;(api.sharing.invite as jest.Mock).mockRejectedValue(err)
    render(<SharingPage />)
    fireEvent.change(await screen.findByLabelText('Email'), { target: { value: 'x@x.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'Make invite link' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(copy)
  })

  it('lists pending and expired invites; New link shows the link block', async () => {
    ;(api.sharing.newLink as jest.Mock).mockResolvedValue({ ...PENDING, token: 'fresh' })
    render(<SharingPage />)
    const row = (await screen.findByText(/gran@x\.com · Viewer · expires/)).closest('li') as HTMLElement
    expect(screen.getByText(/old@x\.com · Viewer · Expired/)).toBeInTheDocument()
    fireEvent.click(within(row).getByRole('button', { name: 'New link' }))
    expect(await screen.findByText('Invite link for gran@x.com. It works once, for 7 days.')).toBeInTheDocument()
    expect(api.sharing.newLink).toHaveBeenCalledWith('diary-1', 'inv-1')
  })

  it('Cancel invite revokes and refreshes', async () => {
    ;(api.sharing.revoke as jest.Mock).mockResolvedValue(undefined)
    render(<SharingPage />)
    const row = (await screen.findByText(/gran@x\.com · Viewer · expires/)).closest('li') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: 'Cancel invite' }))
    await waitFor(() => expect(api.sharing.revoke).toHaveBeenCalledWith('diary-1', 'inv-1'))
    await waitFor(() => expect(api.sharing.invitations).toHaveBeenCalledTimes(2))
  })

  it('Remove confirms, then removes the person', async () => {
    ;(api.sharing.remove as jest.Mock).mockResolvedValue(undefined)
    const confirm = jest.spyOn(window, 'confirm').mockReturnValue(true)
    render(<SharingPage />)
    expect(await screen.findByText('Mom · Viewer')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(confirm).toHaveBeenCalledWith("Remove Mom? They won't see Emma diary any more.")
    await waitFor(() => expect(api.sharing.remove).toHaveBeenCalledWith('diary-1', 'u2'))
    confirm.mockRestore()
  })

  it('on Family the role is a select that changes the role', async () => {
    setup({ plan: { plan_name: 'Family', people_per_diary: null, editors: true } })
    ;(api.sharing.setRole as jest.Mock).mockResolvedValue({ ...MEMBER, role: 'editor' })
    render(<SharingPage />)
    const select = await screen.findByLabelText('Role for Mom')
    fireEvent.change(select, { target: { value: 'editor' } })
    await waitFor(() => expect(api.sharing.setRole).toHaveBeenCalledWith('diary-1', 'u2', 'editor'))
  })

  it('shows "Editor — needs Family" for an editor who acts as a viewer', async () => {
    setup({ members: [{ ...MEMBER, role: 'editor', effective_role: 'viewer' }] })
    render(<SharingPage />)
    expect(await screen.findByText('Mom · Editor — needs Family')).toBeInTheDocument()
  })
})
