import { test, expect, request as playwrightRequest } from "@playwright/test";
import path from "node:path";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const email = "e2e-photos@example.com";
const password = "Password1!";
const diaryName = "E2E Photos Diary";

const FIXTURE = path.join(__dirname, "fixtures/sample.jpg");

const sharedState = {
  diaryId: "",
  token: "",
};

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
  sharedState.token = access_token;

  // List diaries — reuse existing one if present
  const listRes = await ctx.get(`${API}/v1/diaries`, {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  const diaries: Array<{ id: string; name: string }> = await listRes.json();
  const existing = diaries.find((d) => d.name === diaryName);

  if (existing) {
    sharedState.diaryId = existing.id;
  } else {
    const createRes = await ctx.post(`${API}/v1/diaries`, {
      headers: { Authorization: `Bearer ${access_token}` },
      data: { name: diaryName, timezone: "UTC" },
    });
    const created = await createRes.json();
    sharedState.diaryId = created.id;
  }

  await ctx.dispose();
});

/** Log in via the real login page and wait for redirect to /diaries. */
async function loginViaUI(page: import("@playwright/test").Page) {
  await page.goto("/login");
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.click("button[type=submit]");
  await page.waitForURL(/\/diaries/, { timeout: 10_000 });
}

test.describe("Photo library (/photos)", () => {
  test("upload → thumbnail appears → lightbox → escape", async ({ page }) => {
    await loginViaUI(page);

    // Navigate to the global photo library (not the old per-diary URL).
    await page.goto("/photos");
    await expect(page.getByRole("heading", { name: /photo library/i })).toBeVisible();

    // The upload button is now a real <button> that triggers a hidden file input.
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.getByRole("button", { name: /upload photo/i }).click(),
    ]);
    await fileChooser.setFiles(FIXTURE);

    // Wait for thumbnail — presigned-URL upload + finalize + decrypt + render.
    const thumb = page.locator('img[class*="thumbnail"]').first();
    await expect(thumb).toBeVisible({ timeout: 20_000 });

    // Open lightbox.
    await thumb.click();
    await expect(page.getByRole("dialog")).toBeVisible();

    // Escape closes it.
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).not.toBeVisible();
  });
});

test.describe("Inline upload from entry", () => {
  test("upload new photo via entry attach picker → photo attached to entry", async ({ page }) => {
    const { diaryId, token } = sharedState;

    // Create a fresh entry via the API so we have a clean starting state.
    const ctx = await playwrightRequest.newContext();
    const entryRes = await ctx.post(`${API}/v1/diaries/${diaryId}/entries`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        entry_date: new Date().toISOString().slice(0, 10),
        title: `E2E Inline Upload ${Date.now()}`,
        body_markdown: "Test entry for inline photo upload.",
      },
    });
    const entry = await entryRes.json();
    await ctx.dispose();

    await loginViaUI(page);

    // Navigate directly to the entry detail page.
    await page.goto(`/entries/${entry.id}`);
    await page.waitForURL(`**/entries/${entry.id}`, { timeout: 10_000 });

    // Open the attach picker.
    await page.getByRole("button", { name: /attach photo/i }).click();

    // The "Upload new" button inside the picker opens a file chooser.
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.getByRole("button", { name: /upload new/i }).click(),
    ]);
    await fileChooser.setFiles(FIXTURE);

    // After upload + finalize + attach, a thumbnail should appear in the entry's
    // attached photos strip.
    const attachedThumb = page.locator('img[class*="thumbnail"]').first();
    await expect(attachedThumb).toBeVisible({ timeout: 20_000 });
  });
});
