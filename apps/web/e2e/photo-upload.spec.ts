import { test, expect, request as playwrightRequest } from "@playwright/test";
import path from "node:path";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const email = "e2e-photos@example.com";
const password = "Password1!";
const diaryName = "E2E Photos Diary";

let diaryId: string;

test.beforeAll(async () => {
  const ctx = await playwrightRequest.newContext();

  // Register — 201 first run, 409 on re-runs, both fine
  await ctx.post(`${API}/v1/auth/register`, {
    data: { email, password },
  });

  // Login to get token
  const loginRes = await ctx.post(`${API}/v1/auth/login`, {
    data: { email, password },
  });
  const { access_token } = await loginRes.json();

  // List diaries — reuse existing one if present
  const listRes = await ctx.get(`${API}/v1/diaries`, {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  const diaries: Array<{ id: string; name: string }> = await listRes.json();
  const existing = diaries.find((d) => d.name === diaryName);

  if (existing) {
    diaryId = existing.id;
  } else {
    const createRes = await ctx.post(`${API}/v1/diaries`, {
      headers: { Authorization: `Bearer ${access_token}` },
      data: { name: diaryName, timezone: "UTC" },
    });
    const created = await createRes.json();
    diaryId = created.id;
  }

  await ctx.dispose();
});

test("upload → lightbox → escape", async ({ page }) => {
  await page.goto("/login");
  await page.fill('#email', email);
  await page.fill('#password', password);
  await page.click('button[type=submit]');
  await page.waitForURL(/\/diaries/, { timeout: 10_000 });

  await page.goto(`/diaries/${diaryId}/photos`);
  await expect(page.getByRole("heading", { name: /photo library/i })).toBeVisible();

  await page.locator('input[type="file"]').setInputFiles(
    path.join(__dirname, "fixtures/sample.jpg")
  );

  // Wait for thumbnail — finalize + decrypt + render takes a moment
  const thumb = page.locator('img[class*="thumbnail"]').first();
  await expect(thumb).toBeVisible({ timeout: 15_000 });

  // Open lightbox
  await thumb.click();
  await expect(page.getByRole("dialog")).toBeVisible();

  // Escape closes it
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
});
