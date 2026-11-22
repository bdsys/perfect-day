# Diary sharing (Phase 3 #19) — project 1 of 4

Status: **approved by Andrew 2026-11-22.**

## Why

The core goal (`design/00-vision.md`): an automated diary Andrew barely touches, so that his wife and his mom can see what he and his daughter do, privately, instead of on Facebook. Calendar → AI entry works today. Nobody but the owner can see the result. This project lets the family see it.

Andrew's words: "read and see updates about my daughter. Probably not really help write. […] Just a place to share. Like Facebook but not out in the open since it's kids. More private and intentful."

## The four projects, in Andrew's order (option A, 2026-11-20)

1. **This one: sharing.** Invite by a link the owner copies and texts. Accept, the viewer's experience, plan limits and counters, the hand-over when an owner deletes their account.
2. **Notifications:** email confirmation for accepting invites (closes this project's known gap), email and phone notifications (web push to the home-screen shortcut, no store app): invites by email, "new entries" to the family, "ready to share" to the owner. Needs an email provider account and DNS records (Andrew, about an hour).
3. **Per-rule share setting** in the rules engine (`auto_creation_rules.options`): *keep as draft* / *ask me to share* / *share automatically after 24 hours with a heads-up*. Default "ask me to share"; when an event matches two rules the more careful setting wins; manual entries never share on their own. Rewrites CLAUDE.md's "always drafts for human review" rule (Andrew's decision). Needs project 2.
4. **Co-parent's calendar feeds the diary** (Family plan). Touches the scan worker and whose Google connection an event comes from.

Projects 2–4 get their own designs. Nothing in this one may make them harder: keep `diary_permissions.notifications_muted`, and keep invitation creation in one service function project 2 can hook an email onto.

## Goals

1. **Invite.** On a diary's **Sharing** screen the owner enters an email and a role (Viewer; Editor only on Family) and gets an invite link to copy (and the phone's share sheet where the browser supports `navigator.share`). The link works once, for 7 days, and only for an account with that email address.
2. **Accept.** The link opens `/invite/<token>`. Signed out: "You've been invited to a diary on Perfect Day. Sign in or create an account to see it." with Sign in / Create account, both returning to the invite afterwards (Google sign-in included). Nothing about the diary is shown before sign-in. Signed in: "Andrew shared *[diary name]* with you" and **Open diary**. Accepting creates the `diary_permissions` row and opens the diary.
3. **What a viewer sees.** The diary list shows the shared diary marked "Shared by [owner's display name]". Inside: published entries with their photos and place names; no drafts, no AI badge, no rules, no settings, no scan controls, no Google prompts. Already enforced by the API (`entries.py`, `photos.py` rule (c), `exact_places=role is None`); the web hides what the API refuses. A viewer is **never asked to create a diary or connect Google**: someone who arrived by invite and has no diary of their own sees their shared diaries, not the "create your first one" prompt (it moves below the list, or behind a button).
4. **Manage.** The Sharing screen lists people and pending invites with a counter ("Shared with 2 of 3 people"). The owner can remove a person, revoke an invite, make a new link for a pending invite (the old one stops working), and on Family switch a person between Viewer and Editor. A member can **Leave** a shared diary from its page.
5. **Plan limits.** Counted per diary, people plus pending invites:
   | | Free | Plus | Family |
   |---|---|---|---|
   | People who can read | 3 | 10 per diary | Unlimited |
   | Editors | No | No | Yes |
   Over the limit, the invite button is replaced by the limit message and an upgrade link, as the other limits do (`services/tier.py`, the existing plan badge). The API enforces it, not just the page.
6. **Downgrade.** No one loses access. Over the limit after a downgrade, existing people stay and new invites are refused until the count is under the limit. Editors in a diary whose owner is no longer on Family act as viewers (role kept, shown as "Editor — needs Family"), and edit again if the owner upgrades. Matches `design/08-security-privacy.md` § Subscription tier downgrade: nothing is deleted.
7. **Hand-over when an owner deletes their account (option C, Andrew 2026-11-20).** The account-deletion screen lists each diary the person owns that has members, each with a choice: **Give to** [one of its members] or **Delete it**. Default is Delete, so nothing moves without a choice.
   - During the 7-day grace the chosen member sees a banner on that diary: "[Owner] is deleting their account. *[Diary]* becomes yours on [date]." with **Decline** (the diary is then deleted with the account). If the owner restores their account, nothing changes.
   - At the hard delete (`hard_delete_user`), before anything is removed: the diary's `owner_user_id` becomes the recipient, the recipient's own permission row on it is removed, other members keep theirs, and every photo of the departing owner that is attached to an entry in that diary moves to the recipient (below). The diary's rules stay; its scan stops until the new owner connects their own calendar, since the old Google grant is revoked. The diary counts against the recipient's plan; if that puts them over their diary limit, the existing read-only rule applies (no data lost) and they are told on the diary list. *(My call, from the builder check:)* that rule orders diaries by when the person came to own them, not when the diary was created, so the diary just received is the one that goes read-only and the recipient's own diary keeps working. This needs an "owned since" column (set to `created_at` for existing diaries) and `services/read_only.py`'s ordering changed to use it.
   - Diaries not handed over, and photos not attached to a handed-over diary, are deleted exactly as today.
   - *(My calls, from the builder check:)* if the chosen recipient leaves or is removed during the grace, or declines, the diary is deleted with the account. Expired invites don't count against the limit.
8. **Photos stay with the diary they're in (option C).** When an editor's account is hard-deleted, each of their photos attached to an entry in a diary they don't own moves to that diary's owner. A photo attached in diaries of several owners moves once, to the owner of its earliest attachment; reads in the others keep working through rule (b)/(c) of the photo-read rule (`design/03-api-surface.md`), which does not depend on who owns the photo.
   - **Moving a photo:** re-wrap its DEK from the old owner's KEK to the new owner's (`core/photo_crypto.py`: `derive_kek(user_id)` is salted by user id), server-side copy each object (full, thumbnail, display) to the new owner's `{user_id}/` prefix, update `photos.user_id` and the keys, then let the existing prefix scrub delete the old copies. Photo bodies are not re-encrypted.
   - A move failure behaves like a storage failure in #91: the hard delete raises and retries next hour. A deletion never reports done while photos sit in the wrong place.
   - A moved Google Photos item that the recipient already has (unique `user_id, source, external_id`) must not make the move fail every hour: the move resolves the clash (keeps one photo row and points the entry links at it).
9. **Docs and counters (Andrew, 2026-11-20).** Update `COMPETITION.md` (tier matrix: Free 3 viewers, Plus 10 per diary view-only, Family unlimited + editors; the co-parent calendar as planned), `design/02-data-model.md` (photo ownership on delete), `design/03-api-surface.md`, `design/08-security-privacy.md` (deletion flow with hand-over), `design/09-poc-scope.md` #19, `POC_PHASE2_TODO.md`, the `/plans` page rows (People who can read; Editors), and add counters: people on the Sharing screen and in the plan details on the diary list ("People: 2 of 3"), next to the existing Diaries and AI drafts counters.

## Non-goals

- Email or push of any kind (project 2). The owner sends the link.
- The per-rule share setting (project 3); entries are published by hand as today.
- A co-parent's calendar feeding the diary (project 4).
- Separate audiences ("Mom sees everything, the wider family sees highlights"). Logged for later; against the "magic" principle for now.
- Comments or reactions.
- "Anyone with the link" access, public pages (X-2 social sharing is a separate backlog item).
- Changing which roles see exact coordinates (owner only stays).

## Anti-goals

- Anyone not invited seeing a child's photo or entry (one known gap until project 2; see Constraints).
- Mom being asked to set up a diary, connect Google, or understand roles before she sees anything.
- A co-parent losing the diary, or photos vanishing from it, without warning.
- A deletion that leaves the departing person's photos anywhere they didn't choose.

## Constraints

- Roles are already enforced per endpoint; extend, don't duplicate. Owner-only for invites and member changes; "owner or self" for removal (`design/03-api-surface.md` § Diary sharing).
- Store only a hash of the invite token (the `invitations.token` column holds the hash), so a database leak or backup gives no working links. Hence "make a new link" rather than "copy the link again".
- An invite accepted by an account whose email differs from `invited_email` is refused with: "This invite is for [masked email]. Sign in with that address, or ask [owner] for a new link." (Emails compared case-insensitively.)
- **Known gap, accepted by Andrew (2026-11-20: "the risk seems low").** The email match does not prove the person owns the address: `POST /v1/auth/register` accepts any email unverified, and only Google sign-in sets `email_verified_at`. Someone who got a forwarded link before the invited person signed up could register with that address and get in (and take the address). The owner sees every member on the Sharing screen and can remove anyone. Project 2 closes the gap: once the app can send email, accepting needs a verified email. Say this in `design/08-security-privacy.md`.
- Accepting while already a member, or the owner opening their own invite, does nothing harmful and opens the diary.
- Shared diaries never count against the member's own diary limit.
- Deletion must cascade (CLAUDE.md): a member's account deletion removes their permission rows; a diary deletion removes its permissions and invitations; the hand-over is the only exception, and only by the owner's explicit choice.
- **Retry-safe steps, not one transaction** *(my call, from the builder check)*. `hard_delete_user` takes its `FOR NO KEY UPDATE` check in a short session that commits first, and each diary and storage step runs on its own; storage calls can't be rolled back anyway. So each step re-checks state before acting, copies before it updates rows, scrubs old copies last, and locks the diary row so a Decline that lands mid-delete is respected. Any step can fail and the next hourly run finishes the job from where it stopped. Restore already refuses once the deadline has passed (#91), so the restore race stays closed.
- Row 67 (restore brings deleted accounts back) will need to know about hand-overs; note it on that row, don't build it here.

## Approaches

- **Chosen: build on the existing tables and role checks.** `diary_permissions` and `invitations` exist, and every diary-scoped endpoint already resolves the caller's role. Add the invite/accept/member endpoints from `design/03-api-surface.md`, the web screens, the limits in `services/tier.py`, and the hand-over and photo move inside `hard_delete_user`. One migration at most (invite token hash and a hand-over choice per diary, e.g. `diaries.handover_to_user_id` and a decline flag).
- Rejected: equal co-owners (option B). Bigger change, and it raises whose plan, whose calendar and whose photos questions Andrew preferred to avoid (2026-11-20).
- Rejected: invite links that work without an account. Fewer steps for Mom, but a forwarded link would open a child's diary to anyone.

## Weighed against the core goal

- **Less parent effort:** one email and a copied link per person, once. Default Viewer.
- **Family sees it:** yes, this is the project that makes that possible; notifications (project 2) make them come back.
- **Private:** account plus matching email plus one-time hashed link; viewers see only published entries and place names. The email isn't verified until project 2 (accepted risk).
- **True:** unchanged; viewers see what the owner published.

## Left to the builder

Endpoint and response shapes beyond `design/03-api-surface.md`, where the Sharing screen sits (a "Share" button on the diary page is the expectation), the exact migration, how the masked email reads, test layout, and the order of the work.
