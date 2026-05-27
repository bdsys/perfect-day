# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Claude instructions
* Ignore SAP/Concur specific claude guidance. This is a personal project used to learn how to build a large software system using Claude Code. Practically this means do not use any skills in "mcs network", no Obsidian and no SAP/Concur Wiki or Jira. You should still use MCPs.
* Run make test-all or lesser relevant tests after all changes to make sure the app is still working.

## Project Overview

**Perfect Day** is an automated diary app that synthesizes data from Google Calendar, Google Photos, weather, and Spotify into warm, narrative diary entries using an LLM. Entries are always saved as drafts for human review before publishing. The LLM must only use facts present in source data — never infer or fabricate emotional states or details.

Primary use case: a parent's diary of their child's life.

## Planned Tech Stack

| Layer | Technology |
|---|---|
| Web frontend | Next.js (React) — SSR required for Open Graph social sharing previews |
| Mobile frontend | Expo (React Native) — iOS and Android |
| Backend API | Python, FastAPI |
| Database | PostgreSQL |
| File/photo storage | MinIO (S3-compatible, self-hosted) — use S3 API for portability to AWS S3 |
| LLM | Anthropic Claude (Opus/Sonnet preferred); Gemini as fallback |
| Auth | Google OAuth, Facebook OAuth, email+password |

## Infrastructure

- Self-hosted Intel NUC (4-core x86 1.85GHz, 8GB RAM) — shared with other services, resource-constrained
- Network edge: Cloudflare (public TLS via Universal SSL, edge WAF, DDoS, residential IP hidden) in front of FortiGate 7.2+ (CF↔origin TLS termination via Cloudflare Origin Certificate, host-based routing, FortiGate WAF/IPS). FortiGate forwards plain HTTP to NUC on the home LAN.
- Domain: `diary.perfectday.andrewlass.com`
- Hybrid deployment supported: fully self-hosted on NUC, or NUC + cloud offload for LLM/heavy processing

## Core Data Model (planned)

Key entities:
- **User** — includes `subscription_tier`, `stripe_customer_id` (placeholder), `notification_preferences`
- **Diary** — owned by a User; up to 4 per user; has `scan_interval_minutes` (default 60)
- **Permission** — links a User to a Diary with `view` or `edit` role
- **Entry** — belongs to a Diary; has `status: draft | published`; contains narrative body, photos, date, title, location, weather (optional), music (optional)
- **Event** — raw calendar/photo/location data attached to an Entry
- **Photo** — stored in MinIO; linked to Entry; contains EXIF/location metadata
- **OAuthToken** — per-user per-provider (Google Calendar scope, Google Photos scope stored separately to handle partial grants)
- **ScanJob** — per-diary scan scheduling state, last scan timestamp, backfill cursor

## Key Architectural Constraints

**Auth & tokens:**
- JWT with refresh tokens — stateless, works across Next.js and Expo
- Expo must use `expo-secure-store` for token storage (not AsyncStorage)
- Google OAuth partial grant handling: Calendar and Photos use separate scopes; app must degrade gracefully if one is denied (e.g., still scan Calendar if Photos is denied)

**LLM integration:**
- Backend calls LLM; result is always stored as a `draft` Entry
- Prompt must include only facts from source data — no emotional inference
- Drafts surface in web/mobile UI for review before publish

**Scan worker:**
- Background job per diary, configurable interval (default 1 hour)
- Handles Google Calendar and Photos API rate limits with exponential backoff
- Failed scans are logged and retried — not silently skipped
- Backfill mode: pull historical data and generate past entries

**Photo storage:**
- Photos are of a child — treat as sensitive personal data
- Encryption at rest in MinIO is required
- Data deletion flow must cascade: diary deletion removes entries, photos, scan state; account deletion removes all diaries and user data

**Entitlement/tier checks:**
- Entry creation (auto and manual) must check tier limits before proceeding
- Backend enforces tier gating — not just the frontend
- Free tier: 1 diary, 3 auto-generated entries, 5 manual entries

**Notifications:**
- Push notifications via Expo Push Notifications when a draft entry is ready
- Email notification as fallback/alternative
- Notification preferences stored per user and respected before sending

## PoC Scope Priority

Build first:
1. Auth (Google OAuth + email/password), JWT lifecycle
2. Diary and Entry data model, CRUD API
3. Google Calendar integration + scan worker (basic interval)
4. LLM draft generation from Calendar events
5. Web UI: diary view, draft review/approve flow

Defer:
- Google Photos integration
- Weather and Spotify enrichment
- Mobile (Expo) app
- Export (PDF/JPG/PNG)
- Social sharing
- Notification system
- Stripe / subscription enforcement

## Development Notes

- Original product brief and open architectural questions archived at `docs/archive/OPUS_INITIAL_PLAN.md`
- When the stack is scaffolded, update this file with actual build/run/test commands
