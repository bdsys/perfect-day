# Mobile app (Expo)

Status: **not started.** The PoC deferred mobile until the web app is stable (`09-poc-scope.md`). This document collects what the mobile app must do so the requirements aren't lost. Start here when mobile is scheduled.

## Why the mobile app matters more now (2026-11-13)

Automatic photos were meant to come from Google Photos: a worker would read the parent's library every scan and attach photos taken during each entry's events (`05-google-oauth-integrations.md`, PoC item 14). **Google removed that access on 31 March 2025.**

- The full-library scopes (`photoslibrary.readonly`, `photoslibrary.sharing`, `photoslibrary`) were removed. An app can now "only list, search, and retrieve albums and media items that were created by your app." Source: <https://developers.google.com/photos/support/updates>
- The replacement is the Google Photos **Picker API**. The parent opens Google Photos from our app, picks photos, and we get only those. Each new batch needs a new picking session the parent opens. Source: <https://developers.google.com/photos/picker/guides/get-started-picker>, <https://developers.google.com/photos/picker/guides/picking-experience>

So the web app can't get a parent's photos without the parent picking them each time. Reading the photo library on the parent's own phone is the only route we know of to photos that arrive without the parent picking them.

**Decision (Andrew, 2026-11-13):**
- **Now:** PoC item 14 becomes fast photo adding on drafts. A parent sees an auto-created draft ("Haircut, 9:00") and adds photos from their phone in a few taps, with the Google Photos Picker as a possible second button.
- **Later:** automatic photos come from the mobile app (M-1 below).

## Features

### M-1: Automatic photos from the phone's camera roll

The core feature this app exists for.

- With the parent's permission, the app reads the phone's photo library: each photo's time taken and, where present, location.
- It matches photos to the diary's calendar-made entries by time (the event's start to end, with some margin), and suggests or attaches them.
- **Only matching photos leave the phone.** Unmatched photos are never uploaded. This is better for privacy than the old Google Photos plan, which read the whole library.
- Uploads go through the existing photo flow (item 13: `upload-url`, `finalize`, per-photo encryption at rest).
- A photo's GPS feeds the entry's place, as item 24 already does for uploaded photos.

Questions to settle in its design:
- Attach automatically, or suggest and let the parent confirm? Child photos are sensitive; a wrong photo on a shared diary is worse than a missing one.
- How wide the time margin around an event is.
- Which photos to skip: screenshots, videos, burst duplicates.
- Several parents on one diary, each with their own phone.

**Not verified yet:** the platform limits below are from general knowledge, not checked against current docs. Check them before designing M-1.
- iOS lets a person grant access to selected photos only ("limited access"), and the app sees only those.
- iOS gives apps little background time. Matching would mostly run when the app is opened, plus occasional background runs the system schedules.
- Android needs a separate permission to read photo locations.
- Expo's `expo-media-library` is the likely library: it lists photos with time filters and returns location per photo.

### Other mobile items already recorded elsewhere

| Item | Where |
|---|---|
| Tokens in `expo-secure-store`, same JWT scheme as web | `01-architecture.md` |
| Expo push notifications (draft ready, and others) | `07-notifications.md` |
| C-2 handwriting and drawing entries | `docs/BACKLOG.md` |
| C-4 Apple Watch voice notes | `docs/BACKLOG.md` |
| Google sign-in via `expo-auth-session` | `05-google-oauth-integrations.md` |
