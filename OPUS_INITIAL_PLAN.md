  ---
  Perfect Day — PoC Planning Brief

  I want to plan a web and mobile application called Perfect Day — an automated, beautiful diary of a person's life, initially built for my daughter. Please help me create a detailed PoC plan.

  ---
  Core Concept

  The app automatically generates warm, narrative diary entries by synthesizing data from multiple sources using an LLM. Entries are presented chronologically as a diary. The LLM's job is to take raw structured
  data (calendar events, photos, location, weather, music) and produce a human-feeling narrative entry — not just summarize data. Generated entries are always saved as drafts first for human review before being
  published to the diary. The LLM must only use facts present in the data — it must never infer or fabricate emotional states or details.

  ---
  Tech Stack

  - Frontend (web): Next.js (React) — SSR is important for Open Graph/social sharing previews
  - Frontend (mobile): Expo (React Native) — iOS and Android
  - Backend: Python, FastAPI preferred
  - Database: PostgreSQL
  - File/photo storage: MinIO (S3-compatible, self-hosted on lab NUC) — use the S3 API so it's portable to AWS S3 later
  - LLM: Cloud-hosted. Prefer Anthropic Claude (Opus/Sonnet). Gemini as fallback if cost is a concern.
  - Auth: Google OAuth, Facebook OAuth, email+password (app manages user DB)

  ---
  Infrastructure

  - Lab: Intel NUC, 4-core x86 1.85GHz, 8GB RAM, shared with other services
  - Network edge: FortiGate 7.4 — handles Layer 4, WAF, virtual hosting, TLS termination from the internet
  - Domain: diary.perfectday.andrewlass.com (lab domain for now)
  - Cloud offload: Open to cloud services (AWS, GCP) to offload LLM calls and heavy processing from the NUC
  - Hosting should support two deployment options: (1) fully self-hosted on the NUC, (2) hybrid with cloud offload for LLM/processing

  ---
  Data Sources

  Primary (must have for PoC)

  - Google Calendar — events become diary entry candidates
  - Google Photos — photos (with EXIF/location metadata) attached to entries
  - Manual entry — user writes directly, LLM assists with editing and tone

  Secondary (nice to have, include in plan but can defer)

  - Weather API — show conditions during an entry's date/location
  - Spotify — show what was playing around the time of the entry

  ---
  Key Features

  Diary entries

  - Each entry is generated from one or more "events" (calendar + photos + location + enrichment data)
  - LLM synthesizes all available data into a warm narrative draft
  - User reviews and approves/edits drafts before they're published
  - Photos embedded or linked within entries
  - Entries have a date, title, narrative body, photos, location, weather (if available), and music (if available)

  Automation / scanning

  - Configurable scan interval per diary (default: every 1 hour)
  - On each scan: pull new calendar events and photos since last scan, generate draft entries
  - Backfill mode: ability to pull historical data (Calendar, Photos, or manual) and generate entries for past dates

  Multiple diaries

  - Each user account can create up to 4 diaries (e.g., one per family member)
  - Use case: separate diaries for different children or family members

  Sharing & permissions

  - Diary owner can invite family members with view-only or edit access
  - Invite system managed in the web app

  Export

  - Export entire diary, a single day, or a specific entry to PDF or JPG/PNG

  Social sharing

  - Share a specific entry to Instagram, Facebook, Reddit, X
  - Next.js SSR ensures Open Graph preview cards render correctly when links are shared

  ---
  Auth & User Model

  - Login via Google, Facebook, or email (app manages email user DB)
  - Each login = one user account
  - Each user can own up to 4 diaries
  - Diaries can be shared with other users via invite (view or edit permission)
  - Diary owner manages permissions in the web app
  - Session strategy: Use JWT with refresh tokens — stateless, works cleanly across Next.js and Expo. Please include token lifecycle (issuance, refresh, expiry, revocation).
  - Mobile token storage: Tokens on Expo must be stored securely using expo-secure-store, not AsyncStorage. Please address this explicitly.
  - Google OAuth scopes: Calendar and Photos require separate OAuth scopes. Please address the partial grant scenario — what happens if a user grants Calendar access but denies Photos, or vice versa. The app should
   degrade gracefully per scope.

  ---
  Future Monetization (Plan for but do not build in PoC)

  The app will eventually use a monthly subscription model with tiered access. The PoC should be built with this in mind — the data model and API should accommodate tier/entitlement checks even if they are not
  enforced in the PoC.

  Tier Structure (illustrative, exact pricing TBD)

  ┌───────────────────────────────────────────┬──────┬───────────┬───────────┐
  │                  Feature                  │ Free │  Tier 1   │  Tier 2   │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Diaries                                   │ 1    │ 2         │ 4         │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Auto-generated entries (total, per diary) │ 3    │ Unlimited │ Unlimited │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Manual entries (total, per diary)         │ 5    │ Unlimited │ Unlimited │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Google Calendar + Photos integration      │ Yes  │ Yes       │ Yes       │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Weather integration                       │ No   │ Yes       │ Yes       │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Spotify integration                       │ No   │ No        │ Yes       │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Future integrations                       │ No   │ Selective │ All       │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Social sharing                            │ No   │ Yes       │ Yes       │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Export (PDF/JPG/PNG)                      │ No   │ Yes       │ Yes       │
  ├───────────────────────────────────────────┼──────┼───────────┼───────────┤
  │ Family sharing / invite system            │ No   │ Yes       │ Yes       │
  └───────────────────────────────────────────┴──────┴───────────┴───────────┘

  Design Implications

  - User accounts need an associated subscription tier field
  - Entry creation (auto and manual) must check entitlement limits before proceeding — return a clear upgrade prompt when limits are hit
  - Integration availability is gated by tier — the backend should enforce this, not just the frontend
  - Stripe (or equivalent) is the assumed payment processor — not in scope for PoC but the user model should have a stripe_customer_id placeholder field
  - Free tier is designed to let users experience the core value (a real generated diary entry) before hitting a paywall

  ---
  Notifications

  - When the scan worker generates a new draft entry, the user should be notified
  - Please address the notification strategy: push notifications via Expo Push Notifications and/or email
  - Include how notification preferences are stored and respected per user

  ---
  Data Privacy & Security

  - Photos are of a child — treat as sensitive personal data
  - Please address: are photos stored raw in MinIO or encrypted at rest? What is the recommended approach?
  - Include a data deletion flow: when a user deletes a diary or their account, what gets deleted and in what order?
  - Flag any GDPR/CCPA considerations relevant to this app, even as a personal/lab project that may go public later

  ---
  Rate Limiting & API Quotas

  - The scan worker will call Google Calendar and Google Photos APIs on a schedule, potentially across multiple diaries simultaneously
  - Google enforces per-user and per-project API quotas
  - Please address: how does the worker handle rate limit errors (429s) gracefully? Include backoff strategy and whether failed scans are retried or skipped with logging.

  ---
  What I Need From You

  Please produce:
  1. Architecture diagram or description — how the components connect (Next.js frontend, Expo app, FastAPI backend, PostgreSQL, MinIO, LLM API, Google APIs)
  2. Data model — key entities (User, Diary, Entry, Event, Photo, Permission, etc.)
  3. API surface — key FastAPI endpoints needed for the PoC
  4. LLM integration design — how the backend calls the LLM, what the prompt structure looks like, how drafts are stored and surfaced for review
  5. Google OAuth + Calendar + Photos integration — how auth tokens are stored and used for API calls, including partial grant handling
  6. Scan/automation worker design — how the background scanning job works (scheduling, per-diary config, backfill, rate limit handling)
  7. Notification design — push and/or email strategy for draft-ready alerts
  8. Security & privacy design — photo storage encryption, data deletion, GDPR/CCPA notes
  9. PoC scope recommendation — what to build first vs. defer, given the NUC's resource constraints
  10. Open questions — anything ambiguous in this brief that needs a decision before building

  ---
  Take your time. Quality and accuracy over speed. Flag anything that seems underspecified or that has meaningful architectural trade-offs worth discussing before I start building.

  ---

