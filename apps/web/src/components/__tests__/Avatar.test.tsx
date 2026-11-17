import { render } from '@testing-library/react'
import Avatar from '@/components/Avatar'
import { TONES } from '@/lib/avatarIcons'

function root(c: HTMLElement) {
  return c.firstElementChild as HTMLElement
}

it('shows the photo', () => {
  const { container } = render(
    <Avatar avatar={{ kind: 'photo' }} email="andrew@example.com" photoUrl="blob:abc" size={34} />,
  )
  const img = container.querySelector('img')
  expect(img).toHaveAttribute('src', 'blob:abc')
  expect(root(container)).toHaveAttribute('data-icon', 'photo')
})

it('shows a tinted icon', () => {
  const { container } = render(
    <Avatar avatar={{ kind: 'icon', name: 'turtle' }} email="andrew@example.com" photoUrl={null} size={34} />,
  )
  expect(container.querySelector('svg')).not.toBeNull()
  expect(root(container)).toHaveAttribute('data-icon', 'turtle')
  expect(root(container).style.background).not.toBe('')
  expect(root(container).style.backgroundColor).toBe(hex(TONES[3].bg))
})

it('shows the initial on tone 2', () => {
  const { container } = render(
    <Avatar avatar={{ kind: 'initial' }} email="andrew@example.com" photoUrl={null} size={34} />,
  )
  expect(root(container)).toHaveTextContent('A')
  expect(root(container)).toHaveAttribute('data-icon', 'initial')
  expect(root(container).style.backgroundColor).toBe(hex(TONES[2].bg))
})

it('falls back to the initial when the avatar is missing or the photo has not loaded', () => {
  const a = render(<Avatar avatar={undefined} email="zed@example.com" photoUrl={null} size={30} />)
  expect(root(a.container)).toHaveTextContent('Z')
  const b = render(<Avatar avatar={{ kind: 'photo' }} email="zed@example.com" photoUrl={null} size={30} />)
  expect(root(b.container)).toHaveAttribute('data-icon', 'initial')
})

it('uses a placeholder when there is no email', () => {
  const { container } = render(
    <Avatar avatar={undefined} email={undefined as unknown as string} photoUrl={null} size={30} />,
  )
  expect(root(container)).toHaveTextContent('?')
})

function hex(h: string) {
  const n = parseInt(h.slice(1), 16)
  return `rgb(${n >> 16}, ${(n >> 8) & 255}, ${n & 255})`
}
