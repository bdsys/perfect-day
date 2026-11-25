import fs from 'fs'
import path from 'path'

// The root layout wraps every page, /p/{token} among them, whose URL is the secret.
// Google's sign-in script would see that URL, so only the sign-in button loads it.
it('the root layout loads no third-party script', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'layout.tsx'), 'utf8')
  expect(src).not.toContain('accounts.google.com')
  expect(src).not.toContain('next/script')
})
