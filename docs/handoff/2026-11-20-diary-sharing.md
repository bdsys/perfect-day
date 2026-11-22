# Handoff: diary sharing (Phase 3 #19), 2026-11-20

Pick-up note for a new session. Start here, then read the design.

## Where it stands

- **Design written, not yet approved:** `docs/superpowers/specs/2026-11-20-diary-sharing-design.md`. Builder check done and folded in. **Next step: Andrew reads it and approves (or corrects); then write the plan with superpowers:writing-plans** at `docs/superpowers/plans/2026-11-20-diary-sharing.md`.
- **Not committed** (all in the main checkout on `main`): this file, the design, `design/00-vision.md`, and the pointers to it in `CLAUDE.md` and `design/README.md`. They go in one commit once Andrew approves the design (commit only when he asks).
- Nothing built. No branch yet.

## The core goal (new, `design/00-vision.md`)

Andrew's own words: an automated online diary he doesn't have to touch much (calendar → AI write-up → photos of him and his daughter) so his wife and his mom can see what they do, without Facebook and without much effort. "Like Facebook but not out in the open since it's kids. More private and intentful." He asked that every decision be weighed against it. Four tests: less parent effort; the family actually sees it; private to invitees; true to source data. He has other goals to add later.

## Decisions Andrew made (2026-11-20)

1. **Who first:** his wife and his mom. Mostly read-only (viewer); editing "maybe but not really".
2. **Ownership, option C:** one owner per diary. When an owner deletes their account they choose per shared diary: give it to a member, or delete it (default delete). The recipient is told during the 7-day grace and can decline. Photos stay with the diary they're in, including an editor's photos when the editor leaves.
3. **Updates:** email and a mobile notification. My proposal (not objected to): web push to the home-screen shortcut, no store app.
4. **Auto-publish as a per-rule setting** in the rules engine: keep as draft / ask me to share (A) / share automatically after 24 h with a heads-up (B). My calls (not objected to): default "ask me to share"; more careful setting wins on overlap; manual entries never auto-share; CLAUDE.md's "always drafts for human review" rule gets rewritten in that project.
5. **Plans:** Free needs sharing. Agreed table: people who can read Free 3 / Plus 10 per diary / Family unlimited; editors Family only; co-parent's calendar feeds the diary on Family (later project); notifications, the share settings and the hand-over on every plan. The 3 and 10 are my numbers. Separate audiences ("highlights for wider family") logged for later, not built.
6. **Docs, the `/plans` page and limit counters** must all be updated ("People: 2 of 3" next to the existing Diaries and AI drafts counters).
7. **Order, option A:** sharing first with a copy-and-text invite link; then notifications; then the per-rule setting; then the co-parent calendar.
8. **Unverified email risk accepted** ("the risk seems low"): until project 2, the invite's email match doesn't prove ownership (password registration doesn't verify email). Project 2 closes it.

## Research findings (code facts, checked 2026-11-20)

- `diary_permissions` (role viewer/editor, `notifications_muted`) and `invitations` (`invited_email`, `role`, `token`, `expires_at`, `accepted_at`, `accepted_by_user_id`) already exist (`apps/api/app/models/__init__.py:259-301`).
- Role checks already run on every diary, entry and photo endpoint: `routers/v1/diaries.py:_get_diary_or_404` returns the role; viewers see published only (`entries.py`); photo-read rules (b)/(c) in `design/03-api-surface.md` and `photos.py:150`; coordinates owner-only (`exact_places=role is None`); editors use the owner's AI quota and photo limit (tier-enforcement design).
- Planned endpoints are listed in `design/03-api-surface.md` § Diary sharing; none of the invite/accept/member endpoints are built.
- Photos belong to the uploader: `photos.user_id`, objects under `{user_id}/`, DEK wrapped by a per-user KEK (`core/photo_crypto.py: derive_kek` salted by user id). Moving a photo = re-wrap the DEK, server-side copy objects to the new prefix, update rows; bodies aren't re-encrypted.
- `hard_delete_user` (`workers/hard_delete.py`): lock check in a short session that commits first, then separate steps; scrubs the `{user_id}/` prefix. Hand-over and photo moves must be retry-safe steps.
- The app sends **no email** today (only config keys); no provider account set up (`deploy/nuc-ops.md` § A4).
- `POST /v1/auth/register` doesn't verify email; only Google sign-in sets `email_verified_at` (`routers/v1/auth.py:238`).
- Read-only on downgrade orders by `Diary.created_at` (`services/read_only.py`); the design adds an "owned since" date so a received diary is the one that goes read-only.
- After registering, the web app goes to `/diaries`, whose empty state says "Create your first one" (`apps/web/src/app/diaries/page.tsx:112`); invitees must not be pushed into creating a diary.
- Existing counters: `apps/web/src/app/diaries/page.tsx:33-39` (Diaries, AI drafts) and `components/PlanBadge.tsx`. Plans page rows: `apps/web/src/app/plans/page.tsx:4-9`.
- `COMPETITION.md:105` still says Family sharing No / View only / View + edit; to be updated.

## Other open items (not sharing)

- Row 33: Andrew said "all is working" after #91; not confirmed that this covered the rename → Scan now check. Ask, then mark row 33 done.
- Offered, unanswered: a sentence in `deploy/nuc.md` that deleted data can stay on the offsite drive until the next swap (~4 months plus the swap interval). Andrew refreshes the drive by hand every few months; advised deleting the old copy first.
- Deferred minors from #91 (in its PR body): endless hourly retry of a failing hard delete with no alert (row 62), manual pre-change dumps never pruned.
- Earlier recommendation still standing: row 52 (huge-image memory spike, no avatar upload rate limit) as a quick hardening fix.

## Production state

NUC runs `a55cdbb` (#91), deployed 2026-11-21 ~02:30 UTC with Andrew's OK: smoke 17/0, readyz ok, 4.5 GB available. `main` is at `dd0d4e6` (#92, docs only).
