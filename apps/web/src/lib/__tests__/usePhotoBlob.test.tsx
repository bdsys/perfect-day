import { renderHook, act } from '@testing-library/react'
import { usePhotoBlob } from '../usePhotoBlob'
import { api } from '../api'

jest.mock('../api')
const get = api.photos.get as jest.Mock

function deferred() {
  let resolve!: (b: Blob) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<Blob>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const blob = () => new Blob([new Uint8Array([0xff])], { type: 'image/jpeg' })

let n = 0
beforeEach(() => {
  n = 0
  get.mockReset()
  jest.spyOn(URL, 'createObjectURL').mockClear().mockImplementation(() => `blob:${++n}`)
  jest.spyOn(URL, 'revokeObjectURL').mockClear().mockImplementation(() => {})
})

it('is empty while loading, then gives the object URL', async () => {
  const a = deferred()
  get.mockReturnValueOnce(a.promise)
  const { result } = renderHook(() => usePhotoBlob('a', 'thumb'))
  expect(result.current).toEqual({ src: null, error: false })
  expect(get).toHaveBeenCalledWith('a', 'thumb')
  await act(async () => { a.resolve(blob()) })
  expect(result.current).toEqual({ src: 'blob:1', error: false })
})

it("never shows the previous photo's URL after the photo changes, and revokes it", async () => {
  const a = deferred()
  const b = deferred()
  get.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
  const { result, rerender } = renderHook(({ id }) => usePhotoBlob(id, 'full'), { initialProps: { id: 'a' } })
  await act(async () => { a.resolve(blob()) })
  rerender({ id: 'b' })
  expect(result.current).toEqual({ src: null, error: false })
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:1')
  await act(async () => { b.resolve(blob()) })
  expect(result.current).toEqual({ src: 'blob:2', error: false })
})

it('an error clears as soon as the photo changes', async () => {
  const a = deferred()
  const b = deferred()
  get.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
  const { result, rerender } = renderHook(({ id }) => usePhotoBlob(id, 'full'), { initialProps: { id: 'a' } })
  await act(async () => { a.reject(new Error('nope')) })
  expect(result.current).toEqual({ src: null, error: true })
  rerender({ id: 'b' })
  expect(result.current).toEqual({ src: null, error: false })
})

it('a late answer for an earlier photo is ignored', async () => {
  const a = deferred()
  const b = deferred()
  get.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise)
  const { result, rerender } = renderHook(({ id }) => usePhotoBlob(id, 'full'), { initialProps: { id: 'a' } })
  rerender({ id: 'b' })
  await act(async () => { a.resolve(blob()) })
  expect(result.current).toEqual({ src: null, error: false })
  expect(URL.createObjectURL).not.toHaveBeenCalled()
  await act(async () => { b.resolve(blob()) })
  expect(result.current.src).toBe('blob:1')
})

it('revokes the URL on unmount', async () => {
  const a = deferred()
  get.mockReturnValueOnce(a.promise)
  const { unmount } = renderHook(() => usePhotoBlob('a', 'thumb'))
  await act(async () => { a.resolve(blob()) })
  unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:1')
})
