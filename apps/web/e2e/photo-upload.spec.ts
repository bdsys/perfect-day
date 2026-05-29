import { test, expect } from "@playwright/test";
import path from "node:path";

test("upload → attach → lightbox → escape", async ({ page }) => {
  // This test requires a running stack with valid credentials.
  // Skip gracefully when env vars are not set (local dev without stack up).
  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;
  const diaryId = process.env.E2E_DIARY_ID;

  if (!email || !password || !diaryId) {
    test.skip(true, "E2E_EMAIL, E2E_PASSWORD, E2E_DIARY_ID not set — skipping");
    return;
  }

  await page.goto("/login");
  await page.fill('input[name="email"]', email);
  await page.fill('input[name="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/diaries/, { timeout: 10_000 });

  // Navigate to diary photo library
  await page.goto(`/diaries/${diaryId}/photos`);
  await expect(page.getByRole("heading", { name: /photo library/i })).toBeVisible();

  // Upload a photo
  await page.locator('input[type="file"]').setInputFiles(
    path.join(__dirname, "fixtures/sample.jpg")
  );

  // Wait for thumbnail to appear (finalize + fetch)
  const thumb = page.locator('img[class*="thumbnail"]').first();
  await expect(thumb).toBeVisible({ timeout: 15_000 });

  // Open lightbox by clicking thumbnail
  await thumb.click();
  await expect(page.getByRole("dialog")).toBeVisible();

  // Escape closes lightbox
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
});
