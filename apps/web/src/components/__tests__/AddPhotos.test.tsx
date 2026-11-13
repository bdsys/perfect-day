import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { AddPhotos } from '../AddPhotos'
import { api, ApiError, type Entry, type Photo } from '../../lib/api'

jest.mock('../PhotoThumbnail', () => ({
  PhotoThumbnail: ({ photoId }: { photoId: string }) => <span data-testid={`lib-${photoId}`}>thumb {photoId}</span>,
}))
jest.mock('../../lib/api', () => {
  const actual = jest.requireActual('../../lib/api')
  return {
    ...actual,
    api: {
      entries: { get: jest.fn() },
      photos: { upload: jest.fn(), listForUser: jest.fn(), attachToEntry: jest.fn() },
    },
  }
})

const upload = api.photos.upload as jest.Mock
const listForUser = api.photos.listForUser as jest.Mock
const attach = api.photos.attachToEntry as jest.Mock
const getEntry = api.entries.get as jest.Mock

function photo(id: string): Photo {
  return { id, mime_type: 'image/jpeg', bytes: 1, taken_at: null, lat: null, lon: null, source: 'upload',
    finalized_at: '', created_at: '', deleted_at: null, has_thumbnail: true }
}
function entry(over: Partial<Entry> = {}): Entry {
  return {
    id: 'e1', diary_id: 'd1', entry_date: '2026-05-16', entry_end_date: null, title: 'T',
    body_markdown: 'B', body_source: 'user', flagged_tokens: null, status: 'draft',
    created_by: 'manual', creation_source: 'manual', published_at: null, deleted_at: null,
    created_at: '', updated_at: '', events: [], rule_matches: [], last_generation: null,
    photos: [], enrichments: [], suggestion: null, tz_override: null, can_edit: true, location: null,
    ...over,
  } as Entry
}
const file = (n: string) => new File(['x'], n, { type: 'image/jpeg' })

async function open(e = entry(), onChanged = jest.fn()) {
  const utils = render(<AddPhotos entry={e} onChanged={onChanged} />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /add photos/i })) })
  return { ...utils, onChanged }
}
const input = () => document.querySelector('input[type="file"]') as HTMLInputElement

beforeEach(() => {
  jest.resetAllMocks()
  listForUser.mockResolvedValue([])
  attach.mockResolvedValue(photo('x'))
  getEntry.mockResolvedValue(entry())
})

it('uploads several files three at a time', async () => {
  const resolvers: Array<(p: Photo) => void> = []
  let pending = 0
  let maxPending = 0
  upload.mockImplementation(() => new Promise<Photo>((res) => {
    pending++; maxPending = Math.max(maxPending, pending)
    resolvers.push((p) => { pending--; res(p) })
  }))
  await open()
  await act(async () => {
    fireEvent.change(input(), { target: { files: [1, 2, 3, 4, 5].map((n) => file(`f${n}.jpg`)) } })
  })
  expect(upload).toHaveBeenCalledTimes(3)
  await act(async () => { resolvers[0](photo('p1')) })
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(4))
  for (let i = 1; i < 5; i++) {
    await waitFor(() => expect(resolvers.length).toBeGreaterThan(i))
    await act(async () => { resolvers[i](photo(`p${i + 1}`)) })
  }
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(5))
  expect(maxPending).toBe(3)
  await waitFor(() => expect(attach).toHaveBeenCalledTimes(5))
  await waitFor(() => expect(getEntry).toHaveBeenCalledTimes(1))
})

it('a failed file shows its name and Try again re-uploads only it', async () => {
  upload.mockImplementation((f: File) =>
    f.name === 'bad.jpg' ? Promise.reject(new ApiError(415, 'unsupported_mime', 'unsupported_mime')) : Promise.resolve(photo('ok')))
  await open()
  await act(async () => {
    fireEvent.change(input(), { target: { files: [file('good.jpg'), file('bad.jpg')] } })
  })
  await screen.findByText("This file isn't a photo we can use.")
  expect(screen.queryByText(/unsupported_mime/)).toBeNull()
  expect(screen.getByText('bad.jpg')).toBeInTheDocument()
  expect(upload).toHaveBeenCalledTimes(2)
  upload.mockResolvedValue(photo('ok2'))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /try again/i })) })
  await waitFor(() => expect(upload).toHaveBeenCalledTimes(3))
  expect((upload.mock.calls[2][0] as File).name).toBe('bad.jpg')
})

it('library is asked for photos near the entry date and hides attached ones', async () => {
  listForUser.mockResolvedValue([photo('a'), photo('b')])
  await open(entry({ photos: [photo('a')] }))
  expect(listForUser).toHaveBeenCalledWith('2026-05-16')
  await screen.findByTestId('lib-b')
  expect(screen.queryByTestId('lib-a')).toBeNull()
})

it('attaching from the library keeps the panel open', async () => {
  listForUser.mockResolvedValue([photo('b')])
  const { onChanged } = await open()
  await act(async () => { fireEvent.click(await screen.findByRole('button', { name: 'Add this photo to the entry' })) })
  await waitFor(() => expect(attach).toHaveBeenCalledWith('e1', 'b'))
  await waitFor(() => expect(onChanged).toHaveBeenCalled())
  expect(screen.getByText(/from this phone/i)).toBeInTheDocument()
})

describe('readable upload errors', () => {
  const cases: Array<[string, unknown, string]> = [
    ['413 code', new ApiError(413, 'size_exceeds_limit', 'size_exceeds_limit'), 'This photo is over 50 MB.'],
    ['proxy 413', new ApiError(413, 'HTTP 413'), 'This photo is over 50 MB.'],
    ['415', new ApiError(415, 'unsupported_mime', 'unsupported_mime'), "This file isn't a photo we can use."],
    ['401', new ApiError(401, 'unauthorized'), "You've been signed out. Sign in again and try."],
    ['network', new Error('upload network error'), "The upload didn't finish. Check your connection and try again."],
    ['other', new Error('boom'), "Couldn't add this photo."],
  ]
  it.each(cases)('%s', async (_n, err, text) => {
    upload.mockRejectedValue(err)
    await open()
    await act(async () => { fireEvent.change(input(), { target: { files: [file('a.jpg')] } }) })
    expect(await screen.findByText(text)).toBeInTheDocument()
  })

  it('a file over 50 MB is refused without uploading', async () => {
    await open()
    const big = new File(['x'], 'big.jpg', { type: 'image/jpeg' })
    Object.defineProperty(big, 'size', { value: 52428801 })
    await act(async () => { fireEvent.change(input(), { target: { files: [big] } }) })
    expect(await screen.findByText('This photo is over 50 MB.')).toBeInTheDocument()
    expect(upload).not.toHaveBeenCalled()
  })
})

describe('shared upload pool', () => {
  it('two picks back-to-back never exceed 3 pending, and a failure mid-queue does not stall', async () => {
    const pendingRes: Array<{ name: string; ok: (p: Photo) => void; fail: (e: Error) => void }> = []
    let pending = 0
    let max = 0
    upload.mockImplementation((f: File) => new Promise<Photo>((ok, fail) => {
      pending++; max = Math.max(max, pending)
      pendingRes.push({ name: f.name, ok: (p) => { pending--; ok(p) }, fail: (e) => { pending--; fail(e) } })
    }))
    await open()
    await act(async () => { fireEvent.change(input(), { target: { files: ['a', 'b', 'c', 'd'].map((n) => file(n)) } }) })
    await act(async () => { fireEvent.change(input(), { target: { files: ['e', 'f'].map((n) => file(n)) } }) })
    expect(upload).toHaveBeenCalledTimes(3)
    await act(async () => { pendingRes[0].fail(new Error('boom')) })
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(4))
    for (let i = 1; i < 6; i++) {
      await waitFor(() => expect(pendingRes.length).toBeGreaterThan(i))
      await act(async () => { pendingRes[i].ok(photo(`p${i}`)) })
    }
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(6))
    expect(max).toBe(3)
    await waitFor(() => expect(getEntry).toHaveBeenCalledTimes(1))
  })

  it('Try again clicks share the same cap', async () => {
    let pending = 0
    let max = 0
    upload.mockRejectedValue(new Error('boom'))
    await open()
    await act(async () => { fireEvent.change(input(), { target: { files: ['a', 'b', 'c', 'd'].map((n) => file(n)) } }) })
    await screen.findAllByRole('button', { name: /try again/i })
    expect(screen.getAllByRole('button', { name: /try again/i })).toHaveLength(4)
    upload.mockImplementation(() => new Promise<Photo>(() => { pending++; max = Math.max(max, pending) }))
    await act(async () => {
      screen.getAllByRole('button', { name: /try again/i }).forEach((b) => fireEvent.click(b))
    })
    expect(max).toBe(3)
  })
})
