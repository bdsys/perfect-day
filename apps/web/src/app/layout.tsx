import type { Metadata } from 'next'
import { AuthProvider } from '@/lib/auth-context'
import './globals.css'

export const metadata: Metadata = {
  title: 'Perfect Day',
  description: 'Your automated diary',
}

// No third-party script here: this layout wraps /p/{token}, whose URL is the secret.
// Google's sign-in script is loaded by GoogleSignInButton, on the pages that show it.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  )
}
