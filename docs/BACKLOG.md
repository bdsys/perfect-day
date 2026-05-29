# Backlog

Loose collection of future improvements not currently scoped into a plan.

---

## UX / Navigation

- **Global navigation toolbar.** Add a persistent top or side nav with links to common destinations (Diaries, Photos, account/settings) so users don't have to navigate via per-page buttons. Currently each route adds its own ad-hoc links in the page header (e.g., `/diaries/[diaryId]` has Photos, Deleted entries, Auto-Creation Rules buttons in the action row). A shared layout component would reduce duplication and make the app feel more cohesive. Likely belongs in `apps/web/src/app/layout.tsx` or a new `<AppNav />` client component used across authenticated routes.

---

## AI differentiators

These items lean into the "open the app and the draft is already there" magic that differentiates Perfect Day from Day One. Full context in `COMPETITION.md` § AI differentiators.

- **AI-1: Vision photo captioning + smart photo selection.** When generating an entry draft, send photo metadata (timestamp, GPS, user captions) to a vision-capable Claude model to generate short captions, then have the LLM rank and select the 3–5 best photos to embed in the entry. Transforms a collection of snapshots into a curated visual narrative. Promotes the existing L3 backlog item in `design/09-poc-scope.md`. Depends on: item 13 (MinIO), item 14 (Google Photos). Likely files: `workers/llm.py`, `workers/tasks.py`, new `workers/vision.py`. Tier: Family (unlimited), Plus (50 captions/month).

- **AI-2: Ask Your Diary (RAG over history).** Chat interface that answers questions about the diary's published entries: "When did we last go to the beach?" "What did Maya say about kindergarten?" Requires a vector or trigram index on `entries.body_markdown`. Architecture decision needed: `pgvector` (semantic similarity, higher query cost, new extension) vs. `pg_trgm` (keyword/fuzzy, lightweight, already available in Postgres). Flag this decision in `design/10-open-questions.md` before pulling into a wave. Likely files: new `routers/ask.py`, `services/rag.py`, Alembic migration for index.

- **AI-3: Auto recaps & anniversaries.** Auto-generate weekly/monthly/year-in-review summary entries from the user's own published entries. Scheduled Celery beat task. "On This Day" surfacing UI shows entries from same date in prior years (also covers P-1). Trigger: weekly on Sunday night, monthly on 1st, annual on Dec 31. Likely files: `workers/tasks.py` (new beat tasks), `workers/recap.py`, `routers/entries.py` (filter by date-of-year).

- **AI-4: Voice-note → narrative entry.** Upload a voice memo (m4a/mp3/wav); transcribe via Whisper API or AWS Transcribe; feed transcript + that day's calendar events + photos to LLM to produce a narrative draft. Closes the "too tired to write" gap. Depends on: C-1 (audio transcription). Likely files: new `routers/voice.py`, `workers/transcription.py`, `workers/llm.py` (new prompt variant).

- **AI-5: Year-in-Review (Spotify Wrapped style).** Annual engagement feature: shareable slide/card sequence with top moments, photo collage, narrative arc, diary stats (entries written, cities visited, events attended). Runs in December; opt-in notification. Depends on: AI-3 (recap infrastructure). Likely files: new `workers/year_review.py`, new Next.js route `/diaries/[id]/year-in-review`.

- **AI-6: Video upload with audio-music fingerprinting.** Upload a short video clip; server extracts audio; fingerprints via ACRCloud or AudD API; if a match is found, surfaces "the song playing was *X* — add as music enrichment?" Uniquely valuable because we already have an enrichment pipeline (`enrichments.source`). Depends on: item 13 (MinIO). Likely files: new `workers/fingerprint.py`, `workers/enrichments.py` (new source type), Alembic migration.

- **AI-7: AI title + tag suggestions on manual entries.** While a user types a manual entry body, suggest a title and 1–3 tags via a debounced `POST /v1/entries/{id}/suggest` call. Cheap; closes Day One Gold parity gap. No new schema required. Likely files: new `routers/suggest.py`, `services/suggest.py`.

---

## Parity table stakes

Day One's free-tier features that users will expect. Full context in `COMPETITION.md` § Parity table stakes.

- **P-1: "On This Day" surfacing.** List entries from the same calendar date in prior years on the diary timeline view. Query: `WHERE DATE_PART('month', entry_date) = X AND DATE_PART('day', entry_date) = Y AND diary_id = Z`. Covered by AI-3 (which includes the surfacing UI) but can be shipped standalone as a read-only panel. No schema changes.

- **P-2: Daily prompts library.** Curated list of journaling prompts (start with 50–100 static entries). UI: "Need inspiration?" button on diary view opens a prompt picker; selecting one opens the manual entry form with the prompt pre-filled in the title or body. No backend schema required initially; prompts can be a static JSON file in the frontend.

- **P-3: Entry templates.** Markdown templates that pre-fill an entry's body. Schema: optional `diaries.default_template` (text) + `entries.template_id` FK if we want a `templates` table. Start with 3–5 built-in templates (travel day, first milestone, family outing). Likely files: Alembic migration, `routers/entries.py`, Next.js template picker.

- **P-4: Journal streaks.** Count consecutive calendar days on which the user published or manually edited an entry in a given diary. Display streak counter on diary view header. Compute in query on load (cheap at PoC scale) or cache in `diaries.current_streak` (int). Reset condition: no entry on a day. Likely files: `services/streak.py`, `routers/diaries.py`, frontend diary header component.

- **P-5: Tags + favorites + advanced search.** Schema column `entries.tags` (array) already plausible; `entries.is_favorite` (bool) straightforward. UI: tag filter chips on diary timeline, favorites filter, full-text search box (`pg_trgm` or `ILIKE` for PoC). Likely files: Alembic migration, `routers/entries.py` (filter params), Next.js filter bar component.

- **P-6: Map view.** Plot published entries on an interactive map using `entries.lat`/`entries.lon` (or nearest photo/calendar event location). `lat`/`lon` columns already exist on `Diary` and `Photo` after 2026-05-28 commits. Needs lat/lon propagation to `entries` table (or derive on load from linked photos). Frontend: Leaflet or Mapbox GL JS map component at `/diaries/[id]/map`. Likely files: Alembic migration (entries lat/lon), new Next.js route.

- **P-7: Calendar view.** Month view of entries — one dot per entry on its date, click opens the entry. Frontend-only unless we add a `GET /v1/diaries/{id}/entries?month=YYYY-MM` endpoint for efficient range queries. Likely files: new Next.js route `/diaries/[id]/calendar`, optional `routers/entries.py` date-range param.

---

## Premium content types

Day One Silver features. Full context in `COMPETITION.md` § Premium content types.

- **C-1: Audio recording + transcription.** Record audio in-app or upload a file; transcribe via Whisper (self-hosted or OpenAI API) or AWS Transcribe. Store audio as a MinIO object; store transcript as `entries.body_markdown` (manual) or as an event for LLM synthesis. Prerequisite for AI-4. Likely files: new `workers/transcription.py`, `routers/voice.py`, Alembic migration (`audio_recordings` table or `events.payload` extension).

- **C-2: Handwriting / drawing entries.** Canvas widget (mobile-first; Expo Skia or React Native SVG). Export as PNG → store in MinIO like a photo. Likely files: Expo canvas component, `routers/photos.py` (reuse upload flow), frontend viewer.

- **C-3: Document / PDF embedding.** Upload PDFs or images of documents; stored in MinIO under a `documents/` prefix; rendered as a link or inline preview in the entry body. Reuses the photo upload/finalize flow. Likely files: minimal — new `document_type` flag in `photos` table or a separate `documents` table.

- **C-4: Apple Watch app.** Dictation-only: tap to record a voice note on the Watch; syncs to phone via WatchConnectivity; uploads to backend as a C-1 audio recording. Depends on: Expo mobile app, C-1. Entirely mobile scope.

---

## Ambient capture & integrations

Day One Silver features. Full context in `COMPETITION.md` § Ambient capture.

- **I-1: Email-to-journal.** Each diary gets a unique inbound email address (e.g., `diary-{uuid}@in.perfectday.andrewlass.com`). SES or SendGrid inbound parse webhook creates a manual entry from the email body. Useful for quick captures ("just emailed my diary about the concert"). Schema: `diaries.inbound_email_token` (UUID). Likely files: new `routers/inbound_email.py`, Alembic migration.

- **I-2: Browser extension (Safari/Chrome).** "Save to Perfect Day" button that sends the current page's URL and selected text to `POST /v1/entries` as a manual entry draft. Minimal; the extension is a simple `fetch` + browser storage for auth token. Separate repo (`apps/extension/`).

- **I-3: Shortcuts / Zapier / IFTTT receivers.** A webhook endpoint `POST /v1/diaries/{id}/webhook/entry` that accepts `{title, body, date}` and creates a manual entry. Zapier/Make can then automate "when I save a note in Notion → diary entry." Cheap once API is stable. Likely files: new `routers/webhooks.py`, `diaries.webhook_token` (UUID).

- **I-4: Strava + Fitbit + Apple Health enrichment.** New `enrichments.source` values (`strava_activity`, `fitbit_sleep`, `apple_health_steps`). OAuth per provider (same pattern as Google Calendar). Enrichment pipeline already has a normalized `Enrichment` model. Likely files: new `workers/enrichments_strava.py` etc., Alembic migration for new source types.

- **I-5: Spotify + Apple Music enrichment.** Stub OAuth endpoints already referenced in the archived plan. Promote when Tier 2 (now "Plus") is defined. Spotify: pull recently played during entry date window. Apple Music: similar via MusicKit. Likely files: `workers/enrichments.py` (new source handler), `routers/oauth.py` (Spotify scope).

---

## Export & physical products

Full context in `COMPETITION.md` § Export & physical products.

- **X-1: PDF export.** Export an entry, a date range, or the full diary as a formatted PDF. Use Puppeteer (headless Chromium, renders the existing Next.js entry view) or WeasyPrint (Python, lighter). Endpoint: `GET /v1/diaries/{id}/export/pdf?from=&to=`. Breadcrumb routes already exist in `design/09-poc-scope.md`. Gated: Plus and Family tiers.

- **X-2: Social sharing with OG previews.** Each published entry gets a shareable URL with Open Graph meta tags populated server-side by Next.js SSR. Requires a `share_tokens` table (public read-only token per entry). Breadcrumb routes already exist. Gated: Plus and Family tiers.

- **X-3: Printed photo books.** Integrate with a print-on-demand partner (Blurb API, Lulu Direct, or Mixbook). Backend generates a print-ready PDF via X-1; submits to the partner's order API. Discount rate (25%/35%) is a tier perk stored on the User. Real secondary revenue stream — not just a nice-to-have. Requires X-1 first.

- **X-4: JPG/PNG single-entry image export.** Generate a styled card image for a single entry — suitable for Instagram-style sharing. Subset of X-1 (Puppeteer screenshot of a fixed-width entry card component). Likely faster to ship than X-1 and higher shareability value. Gated: Plus and Family tiers.
