# Adding photos to entries (PoC item 14)

Status: design, 2026-11-13. Replaces item 14's old plan ("Google Photos grant + scan").

## Why

Item 14 was meant to make photos arrive by themselves: the scan worker would read the parent's Google Photos library and attach photos taken during each entry's events. Google removed third-party library access on 31 March 2025 (<https://developers.google.com/photos/support/updates>), so that can't be built. Automatic photos now belong to the mobile app (`design/11-mobile-app.md`, M-1).

What a parent can still have on the web is **adding photos in seconds**. A calendar-made draft ("Haircut, 9:00") is already there; the parent opens it on their phone and adds photos from the event in a few taps. Today that takes several taps per photo, one photo at a time, from a button at the bottom of the entry page.

Andrew's photos come from two places: the last ~3 months are on his phone's camera roll; anything older is in Google Photos. Both must be quick.

## Goals

1. **One "Add photos" on every entry**, however it was made (scan, rule, calendar picker, by hand), easy to reach on a phone. It offers three sources:
   - **From this phone:** the device's own file picker, **several photos at once**. They upload, go into the parent's library, and attach to the entry.
   - **From Google Photos:** the Google Photos Picker. The first use asks the parent to approve access with Google, once. Picked photos are copied into the parent's library and attached.
   - **From your library:** photos the parent already uploaded, tap to attach. Photos taken near the entry's time come first.
2. **A photo is uploaded once** and belongs to the parent, across all their diaries; it can be attached to any entry without uploading again. (This is how the library already works, item 13.)
3. **Co-parents who can edit** add photos from *their own* library, camera roll or Google Photos, as attaching works today. Viewers can't add photos. This is also how photos from **other Google accounts** get in: a co-parent connects their own account (Andrew, 2026-11-13).
4. **Fix three review findings on the same path** (Andrew, 2026-11-13): S-5, S-7 and S-8 from `design/code-review-2026-11.md`. See "Security fixes folded in".

## Non-goals

- **Notifications** ("your draft is ready"). Push and email stay in item 20.
- **Automatic photos** of any kind (mobile app, M-1).
- **Videos.** Images only, as the library accepts today. A picked Google Photos video is skipped with a visible note.
- AI use of photos (captions, choosing the best ones): backlog AI-1. Adding photos does not change the draft's text.
- Tier limits on photos: item 18.
- Spotting the same photo uploaded twice from different sources (camera roll and Google Photos).
- **A second Google account per person.** Each person has one Google connection, used for Calendar and Photos alike (see "One Google account per person").

## Anti-goals

- **Never lose the calendar connection** by connecting Google Photos. They share one Google token per user (`oauth_tokens`); approving Photos must keep Calendar working, and declining Photos must change nothing.
- **No photo leaves the parent's control without a pick.** We only ever receive photos the parent chose.
- A failed photo must not silently vanish from a batch: the parent sees which ones didn't make it.

## Facts that shape this (Google Photos Picker API, read 2026-11-13)

| Fact | Source |
|---|---|
| Scope `https://www.googleapis.com/auth/photospicker.mediaitems.readonly`. The old `photoslibrary.readonly` is gone. | <https://developers.google.com/photos/support/updates>, <https://developers.google.com/photos/overview/authorization> |
| Flow: create a session → send the parent to its `pickerUri` → poll the session until `mediaItemsSet` → list the picked items → download bytes from each `baseUrl`. Append `/autoclose` to the `pickerUri` on the web so the tab closes when done. The `pickerUri` can't be shown in an iframe and must open in a browser signed in to the same Google account. | <https://developers.google.com/photos/picker/guides/get-started-picker>, <https://developers.google.com/photos/picker/reference/rest/v1/sessions> |
| Session polling: the session gives `pollingConfig.pollInterval` and `timeoutIn`, and an `expireTime`. Delete the session after the bytes are fetched. `pickingConfig.maxItemCount` is the only picking option; no date or album pre-filter. | sessions reference, <https://developers.google.com/photos/picker/guides/sessions> |
| A picked item has `id`, `createTime` (when the photo was taken, not uploaded), `type` (PHOTO/VIDEO) and `mediaFile` (`baseUrl`, `mimeType`, `filename`, width/height, camera make/model). **No location field.** | <https://developers.google.com/photos/picker/reference/rest/v1/mediaItems> |
| Download with `baseUrl` + `=d` keeps EXIF **except location**; requests need the OAuth bearer token; base URLs last **60 minutes**. | <https://developers.google.com/photos/picker/guides/media-items> |
| Apps using the Photos APIs must pass Google's OAuth verification; unverified apps show a warning. | authorization page |

Consequences:
- **Google Photos picks never set an entry's place** (item 24's photo source). The place falls to the event's address or the diary's home. Camera-roll uploads still can, if the browser keeps the photo's GPS.
- **We copy each picked photo into the parent's encrypted library straight away**, within the 60 minutes. After that it's an ordinary library photo.
- The picker opens on recent photos and can't be pointed at a date, so the panel shows the entry's date beside the Google Photos button ("Search Google Photos for 16 May 2026").
- Verification is needed before public launch, as for Calendar. The PoC runs unverified with Andrew's account.

## How it should behave

**Reaching it.** "Add photos" is a clear button near the top of the entry page for anyone who can edit, not at the bottom. The calendar picker and new-entry flows already land on the entry page, so they get it too.

**From this phone.**
- The file picker allows several images.
- Uploads run a few at a time, each with its own progress, through the existing upload path (`upload-url` → upload → `finalize`, encrypted at rest; `taken_at` and GPS read from EXIF).
- Each finished photo attaches to the entry. Failures are listed by file name with a "Try again".
- After the last one, the entry's place and weather refresh once (item 24 already queues this on attach).

**From Google Photos.**
- Not connected yet: the button starts Google's consent for the picker scope only. On return, the parent is back on the same entry and can pick.
- Connected: tapping opens the picker in a new tab (`pickerUri` + `/autoclose`). The tab must be opened in the tap itself, or phone browsers block it.
- Our page shows "Waiting for you to finish in Google Photos…" with Cancel, polling our API at Google's suggested interval until done or timed out.
- When the parent finishes, the server downloads each picked photo (`=d`), encrypts and stores it like an upload, and attaches it to the entry. `taken_at` comes from EXIF, else from `createTime`. Then it deletes the session.
- Videos are skipped, and the page says how many.
- Picking a Google photo that's already in the library (same `source` and Google item id, `photos.external_id`) reuses it instead of storing a copy.
- If Google says access was revoked, the panel says so and offers to reconnect.

**From your library.** The parent's photos not already on this entry, sorted with those taken closest to the entry's date first, then newest. Tap to attach; several can be attached without the panel closing.

**Removing.** As today: remove from the entry; the photo stays in the library.

**One Google account per person.** Calendar and Photos share one Google connection per person (`oauth_tokens`, one row per user and provider). Andrew's Calendar and Photos are on the same account (Andrew, 2026-11-13). If a person who already has Calendar connected approves Photos with a *different* Google account, the app refuses it and keeps the Calendar connection untouched, saying: "Use the Google account you connected for Calendar (name@…)". The consent request passes that account as a hint. On a phone, the picker opens in whatever Google account the browser is signed in to; if that's the wrong one, Google's own picker shows it, and our page says which account to use.

**HEIC display.** Chrome and Firefox can't show HEIC. Thumbnails are already JPEG; the full-size view isn't. Every HEIC/HEIF photo gets a stored JPEG display copy (encrypted like the original), served for the full-size view; existing HEIC photos get one too. The original is kept.

**When the parent has left the page.** On a phone, Google Photos takes over the screen and our tab may be closed. The server notices the finished pick and copies the photos itself. The outcome is kept and shown on the entry until the parent dismisses it, e.g. "12 photos added from Google Photos · 2 videos skipped · 1 couldn't be copied — pick it again from Google Photos". A failed Google photo isn't retried later (Google's links last an hour); the parent picks it again.

## Security fixes folded in

| Finding | Fix |
|---|---|
| **S-5** Google connect isn't tied to the browser that started it: someone could send a victim their connect link and get the victim's Google data on their own account. | When the connect link is issued, set a short-lived, HttpOnly cookie on the API's origin tied to the signed `state`; the callback refuses a mismatch or a missing cookie. Covers Calendar and Photos, since they share the flow. |
| **S-7** Phone uploads sit **unencrypted** in storage (`tmp/`) until finalized, up to 24 hours if never finalized. | Photo bytes go through the API, which encrypts them before storing, so nothing is ever stored unencrypted. Replaces the direct-to-storage upload URL. Cost: the API carries upload traffic (photos are capped at 50 MB, within Cloudflare's request limit). Unfinished uploads leave nothing behind. |
| **S-8** "Disconnect Google" only sets a flag: access isn't revoked at Google, and the encrypted tokens stay. | Disconnect revokes the grant at Google and deletes the stored tokens. One disconnect covers Calendar and Photos. If Google can't be reached, the tokens are still deleted and the failure logged. |

## Constraints

- Photos of children are sensitive: everything stored goes through the existing per-photo encryption. Google's bytes are never stored unencrypted, and never logged.
- Coordinates stay owner-only in responses (S-12). Event addresses stay owner-only (S-13).
- The Google token, refresh and revocation handling reuse the Calendar code (`oauth_tokens`, token refresh, `invalid_grant` marking it revoked, C-18), without breaking Calendar.
- Fetching picked photos must survive a slow phone connection closing the page: the server finishes the job once the parent has picked, not the browser.

## Builder's calls (decided here, change if wrong)

- Remove the dead `photoslibrary.readonly` scope from `routers/v1/integrations.py`; "photos" now means the picker scope. Stored scope strings are migrated.
- A Photos-only connection must not make the diary page say Calendar is disconnected (`services/calendar_connection.py`).
- Duplicate Google picks are matched per user (`photos.user_id`, `source`, `external_id`); the existing unique index is across all users and needs changing.
- After Google consent, the parent returns to the entry they started from (the return path rides in the signed `state`).
- Google photos are copied one at a time, to keep memory low on the NUC.
- Pick outcomes are stored in the database, not only Redis, because they must outlast the parent's visit.
- Picker sessions are short-lived and don't need a table if Redis holds them (session id → user, entry, expiry). The builder may choose a table if it makes the job and tests simpler.
- The fetch runs in a Celery task, queued when the session reports `mediaItemsSet`.
- `maxItemCount` 50 per pick; at most 3 uploads at a time from the phone.

## To check on a real phone before calling it done

1. **iPhone Safari and Android Chrome:** does the file picker allow several photos, and does an upload keep the photo's GPS? (Decides whether phone uploads set the entry's place.)
4. Google Cloud: the Picker API is enabled and the picker scope added to the OAuth consent screen (Andrew's setup step).
2. **Google Photos picker on a phone:** does the `pickerUri` open (Google Photos app or web), and does the parent get back to our tab afterwards?
3. HEIC from an iPhone uploads and shows correctly.

## Open

- Nothing. Andrew's answers of 2026-11-13 are in place: same Google account for Calendar and Photos; co-parents bring other accounts; S-5, S-7 and S-8 folded in. The rest of the review's open findings get one triage session with Andrew after item 14.
