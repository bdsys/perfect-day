# Photos: real upload button, global user library, in-line upload from entry

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three connected UX fixes to the photo feature shipped in item #13: replace the text-styled "Upload photo" label with a real button, make the photo library user-global instead of per-diary, and allow inline upload from the entry-detail attach picker.

**Architecture:** No DB migration in this slice. The `photos` table is already user-scoped (`Photo.user_id`, no `diary_id`). The diary-attach UI is removed (`DiaryPhoto` join table model is left in place as deprecated dead code; a follow-up PR can drop the table). A new `GET /v1/photos` endpoint serves the user's library. The web app gains a global `/photos` page replacing the per-diary one, and the entry-detail picker reads from the global library and offers inline upload.

**Tech Stack:** FastAPI + SQLAlchemy 2.x async (existing `Photo` model, existing `_photo_out` helper) + Next.js App Router (existing `PhotoUploadButton`, `PhotoThumbnail`, `PhotoLightbox` components).

---

## Context

After item #13 shipped, three rough edges showed up while testing in the UI:

1. **The "Upload photo" trigger is a `<label>` wrapping a hidden `<input type=file>`** (`apps/web/src/components/PhotoUploadButton.tsx`). It looks like clickable text, not a button — visually inconsistent with every other action in the app, which uses `.btn .btn-primary` / `.btn-secondary`.

2. **Photos are presented as if they're scoped per-diary** even though the data model has them user-scoped. The DB `Photo` row has `user_id` only — there is no `photo.diary_id` (`apps/api/app/models/__init__.py:372-404`). The diary scoping comes from a `DiaryPhoto` join table and a `GET /v1/diaries/{id}/photos` endpoint (`apps/api/app/routers/v1/diaries.py:323-361`). For a single-user product where one person owns multiple diaries of (e.g.) their child's life, having "Diary A's photos" and "Diary B's photos" be separate libraries is friction — the user thinks of "my photos."

3. **Attaching a photo to an entry requires a round trip to upload first.** From `/entries/{entryId}`, the picker (`apps/web/src/app/entries/[entryId]/page.tsx:437-477`) only lists already-uploaded photos via `api.photos.listForDiary(entry.diary_id)`. To upload a new one the user must navigate away to `/diaries/{id}/photos`, upload, then navigate back.

User confirmed scope:
- Photo picker should show **all user photos, sorted by most-recent uploaded first**.
- **Drop diary-attach entirely** (`DiaryPhoto` join, `POST /diaries/{id}/photos`, `DELETE /diaries/{id}/photos/{photo_id}`, `GET /diaries/{id}/photos`). Entry-attach stays — that's the meaningful relationship.

## Already done — do not redo

- `apps/api/app/core/photo_crypto.py` — chunked AES-256-GCM (item #13).
- `apps/api/app/routers/v1/photos.py` — upload/finalize/get/delete/attach-to-entry endpoints + `_photo_out` helper. We extend this file with one new endpoint and remove two diary-attach endpoints.
- `apps/api/app/models/__init__.py:372-404` — `Photo` model, user-scoped at the DB layer.
- `apps/web/src/components/PhotoUploadButton.tsx` — current implementation works but is a `<label>` styled as text. We refactor this file in place.
- `apps/web/src/components/PhotoThumbnail.tsx`, `apps/web/src/components/PhotoLightbox.tsx` — reused as-is.
- S3 keys are already namespaced by user (`{user.id}/{photo_id}.enc`) — no migration needed.

---

## File Structure

**Backend (`apps/api/`):**
- `app/routers/v1/photos.py` — add `GET /photos`; remove `attach_photo_to_diary` + `detach_photo_from_diary`.
- `app/routers/v1/diaries.py` — remove `list_diary_photos`.
- `app/models/__init__.py` — leave `DiaryPhoto` class in place; add a one-line `# DEPRECATED:` comment above it.
- `tests/integration/test_photos.py` — add `GET /v1/photos` tests; remove diary-attach tests.
- `tests/integration/test_diaries.py` — remove `list_diary_photos` tests if any exist.

**Frontend (`apps/web/`):**
- `src/components/PhotoUploadButton.tsx` — refactor to `<button>` + ref-driven hidden input.
- `src/components/__tests__/PhotoUploadButton.test.tsx` — update selectors.
- `src/lib/api.ts` — add `photos.listForUser`; remove `photos.listForDiary`, `photos.attachToDiary`.
- `src/lib/__tests__/api.test.ts` — keep in sync.
- `src/app/photos/page.tsx` — **NEW** global photo library page.
- `src/app/diaries/[diaryId]/photos/page.tsx` — **DELETE**.
- `src/app/diaries/[diaryId]/page.tsx` — remove the per-diary "Photos" button (added in the prior turn at L437-442).
- `src/app/diaries/page.tsx` — add a global "Photos" button next to "Deleted diaries" (header at L99-101).
- `src/app/entries/[entryId]/page.tsx` — picker uses `listForUser`; inline `<PhotoUploadButton>` inside the picker; thumbnails use `.thumbnail` class.
- `src/app/globals.css` — add `.photo-grid` + `.thumbnail` classes (blue-border hover via `var(--accent)`).
- `tests/e2e/photos.spec.ts` (or whatever the existing photo spec is named) — migrate `/diaries/{id}/photos` → `/photos`; add inline-upload-from-entry assertion.

---

## Tasks

### Task 1: Backend — add `GET /v1/photos`

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Test: `apps/api/tests/integration/test_photos.py`

- [ ] **Step 1: Write the failing test**

In `apps/api/tests/integration/test_photos.py`, add tests covering:
1. User A uploads + finalizes 3 photos at staggered timestamps; `GET /v1/photos` returns them in `created_at DESC` order.
2. Cross-user isolation — user B's `GET /v1/photos` does not return user A's photos.
3. Excludes soft-deleted photos (`deleted_at IS NOT NULL`).
4. Excludes un-finalized photos (`finalized_at IS NULL`).

Reuse the existing helpers in that file for upload/finalize. Each test should hit `client.get("/v1/photos", headers=auth_headers(user))` and assert against the JSON array.

- [ ] **Step 2: Run tests to verify they fail**

```
cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -q -k "list_user_photos"
```

Expected: 4 failing tests with 404 (route not registered).

- [ ] **Step 3: Implement the endpoint**

In `apps/api/app/routers/v1/photos.py`, add:

```python
@router.get("/photos", response_model=list[PhotoOut])
async def list_user_photos(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PhotoOut]:
    q = (
        select(Photo)
        .where(
            Photo.user_id == user.id,
            Photo.deleted_at.is_(None),
            Photo.finalized_at.is_not(None),
        )
        .order_by(Photo.created_at.desc())
    )
    rows = (await db.execute(q)).scalars().all()
    return [_photo_out(p) for p in rows]
```

Reuse the existing `_photo_out` helper and `PhotoOut` schema in this file.

- [ ] **Step 4: Run tests to verify they pass**

```
cd apps/api && .venv/bin/pytest tests/integration/test_photos.py -q -k "list_user_photos"
```

Expected: 4 passing.

- [ ] **Step 5: Commit**

```
git add apps/api/app/routers/v1/photos.py apps/api/tests/integration/test_photos.py
git commit -m "feat(api): GET /v1/photos — user-global photo library"
```

---

### Task 2: Backend — remove diary-attach endpoints

**Files:**
- Modify: `apps/api/app/routers/v1/photos.py`
- Modify: `apps/api/app/routers/v1/diaries.py`
- Modify: `apps/api/app/models/__init__.py`
- Modify: `apps/api/tests/integration/test_photos.py`
- Modify: `apps/api/tests/integration/test_diaries.py`

- [ ] **Step 1: Remove `attach_photo_to_diary` (currently L296-331 of `photos.py`) and `detach_photo_from_diary` (L334-356).**

Also remove the imports they uniquely depended on if any (likely none — `DiaryPhoto`, `_get_diary_or_404` are still used elsewhere).

- [ ] **Step 2: Remove `list_diary_photos` from `apps/api/app/routers/v1/diaries.py` (L323-361).**

- [ ] **Step 3: Mark `DiaryPhoto` model as deprecated.**

In `apps/api/app/models/__init__.py`, add a one-line comment above the `DiaryPhoto` class declaration:

```python
# DEPRECATED: diary-photo attachments removed in 2026-05-29. Drop table in follow-up migration.
class DiaryPhoto(Base):
    ...
```

Do **not** remove the class — keeping it preserves the alembic baseline. Do not write a migration in this slice.

- [ ] **Step 4: Remove tests covering the deleted endpoints.**

Grep for and delete:
```
grep -rn "attach_photo_to_diary\|detach_photo_from_diary\|list_diary_photos" apps/api/tests/
```

Likely matches in `apps/api/tests/integration/test_photos.py` (any test around `POST /v1/diaries/{id}/photos`, `DELETE /v1/diaries/{id}/photos/{photo_id}`) and `apps/api/tests/integration/test_diaries.py` (any test around `GET /v1/diaries/{id}/photos`). Delete those test functions.

- [ ] **Step 5: Run integration tests**

```
cd apps/api && .venv/bin/pytest tests/integration/test_photos.py tests/integration/test_diaries.py -q
```

Expected: all green; previously-passing diary-attach tests are gone, not failing.

- [ ] **Step 6: Commit**

```
git add apps/api/app/routers/v1/photos.py apps/api/app/routers/v1/diaries.py apps/api/app/models/__init__.py apps/api/tests/integration/
git commit -m "refactor(api): drop diary-photo attach (now user-global)"
```

---

### Task 3: Frontend — refactor `PhotoUploadButton` to a real button

**Files:**
- Modify: `apps/web/src/components/PhotoUploadButton.tsx`
- Modify: `apps/web/src/components/__tests__/PhotoUploadButton.test.tsx`

- [ ] **Step 1: Update tests first**

In `apps/web/src/components/__tests__/PhotoUploadButton.test.tsx`, update selectors:
- Replace `getByLabelText("Upload photo")` with `getByRole("button", { name: /upload photo/i })`.
- For triggering file selection, query the hidden input with `container.querySelector('input[type="file"]')` and use `fireEvent.change(input, { target: { files: [file] } })`.
- Add a test: clicking the button programmatically clicks the hidden input (mock `HTMLInputElement.prototype.click` and assert it was called).
- Add a test: when `label="Upload new"` prop is passed, the button shows "Upload new" instead of "Upload photo".

- [ ] **Step 2: Run tests to confirm they fail**

```
cd apps/web && npm test -- PhotoUploadButton
```

Expected: failures on label-based queries / missing `label` prop.

- [ ] **Step 3: Refactor the component**

Replace the entire body of `apps/web/src/components/PhotoUploadButton.tsx` with:

```tsx
"use client";
import { useRef, useState } from "react";
import { api, Photo } from "../lib/api";

interface Props {
  onUploaded: (photo: Photo) => void;
  className?: string;
  label?: string;
}

export function PhotoUploadButton({ onUploaded, className, label = "Upload photo" }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = progress !== null;

  async function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setProgress(0);
    try {
      const meta = await api.photos.requestUploadUrl({
        declared_mime: file.type,
        declared_size: file.size,
      });
      await api.photos.uploadFile(meta.upload_url, file, setProgress);
      const photo = await api.photos.finalize(meta.photo_id);
      onUploaded(photo);
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      e.target.value = "";
      setProgress(null);
    }
  }

  return (
    <>
      <button
        type="button"
        className={className ?? "btn btn-primary"}
        onClick={() => inputRef.current?.click()}
        disabled={busy}
      >
        {busy ? `Uploading… ${Math.round((progress ?? 0) * 100)}%` : label}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        onChange={handleChange}
        style={{ display: "none" }}
      />
      {error && (
        <span role="alert" style={{ marginLeft: "0.5rem", color: "var(--error)" }}>
          {error}
        </span>
      )}
    </>
  );
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```
cd apps/web && npm test -- PhotoUploadButton
```

Expected: all green.

- [ ] **Step 5: Commit**

```
git add apps/web/src/components/PhotoUploadButton.tsx apps/web/src/components/__tests__/PhotoUploadButton.test.tsx
git commit -m "refactor(web): PhotoUploadButton uses a real button + ref-driven input"
```

---

### Task 4: Frontend — API client surface

**Files:**
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/lib/__tests__/api.test.ts`

- [ ] **Step 1: Update API client tests first**

In `apps/web/src/lib/__tests__/api.test.ts`:
- Add a test for `api.photos.listForUser()` that mocks `fetch` and asserts a `GET /v1/photos` request with bearer token, then asserts the parsed response is an array of `Photo`.
- Remove tests for `api.photos.listForDiary` and `api.photos.attachToDiary`.

- [ ] **Step 2: Run tests to confirm they fail**

```
cd apps/web && npm test -- api.test
```

Expected: missing `listForUser` symbol → fail.

- [ ] **Step 3: Update `apps/web/src/lib/api.ts`**

Inside the `photos` namespace (currently L548-599):
- **Add**:
  ```ts
  async listForUser(): Promise<Photo[]> {
    return apiFetch<Photo[]>("/v1/photos");
  },
  ```
- **Remove** `listForDiary` and `attachToDiary` (the two methods will have one call site each, both updated below in Tasks 5 and 7).

- [ ] **Step 4: Run tests**

```
cd apps/web && npm test -- api.test
```

Expected: green.

- [ ] **Step 5: Commit**

```
git add apps/web/src/lib/api.ts apps/web/src/lib/__tests__/api.test.ts
git commit -m "refactor(web): photos API — add listForUser, remove diary-attach methods"
```

---

### Task 5: Frontend — create global `/photos` page

**Files:**
- Create: `apps/web/src/app/photos/page.tsx`
- Delete: `apps/web/src/app/diaries/[diaryId]/photos/page.tsx`

- [ ] **Step 1: Create `apps/web/src/app/photos/page.tsx`**

```tsx
"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, Photo } from "@/lib/api";
import { PhotoThumbnail } from "@/components/PhotoThumbnail";
import { PhotoUploadButton } from "@/components/PhotoUploadButton";
import { PhotoLightbox } from "@/components/PhotoLightbox";

export default function UserPhotosPage() {
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.photos
      .listForUser()
      .then((p) => {
        if (!cancelled) setPhotos(p);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load photos");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleUploaded(p: Photo) {
    // Photo is already a user-owned row at finalize time; just refresh the list.
    try {
      const refreshed = await api.photos.listForUser();
      setPhotos(refreshed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to refresh photos");
    }
    void p;
  }

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <Link href="/diaries" className="nav-brand">
            ← Diaries
          </Link>
        </div>
      </nav>
      <main className="container" style={{ paddingTop: "1.5rem" }}>
        <h1>Photo library</h1>
        {error && <p role="alert">{error}</p>}
        <PhotoUploadButton onUploaded={handleUploaded} />
        <ul
          className="grid"
          style={{
            listStyle: "none",
            padding: 0,
            display: "flex",
            flexWrap: "wrap",
            gap: "8px",
            marginTop: "1rem",
          }}
        >
          {photos.map((p, i) => (
            <li key={p.id}>
              <PhotoThumbnail
                photoId={p.id}
                alt=""
                onClick={() => setOpenIndex(i)}
                className="thumbnail"
              />
            </li>
          ))}
        </ul>
        {openIndex !== null && (
          <PhotoLightbox
            photoIds={photos.map((p) => p.id)}
            index={openIndex}
            onIndexChange={setOpenIndex}
            onClose={() => setOpenIndex(null)}
          />
        )}
      </main>
    </>
  );
}
```

- [ ] **Step 2: Delete the per-diary photos page**

```
rm apps/web/src/app/diaries/[diaryId]/photos/page.tsx
```

- [ ] **Step 3: Run typecheck + lint**

```
cd apps/web && npx tsc --noEmit && npx eslint src --max-warnings 0
```

Expected: green. (If lint flags `void p;` rewrite the handler signature.)

- [ ] **Step 4: Commit**

```
git add apps/web/src/app/photos/page.tsx apps/web/src/app/diaries/[diaryId]/photos/page.tsx
git commit -m "feat(web): /photos global library; drop /diaries/[id]/photos"
```

---

### Task 6: Frontend — wire navigation buttons

**Files:**
- Modify: `apps/web/src/app/diaries/page.tsx`
- Modify: `apps/web/src/app/diaries/[diaryId]/page.tsx`

- [ ] **Step 1: Add global "Photos" button to `/diaries` header**

In `apps/web/src/app/diaries/page.tsx`, find the header actions block (around L99-101 with "Deleted diaries"). Add a sibling button:

```tsx
<button
  className="btn btn-secondary"
  onClick={() => router.push("/photos")}
>
  Photos
</button>
```

Place it before the "Deleted diaries" button so the order is: New diary | Photos | Deleted diaries. (Confirm `useRouter` is already imported and a `router` is available; if the page uses `<Link>` for navigation instead, use `<Link href="/photos" className="btn btn-secondary">Photos</Link>` to match existing pattern.)

- [ ] **Step 2: Remove the per-diary Photos button**

In `apps/web/src/app/diaries/[diaryId]/page.tsx`, find the button added in the previous session (around L437-442):

```tsx
<button
  className="btn btn-secondary"
  onClick={() => router.push(`/diaries/${diaryId}/photos`)}
>
  Photos
</button>
```

Delete this block. Leave the adjacent "Auto-Creation Rules" button untouched.

- [ ] **Step 3: Run typecheck + lint + the relevant tests**

```
cd apps/web && npx tsc --noEmit && npx eslint src --max-warnings 0
cd apps/web && npm test
```

Expected: green.

- [ ] **Step 4: Commit**

```
git add apps/web/src/app/diaries/page.tsx apps/web/src/app/diaries/[diaryId]/page.tsx
git commit -m "feat(web): move Photos entry-point from diary page to /diaries header"
```

---

### Task 7: Frontend — entry-detail picker uses global library + inline upload

**File:**
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`

- [ ] **Step 1: Switch the picker source from `listForDiary` to `listForUser`**

Find the call site that loads picker library (currently calls `api.photos.listForDiary(entry.diary_id)`, around L442). Replace with:

```tsx
const lib = await api.photos.listForUser();
const attachedIds = new Set(entry.photos?.map((p) => p.id) ?? []);
setLibraryPhotos(lib.filter((p) => !attachedIds.has(p.id)));
```

(Library entries from `listForUser` are already finalized — drop any redundant `finalized_at != null` filter that was passed before.)

- [ ] **Step 2: Add an inline upload button inside the picker**

Inside the `{showAttachPicker && (...)}` block (currently around L437-477), add a row above the thumbnail strip:

```tsx
<div style={{ marginBottom: "0.5rem" }}>
  <PhotoUploadButton onUploaded={handlePickerUpload} label="Upload new" />
</div>
```

Import `PhotoUploadButton` at the top of the file: `import { PhotoUploadButton } from "@/components/PhotoUploadButton";` (only if not already imported).

- [ ] **Step 3: Add the `handlePickerUpload` handler**

Inside the component, alongside the other handlers:

```tsx
async function handlePickerUpload(p: Photo) {
  try {
    await api.photos.attachToEntry(entry.id, p.id);
    const refreshed = await api.entries.get(entry.id);
    setEntry(refreshed);
    const lib = await api.photos.listForUser();
    const attachedIds = new Set(refreshed.photos?.map((ph) => ph.id) ?? []);
    setLibraryPhotos(lib.filter((ph) => !attachedIds.has(ph.id)));
  } catch (e: unknown) {
    setError(e instanceof Error ? e.message : "Failed to attach uploaded photo");
  }
}
```

- [ ] **Step 4: Typecheck, lint, run unit tests**

```
cd apps/web && npx tsc --noEmit && npx eslint src --max-warnings 0
cd apps/web && npm test
```

Expected: green.

- [ ] **Step 5: Commit**

```
git add apps/web/src/app/entries/[entryId]/page.tsx
git commit -m "feat(web): entry picker uses global photo library + inline upload"
```

---

### Task 8: Visual polish — blue-border hover on photo thumbnails

**Files:**
- Modify: `apps/web/src/app/globals.css`
- Modify: `apps/web/src/app/photos/page.tsx` (created in Task 5)
- Modify: `apps/web/src/app/entries/[entryId]/page.tsx`

**Goal:** Make the global photo library and the entry-attach picker feel like a real grid of clickable images. Thumbnails get a 2px transparent border at rest and the theme blue (`var(--accent)`, `#2563eb`) on hover, with a soft lift (subtle scale + shadow) and `cursor: pointer`.

The existing `.thumbnail` class is referenced from `PhotoThumbnail` usage sites but not defined in `globals.css` (verified by grep). We define it here once and reuse it.

- [ ] **Step 1: Add `.thumbnail` and `.photo-grid` styles to `globals.css`**

Append to `apps/web/src/app/globals.css`:

```css
/* Photo grid + hover-able thumbnails */
.photo-grid {
  list-style: none;
  padding: 0;
  margin: 1rem 0 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 8px;
}

.photo-grid li {
  margin: 0;
}

.thumbnail {
  display: block;
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  border: 2px solid transparent;
  border-radius: 6px;
  background: #f3f4f6;
  cursor: pointer;
  transition: border-color 120ms ease, transform 120ms ease, box-shadow 120ms ease;
}

.thumbnail:hover,
.thumbnail:focus-visible {
  border-color: var(--accent);
  transform: scale(1.02);
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.2);
  outline: none;
}
```

(`var(--accent)` is the existing blue `#2563eb` defined at the top of `globals.css`.)

- [ ] **Step 2: Update `/photos` page to use `.photo-grid`**

In `apps/web/src/app/photos/page.tsx` (created in Task 5), replace the inline-styled `<ul>` with:

```tsx
<ul className="photo-grid">
  {photos.map((p, i) => (
    <li key={p.id}>
      <PhotoThumbnail
        photoId={p.id}
        alt=""
        onClick={() => setOpenIndex(i)}
        className="thumbnail"
      />
    </li>
  ))}
</ul>
```

Drop the `style={{ ... }}` prop from the `<ul>` — the class handles layout now.

- [ ] **Step 3: Apply `.photo-grid` to the entry-detail attach picker**

In `apps/web/src/app/entries/[entryId]/page.tsx`, find the picker thumbnail strip (inside `{showAttachPicker && (...)}`). Wrap the picker thumbnails in `<ul className="photo-grid">` (or apply the class to the existing list element). Ensure each thumbnail uses `className="thumbnail"`.

Also apply the same class to the *attached* thumbnails list on the entry detail page so attached and pickable photos share the same visual treatment.

If the picker currently uses a flexbox row with horizontal scroll for its design, keep that layout — but still apply `.thumbnail` to each `<PhotoThumbnail>` so the hover border and lift work. Only the *grid container* class is optional; the *thumbnail item* class is required for hover styling.

- [ ] **Step 4: Verify visually**

```
make infra
# In separate terminal:
cd apps/web && npm run dev
# In browser, sign in. Visit /photos. Hover a thumbnail.
# Open an entry, click "Attach photo". Hover a picker thumbnail.
```

Expected: 2px blue border + subtle scale on hover. No layout shift on hover (border is always 2px, transparent at rest).

- [ ] **Step 5: Lint + typecheck**

```
cd apps/web && npx tsc --noEmit && npx eslint src --max-warnings 0
```

- [ ] **Step 6: Commit**

```
git add apps/web/src/app/globals.css apps/web/src/app/photos/page.tsx apps/web/src/app/entries/[entryId]/page.tsx
git commit -m "feat(web): photo grid + blue-border hover on thumbnails"
```

---

### Task 9: Playwright e2e migration

**Files:**
- Modify: existing photo-upload spec under `apps/web/tests/e2e/` (grep for `/diaries/${...}/photos` to find it).

- [ ] **Step 1: Locate the existing photo spec**

```
grep -rn "diaries.*photos" apps/web/tests/e2e/
```

- [ ] **Step 2: Update navigation**

Replace `await page.goto('/diaries/{id}/photos')` (or whatever pattern is used) with `await page.goto('/photos')`. Remove any per-diary navigation step. Photos library is now reached via the "Photos" button on `/diaries`.

- [ ] **Step 3: Add inline-upload-from-entry assertion**

Append to the same spec (or add a new one) a flow that:
1. Logs in.
2. Creates / opens a diary and entry.
3. Clicks "Attach photo" on the entry detail page.
4. Inside the picker, clicks "Upload new" (the inline upload button).
5. Uploads a fixture image.
6. Asserts the photo appears as attached to the entry (e.g., a thumbnail with the new photo's ID is rendered in the entry's attached-photos list).

Reuse the fixture and test-helper patterns from the existing spec — do not reinvent the upload flow. The existing PhotoUploadButton test for the picker should already drive the same `handleChange` path.

- [ ] **Step 4: Run e2e**

```
make test-e2e
```

Expected: green.

- [ ] **Step 5: Commit**

```
git add apps/web/tests/e2e/
git commit -m "test(e2e): migrate photo flow to /photos + inline-upload-from-entry"
```

---

## Critical files

- `apps/api/app/routers/v1/photos.py` — add `GET /photos`; remove diary-attach endpoints.
- `apps/api/app/routers/v1/diaries.py` — remove `list_diary_photos`.
- `apps/api/app/models/__init__.py` — comment `DiaryPhoto` as deprecated.
- `apps/api/tests/integration/test_photos.py` — add user-list tests; drop diary-attach tests.
- `apps/web/src/components/PhotoUploadButton.tsx` — real button, hidden input via ref.
- `apps/web/src/components/__tests__/PhotoUploadButton.test.tsx` — selector update.
- `apps/web/src/app/photos/page.tsx` — **new** global photo library page.
- `apps/web/src/app/diaries/[diaryId]/photos/page.tsx` — **delete**.
- `apps/web/src/app/diaries/[diaryId]/page.tsx` — remove the per-diary "Photos" button.
- `apps/web/src/app/diaries/page.tsx` — add global "Photos" button to header.
- `apps/web/src/app/entries/[entryId]/page.tsx` — picker uses `listForUser`; inline upload button; thumbnails use `.thumbnail` class.
- `apps/web/src/app/globals.css` — `.photo-grid` + `.thumbnail` styles with `var(--accent)` blue hover border.
- `apps/web/src/lib/api.ts` — add `photos.listForUser`; remove `listForDiary` / `attachToDiary`.
- `apps/web/src/lib/__tests__/api.test.ts` — keep in sync.

## Reused utilities

- `_photo_out` helper in `apps/api/app/routers/v1/photos.py`.
- `PhotoOut` schema in `apps/api/app/routers/v1/photos.py`.
- Existing `Photo` model and S3 key namespacing (`{user.id}/{photo_id}.enc`) — already user-scoped, no migration.
- `app.css` button classes (`btn btn-primary`, `btn btn-secondary`) already used app-wide.
- `PhotoThumbnail`, `PhotoLightbox` components reused as-is in the new `/photos` page.

## Out of scope

- Dropping the `diary_photos` table itself. Leave the model class with a deprecated comment; write the migration in a follow-up cleanup PR once we're confident nothing reads it.
- `Diary.cover_photo_id` (`apps/api/app/models/__init__.py:172`) — currently unused in routes; keep schema as-is.
- Photo-album / collection grouping. The user library is a flat reverse-chronological list for this slice.
- Pagination on `GET /v1/photos`. Free tier caps total photos low enough that an unbounded list is fine for now; revisit when paid tiers exist or library size warrants it.
- Reorganizing the entry-detail picker UI beyond inserting the upload button (no thumbnail-grid redesign here).

## Verification

1. **API integration:**
   ```
   cd apps/api && .venv/bin/pytest tests/integration/test_photos.py tests/integration/test_diaries.py -q
   ```
   New `GET /v1/photos` tests pass; removed-endpoint tests are deleted (not failing). Cross-user 404 isolation still asserted by existing photo tests.

2. **Lint + types:**
   ```
   make lint
   make typecheck
   ```

3. **Web unit tests:**
   ```
   cd apps/web && npm test
   ```
   Adjusted `PhotoUploadButton.test.tsx` and `api.test.ts` pass.

4. **Manual UI walkthrough (against `make infra` + local api/web):**
   - Sign in.
   - On `/diaries`, see new "Photos" button in header. Click → land on `/photos`. The "Upload photo" trigger is a styled button.
   - Upload a photo. It appears in the grid. Refresh — still there.
   - **Hover a thumbnail in the `/photos` grid → it gets a 2px blue border and subtly scales up.**
   - Open a diary, open or create an entry. Confirm there is no longer a per-diary "Photos" button on the diary page header.
   - In the entry, click "Attach photo". The strip shows the photo just uploaded on `/photos` (i.e., user-global, not diary-scoped). **Hover a picker thumbnail → same blue-border hover treatment.** Click it → attached.
   - Detach it.
   - Click "Attach photo" again → "Upload new" button is visible inside the picker. Click → upload a different photo → it auto-attaches to the entry.
   - Open a *second* diary, create an entry there. Open the picker — both photos uploaded above are visible (proving global scope).

5. **Playwright e2e:**
   ```
   make test-e2e
   ```
   The migrated spec (`/photos` instead of `/diaries/{id}/photos`) plus the new in-picker-upload step pass.

6. **Full pipeline:**
   ```
   make test-all
   ```
   Green end to end.
