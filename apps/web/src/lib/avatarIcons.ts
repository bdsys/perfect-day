import {
  Cake, Bird, Cat, Dog, Fish, Flower, Moon, Panda, PawPrint, Rabbit, Rainbow, Rocket, Snail, Squirrel, Sun, Turtle,
  type LucideIcon,
} from 'lucide-react'
import type { AvatarIconName } from '@/lib/api'

export type Tone = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8

/** The 16 preset icons in display order, each with its fixed colour tone. */
export const AVATAR_ICONS: readonly { name: AvatarIconName; Icon: LucideIcon; tone: Tone }[] = [
  { name: 'cat', Icon: Cat, tone: 1 },
  { name: 'dog', Icon: Dog, tone: 4 },
  { name: 'bird', Icon: Bird, tone: 2 },
  { name: 'fish', Icon: Fish, tone: 7 },
  { name: 'rabbit', Icon: Rabbit, tone: 6 },
  { name: 'squirrel', Icon: Squirrel, tone: 1 },
  { name: 'turtle', Icon: Turtle, tone: 3 },
  { name: 'panda', Icon: Panda, tone: 8 },
  { name: 'snail', Icon: Snail, tone: 4 },
  { name: 'flower', Icon: Flower, tone: 6 },
  { name: 'sun', Icon: Sun, tone: 4 },
  { name: 'moon', Icon: Moon, tone: 5 },
  { name: 'rocket', Icon: Rocket, tone: 2 },
  { name: 'rainbow', Icon: Rainbow, tone: 5 },
  { name: 'cake', Icon: Cake, tone: 6 },
  { name: 'paw-print', Icon: PawPrint, tone: 3 },
]

export const TONES: Record<Tone, { bg: string; fg: string }> = {
  1: { bg: '#fde2d6', fg: '#9a3412' },
  2: { bg: '#dbeafe', fg: '#1e40af' },
  3: { bg: '#dcfce7', fg: '#166534' },
  4: { bg: '#fef3c7', fg: '#92400e' },
  5: { bg: '#ede9fe', fg: '#5b21b6' },
  6: { bg: '#fce7f3', fg: '#9d174d' },
  7: { bg: '#ccfbf1', fg: '#115e59' },
  8: { bg: '#e5e7eb', fg: '#374151' },
}

/** The default avatar (first letter of the email) uses this tone. */
export const INITIAL_TONE: Tone = 2
