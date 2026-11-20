import { test, expect } from '@playwright/test'

// Tracker row 64: every page links the rainbow tab icon and the iPhone home-screen icon.
test('the login page links a tab icon and a home-screen icon that both load', async ({ page, request }) => {
  await page.goto('/login')

  const icon = page.locator('head link[rel="icon"][type="image/svg+xml"]')
  await expect(icon).toHaveCount(1)
  const iconRes = await request.get(new URL((await icon.getAttribute('href'))!, page.url()).href)
  expect(iconRes.status()).toBe(200)
  expect(iconRes.headers()['content-type']).toContain('image/svg+xml')

  const apple = page.locator('head link[rel="apple-touch-icon"]')
  await expect(apple).toHaveCount(1)
  const appleRes = await request.get(new URL((await apple.getAttribute('href'))!, page.url()).href)
  expect(appleRes.status()).toBe(200)
  expect(appleRes.headers()['content-type']).toContain('image/png')
})
