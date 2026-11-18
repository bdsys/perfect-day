import type { CSSProperties } from 'react'
import { AVATAR_ICONS, INITIAL_TONE, TONES } from '@/lib/avatarIcons'
import type { AvatarChoice } from '@/lib/api'

interface Props {
  avatar: AvatarChoice | undefined
  email: string
  photoUrl: string | null
  size: number
}

/** The person's photo, preset icon, or (by default) the first letter of their email. */
export default function Avatar({ avatar, email, photoUrl, size }: Props) {
  const base: CSSProperties = { width: size, height: size, fontSize: Math.round(size * 0.45) }

  if (avatar?.kind === 'photo' && photoUrl) {
    return (
      <span className="avatar" data-icon="photo" style={base}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={photoUrl} alt="" width={size} height={size} />
      </span>
    )
  }

  const preset = avatar?.kind === 'icon' ? AVATAR_ICONS.find((i) => i.name === avatar.name) : undefined
  if (preset) {
    const tone = TONES[preset.tone]
    return (
      <span
        className="avatar"
        data-icon={preset.name}
        style={{ ...base, backgroundColor: tone.bg, color: tone.fg }}
      >
        <preset.Icon aria-hidden="true" size={Math.round(size * 0.56)} />
      </span>
    )
  }

  const tone = TONES[INITIAL_TONE]
  const letter = (email?.trim().charAt(0) || '?').toUpperCase()
  return (
    <span className="avatar" data-icon="initial" style={{ ...base, backgroundColor: tone.bg, color: tone.fg }}>
      {letter}
    </span>
  )
}
