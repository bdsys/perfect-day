# Item 18 — Tier enforcement (Free / Plus / Family)

Status: built and merged to `main` on 2026-11-15 (#53 → #51). Andrew's decisions after the build change three points; see "Decisions after the build" at the end.
Branch: `feat/18-tier-enforcement`, cut from `feat/14-photos-on-drafts` at `291b87b`. Its PR targets `feat/14-photos-on-drafts` and is rebased onto `main` once PR #51 merges. (Andrew: "I do not want you to redo ANY work".)

## Why

Perfect Day is free to try and paid to keep using heavily. The upgrade hook is AI-generated drafts: a Free parent gets enough of them to see the "the draft is already there" moment, then hits a limit. Andrew also wants a photo limit on Free: "People need a reason to upgrade."

Today the code enforces an older, superseded model:
- The tiers are `free / tier1 / tier2` (`services/tier.py:11-15`, check constraint `ck_users_subscription_tier`).
- Free gets 5 manual and 3 auto entries for the account's lifetime.
- Regenerate has no limit (review finding L-6).
- Photos are not gated.
- When a scan hits the auto limit, the entry is silently not created (`workers/rules.py:206-215`).

## Goals

1. Enforce the Free / Plus / Family limits below **in the API**, so a stale page or a script can't get around them.
2. Show a Free parent their limits at a glance, coloured by how much is left, with the reset date and an **Upgrade now** button.
3. Gray out a limited control once its limit is reached, and say why next to it.
4. Never lose a memory because of a limit. A calendar event that arrives after the AI limit still becomes an entry, just without AI text.

## Non-goals

- Billing, checkout and Stripe. "Upgrade now" leads to a "coming soon" page.
- Notifications about limits (item 20 builds notifications).
- Limits on features that don't exist yet: music, voice notes, vision captions, recaps, sharing and export.
- The Open-Meteo licence question (review TR-41). It moves to the billing item, because item 18 sells nothing.

## Anti-goals (a failure even if it technically works)

- An event silently dropped because the parent was over a limit.
- A limit the UI shows but the API doesn't enforce, or the other way round.
- Next month's AI drafts quietly spent on the 1st, before the parent sees them.
- Deleting a draft giving an AI draft back. That would be a loophole to unlimited drafts.

## The limits

Source: `COMPETITION.md:91-96`, with Andrew's decision to keep the photo limit.

| Limit | Free | Plus | Family |
|---|---|---|---|
| Diaries (active) | 1 | 2 | 4 |
| Manual entries | Unlimited | Unlimited | Unlimited |
| AI drafts per month | 5 | Unlimited | Unlimited |
| Photos per entry | 1 | Unlimited | Unlimited |

The tier values are renamed `free / plus / family`, with `tier1 → plus` and `tier2 → family`. That means:
- an Alembic migration that rewrites existing rows and the check constraint;
- updating `scripts/manage.py` (`users create --tier`, `users set-tier`);
- updating `design/02-data-model.md`, `design/03-api-surface.md` and every other `tier1/tier2` mention.

The limit table lives in one place in code (`services/tier.py`), and the API, workers and frontend all read it from there. The frontend gets it through the API (see "API"), never as its own copy.

## What counts as an AI draft

- **Counts:**
  - each AI text that is successfully saved to an entry: a draft written automatically after a scan, a rule or "New entry from Google Calendar";
  - each click of Generate, Polish or Regenerate.
- **Doesn't count:**
  - a failed generation, where every provider failed;
  - provider failover inside one generation. Anthropic failing and Gemini succeeding counts once.
- **Whose quota:** it belongs to the **diary owner**. An editor who clicks Regenerate in someone else's diary uses the owner's drafts. That's how the entry code already resolves the owner (`routers/v1/entries.py:681-688`).
- **The month** is a calendar month, resetting at 00:00 on the 1st.
  - It uses the timezone of the owner's oldest active diary, falling back to UTC when there is none.
  - The monthly limit exists only on Free, and Free has one active diary, so this is the parent's own timezone. (Users have no timezone column; diaries do: `diaries.timezone`.)
- **Usage is stored separately from entries.** Deleting or hard-deleting an entry never lowers the count.
  - `llm_generations` rows cascade away with their entry (`models/__init__.py:541-543`), so counting those rows is not enough. A per-user, per-month usage record is.
  - The table shape is the builder's choice.
- **No overspending under concurrency.** Two Regenerate clicks, or a click racing a scan, must not take a Free user to 6.
  - The draft is reserved atomically before the LLM call, for example with a conditional increment.
  - The reservation is released if the generation fails.

## Behaviour at the AI-draft limit

- **The Generate, Polish and Regenerate buttons** are grayed out. The note next to them says "No AI drafts left this month. Resets Dec 1." (the date varies).
  - `POST /v1/entries/{id}/regenerate` returns 403 `tier_limit` before anything is queued.
- **Automatic drafts** (scan, rule, backfill and "New entry from Google Calendar"):
  - The entry **is still created** with its title, date, place and any photos, but no AI text.
  - The entry shows: "Out of AI drafts until Dec 1. Write it yourself, or generate it then."
  - Writing it by hand is unlimited.
  - This replaces today's skip in `workers/rules.py:206-215`, and the "upgrade to generate" wording in `design/06-scan-worker.md:172`.
- **On the 1st:** the Generate button becomes available again on those entries. Nothing is generated automatically for them.
- **Manual entries** have no limit. Their creation check goes away (`routers/v1/entries.py:467`). Entry restore stops checking entry counts (`routers/v1/entries.py:684`).

## Behaviour at the photo limit (Free: 1 per entry)

- **Counts:** photos attached to that entry, by any route:
  - phone upload;
  - library pick;
  - Google Photos import;
  - attaching an existing photo (`POST /v1/entries/{entry_id}/photos`, `routers/v1/photos.py:285`).
- **API:** attaching beyond the limit returns 403 `tier_limit`. A Google Photos import on Free is capped at the entry's remaining room:
  - `pickingConfig.maxItemCount` (`services/google_photos.py:229-233`, today `MAX_PICK = 50` at `routers/v1/photo_imports.py:37`) is set to the room left, and the worker refuses extras.
  - If the entry is already full, starting an import returns 403.
- **UI:** once the entry has its 1 photo, Add photos is grayed out with "Free plan: 1 photo per entry."
  - With no photo yet, the phone and library pickers let a Free parent choose only one photo (my call: better than accepting 4 and refusing 3).
- Uploading a photo to the account without attaching it is not limited. The limit is per entry.

## Behaviour at the diary limit

- The **New diary** button is grayed out, with a note naming the limit. `POST /v1/diaries` keeps its 403 `tier_limit` (`routers/v1/diaries.py:194-213`).
- Restoring a deleted diary keeps its limit check (`routers/v1/diaries.py:349-373`).
- The existing per-user lock on diary creation stays (S-9).

## Over-limit data (downgrades and accounts from before the limits)

Andrew approved this on 2026-11-15. It follows `design/02-data-model.md:332`.

- **Diaries over the limit:**
  - The oldest by `created_at` stay active, as many as the plan allows. The rest become **read-only**:
    - entries stay readable;
    - scans, rules and backfill are off;
    - nothing can be added or edited.
  - The read-only state is computed from the current plan, not stored, so moving back up a plan reactivates them at once.
  - The diary page says the diary is read-only on the Free plan and shows Upgrade now.
- **Entries with more photos than the limit** keep all their photos. Only adding more is blocked.
- **AI drafts already used this month** before the feature ships are not counted retroactively. Counting starts at deploy.

## What the parent sees (option C from the mockups)

Andrew picked this from the mockups on 2026-11-15.

- **A badge in the page header** on the diary list page, the diary page and the entry page.
  - Example: "AI drafts: 3 of 5 left · resets Dec 1", with an **Upgrade now** button beside it.
  - Tapping the badge shows every limit (my call).
  - The diary list page's badge leads with Diaries.
- **Colours:**
  - green before the first use;
  - yellow after the first use;
  - red at the limit.
  - Diaries (limit 1) go straight from green to red. Photos per entry have no reset date.
- **A short note beside each limited control,** and the control is grayed out at the limit (see the three sections above).
- **Plus and Family show no badge,** because there's nothing to count.
- **Upgrade now** goes to a new page that shows the three plans (the table above, in plain words) and says paid plans are coming soon. It's a real route, so it can point at checkout later.

## API

- **Every blocked request** returns 403 with `{"error": {"code": "tier_limit", "details": {...}}}`, the existing shape (`core/errors.py:36`, `design/03-api-surface.md:218`).
  - `details` carries `limit`, `current`, `source` (`ai_draft`, `photo` or `diary`), `required_tier` (`plus`), and `resets_on` for AI drafts.
- **The frontend gets the plan and usage from the API:**
  - plan name;
  - each limit;
  - usage this month;
  - reset date;
  - which diaries are read-only.
  - Whether that's a new endpoint or part of `GET /v1/auth/me` is the builder's choice.
- **Entry responses say whether AI generation is available**, so the entry page can gray out its buttons without a second request.

## Settled after the builder check (Claude's calls, for Andrew to check)

1. **The owner's plan applies everywhere** in a diary: AI drafts, photos per entry, read-only state.
   - An editor who attaches photos in a Plus owner's diary has no photo limit. In a Free owner's diary they have the Free limit.
   - The diary and entry pages show the badge with the **owner's** numbers, to the owner and to editors. On the diary list page, everyone sees their own plan.
2. **Read-only diaries.**
   - **Allowed:** reading, deleting entries, and deleting the diary. Deleting the diary is how a parent gets back under the limit (`design/02-data-model.md:332`).
   - **Blocked:** everything else. That covers creating entries, editing, publishing and unpublishing, accepting suggestions, AI, adding photos, restoring entries, scans, backfill, rules and settings changes.
   - The API uses one shared guard, and the workers skip read-only diaries.
   - A draft waiting in a read-only diary stays a draft until the parent upgrades or deletes another diary.
3. **Where a draft is spent.**
   - Entry creation never spends a draft. `generate_entry_draft` (`workers/tasks.py:701`) reserves one immediately before the LLM call.
   - That task retries (`max_retries=3`), so the reservation is tied to the generation attempt. A retry of the same task never spends twice.
   - Backfill and batches create entries in date order. Each draft reserves as it is generated, so once the limit is reached the rest stay "out of AI drafts".
   - "New entry from Google Calendar" is one request: it creates the entry, then queues `generate_entry_draft` (`routers/v1/calendar_events.py:100-158`). The same reservation rule applies.
4. **Polish is not a separate endpoint.** Generate, Polish and Regenerate are one button. Its label follows the text (`apps/web/src/app/entries/[entryId]/page.tsx:574-586`), and it calls `POST /v1/entries/{id}/regenerate`. That endpoint and the worker are the two places the AI limit is enforced.
5. **Usage is recorded on every plan,** not only Free.
   - A parent who used 20 drafts on Plus and drops to Free mid-month sees "0 of 5 left" until the 1st.
   - The badge shows 0, never a negative number.
6. **The usage month** is a key like `2026-11`, computed when the draft is reserved, in the timezone that applies at that moment. If the diary's timezone changes mid-month, the key can shift by a few hours at the boundary. That's acceptable.
7. **Old tier code:**
   - `enforce_entry_tier_limit` and `try_enforce_entry_tier_limit` and their lifetime counts are removed, along with the tests that assert 5 manual / 3 auto. They encode the superseded model.
   - Callers today: `routers/v1/entries.py:467,684`, `routers/v1/calendar_events.py:125` and `workers/rules.py:207`.
8. **Stacked branch:** photo gating adds checks to `feat/14`'s attach and import paths without restructuring them, so the rebase onto `main` after #51 stays small.

## Left to the builder

- The table and column names for usage, and the migration details.
- Where the plan and usage data sits in the API, and the exact field names.
- Component structure for the badge and notes, and exact colours. Use the existing CSS tokens. The mockup used green `#2e7d32`, amber `#f9a825` and red `#c62828` as a guide.
- How the "Out of AI drafts" state is marked on an entry: a field, or derived from usage plus having no AI text.

## Docs to update with the build

- `COMPETITION.md`: rename the tier column headers if needed, and change the "Design intent" line that says photo limits aren't an upgrade hook. Andrew decided Free keeps 1 photo per entry as an upgrade reason.
- `design/02-data-model.md`, `design/03-api-surface.md`, `design/06-scan-worker.md:172`, `design/09-poc-scope.md` item 18 and `POC_PHASE2_TODO.md`.
- `design/code-review-2026-11.md`: mark L-6, TR-12 and TR-17 against this item. TR-14 (prompt caching) and TR-41 (Open-Meteo licence) stay open; TR-41 moves to billing.

## How we'll know it works

- **API tests:**
  - each limit at under, at and over the limit, for each plan;
  - two concurrent Regenerates at 4 of 5 give exactly one success;
  - a failed generation releases its reservation;
  - deleting an entry doesn't lower usage;
  - the month resets in the diary's timezone;
  - an editor uses the owner's quota;
  - over-limit diaries are read-only and come back on upgrade;
  - the migration maps `tier1 → plus` and `tier2 → family`.
- **Worker tests:** a rule match at the limit still creates the entry, without AI text, in the "out of AI drafts" state.
- **Jest:** badge colours at 5, 4, 1 and 0 left; the badge is hidden on Plus; the buttons are grayed out with their notes.
- **Playwright:**
  - a Free user uses drafts down to 0 and sees red with the buttons grayed out;
  - Upgrade now opens the plans page;
  - adding a second photo is blocked;
  - `manage.py users set-tier --tier plus` removes the badge.
- `make test-all` green.

## Decisions after the build (Andrew, 2026-11-15)

These override the sections above where they disagree. Details, open questions and the fix list are in `design/tier-followups-2026-11.md`, and the work is PoC item 18.1. The 18.1 design, which settles the open questions, is `docs/superpowers/specs/2026-11-15-tier-followups-design.md`.

1. **Automatic redrafts are free.** A background redraft triggered by a calendar change spends no AI draft. The first automatic draft of a new entry, and every Generate, Polish or Regenerate, still count. (That last part is Claude's reading, for Andrew to confirm.)
2. **Inviting stays allowed on a read-only diary.** This is unchanged; invitation and permission endpoints were never guarded.
3. **Backfill is a paid feature (Plus and Family).** Free gets a grayed-out Backfill control with Upgrade now, and the API returns 403 `tier_limit` with `source: "backfill"`.
