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

  // Clean up existing entries so we don't hit the free-tier manual entry
  // limit (5 entries) on repeated test runs.
  const entriesRes = await ctx.get(`${API}/v1/diaries/${sharedState.diaryId}/entries`, {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  if (entriesRes.ok()) {
    const entries: Array<{ id: string }> = await entriesRes.json();
    for (const entry of entries) {
      await ctx.delete(`${API}/v1/entries/${entry.id}`, {
        headers: { Authorization: `Bearer ${access_token}` },
      });
    }
  }

  // Clean up existing photos so the grid starts empty on each run.
  const photosRes = await ctx.get(`${API}/v1/photos`, {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  if (photosRes.ok()) {
    const photos: Array<{ id: string }> = await photosRes.json();
    for (const photo of photos) {
      await ctx.delete(`${API}/v1/photos/${photo.id}`, {
        headers: { Authorization: `Bearer ${access_token}` },
      });
    }
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

  test("delete photo from library → thumbnail disappears", async ({ page }) => {
    // Upload + delete in Docker can take ~40s; give this test extra budget.
    test.setTimeout(60_000);

    await loginViaUI(page);

    // Register the response waiter BEFORE navigating so we never miss the
    // initial GET /v1/photos that fires from the page's useEffect on mount.
    const initialLoad = page.waitForResponse(
      (r) => r.url().includes("/v1/photos") && r.request().method() === "GET" && r.ok(),
    );
    await page.goto("/photos");
    await expect(page.getByRole("heading", { name: /photo library/i })).toBeVisible();
    await initialLoad;

    // Upload one photo unconditionally so this test owns a known item regardless
    // of state left by sibling tests. Register the post-upload refetch waiter
    // BEFORE triggering the upload so we never miss handleUploaded's listForUser() call.
    const grid = page.locator("ul.photo-grid");
    const afterUpload = page.waitForResponse(
      (r) => r.url().includes("/v1/photos") && r.request().method() === "GET" && r.ok(),
    );
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.getByRole("button", { name: /upload photo/i }).click(),
    ]);
    await fileChooser.setFiles(FIXTURE);
    // Wait for the handleUploaded GET to settle the grid before reading any IDs.
    await afterUpload;
    await expect(grid.locator("li").first()).toBeVisible({ timeout: 5_000 });

    // Capture the identity of the first item now that the list is stable.
    const targetId = await grid.locator("li").first().getAttribute("data-photo-id");
    expect(targetId).toBeTruthy();

    // Pin the locator to the specific photo by ID — immune to any subsequent
    // re-renders that might reorder the grid between getAttribute and click.
    const targetItem = page.locator(`li[data-photo-id="${targetId}"]`);

    // Stub window.confirm to always return true — avoids any race between
    // registering a dialog event handler and the synchronous confirm() call
    // in the delete onClick handler.
    await page.evaluate(() => {
      window.confirm = () => true;
    });

    // Hover to reveal the action button, wait for it to be visible, then delete.
    await targetItem.hover();
    const deleteBtn = targetItem.getByRole("button", { name: "Delete photo" });
    await expect(deleteBtn).toBeVisible();
    await deleteBtn.click();

    // Assert that *this specific photo* is gone, not just that the count changed.
    await expect(targetItem).toHaveCount(0, { timeout: 15_000 });
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

  test("remove photo from entry → photo disappears from strip", async ({ page }) => {
    const { diaryId, token } = sharedState;

    // Create a fresh entry via the API.
    const ctx = await playwrightRequest.newContext();
    const entryRes = await ctx.post(`${API}/v1/diaries/${diaryId}/entries`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        entry_date: new Date().toISOString().slice(0, 10),
        title: `E2E Remove Photo ${Date.now()}`,
        body_markdown: "Test entry for photo remove.",
      },
    });
    const entry = await entryRes.json();
    await ctx.dispose();

    await loginViaUI(page);

    // Navigate to the entry and open the attach picker.
    await page.goto(`/entries/${entry.id}`);
    await page.waitForURL(`**/entries/${entry.id}`, { timeout: 10_000 });

    await page.getByRole("button", { name: /attach photo/i }).click();

    // Upload a new photo via the picker so it gets attached to this entry.
    const [fileChooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.getByRole("button", { name: /upload new/i }).click(),
    ]);
    await fileChooser.setFiles(FIXTURE);

    // Wait for the photo strip to appear with the attached thumbnail.
    const photosSection = page.locator("section").filter({ hasText: /photos/i });
    const strip = photosSection.locator("ul.photo-grid");
    await expect(strip.locator("li").first()).toBeVisible({ timeout: 20_000 });

    // Click the remove button on the attached photo (no confirm needed).
    const firstItem = strip.locator("li").first();
    await firstItem.hover();
    await firstItem.getByRole("button", { name: "Remove photo from entry" }).click();

    // The photos section should disappear once all photos are removed.
    await expect(photosSection).not.toBeVisible({ timeout: 10_000 });
  });
});
