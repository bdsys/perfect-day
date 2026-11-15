import type { ReactNode } from 'react'

/** Small muted text explaining a limit. Controls point at it with aria-describedby. */
export default function LimitNote({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <p className="limit-note" id={id}>
      {children}
    </p>
  )
}
