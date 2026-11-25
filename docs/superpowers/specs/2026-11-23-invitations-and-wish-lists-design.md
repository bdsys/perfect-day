# Invitations, wish lists and guest photos (tracker rows 34 and 69)

Status: **approved by Andrew 2026-11-23** ("The design is good"). Items marked *(my call)* were proposed by Claude and approved with the rest; everything else Andrew chose in the conversation of 2026-11-23.

## Why

The core goal (`design/00-vision.md`): a diary the parent barely touches, that the family sees, privately. Diary sharing (PR #93) gets entries to the family. This project covers the moments around a child's life that involve people outside the diary, and that today cost the parent work: sending invitations, collecting RSVPs, telling everyone what the child wants, and chasing other parents for photos.

Andrew's words:

- "you'd use the PD platform to build out wishlists for a birthday party and then it would be included in the invitation page for others to see. Christmas is similar, but not as strongly linked to an invitation. Instead being more of a link to give to someone or even if a grandparent could just go into PD to see the wishlist when the holiday/birthday approaches. I see a grandparent saying 'hey it's almost the kids birthday/christmas let's go to PD to see if they have any invitations or wishlists published.' It removes the parent from having to remember to send it out to people or even remember that part."
- "I want invitations to be public. Parents at school/daycare may not want to sign up for PD, but still need to RSVP, see details and wishlists."
- On guest photos: "I was at a child's birthday party with Amelia. And her and another guest hit it off. Good buddies. I got their parents number and the mom sent me some photos. […] when we had Amelia's birthday it would have been fantastic for parents to share their pictures some how with me at least."
- On who sees wish-list claims: "Kids won't see this. They won't hear anything. It's really just an adults thing."

## The vision exception (to write into `design/00-vision.md` point 3)

Point 3 says "never 'anyone with the link', never public". Andrew decided (2026-11-23) that invitations are an exception, for the reason quoted above. The same rules cover the two other link-only pages this project adds (a shared wish list, and "send me photos"). Text to add under point 3:

> **Exception: link-only pages for people outside the diary** (Andrew, 2026-11-23). An invitation, a shared wish list and a "send me photos" page open for anyone holding their link, because parents from school or daycare need to RSVP, see the details and the wish list, or send photos, without signing up. They stay private in every other way:
> - They show only what the parent put on that page. Never diary entries, diary photos, the diary's places, or other guests' names, answers or photos.
> - The link is long and unguessable, never listed anywhere, and search engines are told not to index it.
> - The parent can turn a link off at any time, and every link stops working a week after its date (a request for photos: a week after it was made).
> - Guests' photos go only to the parent, who decides whether any of them enter the private diary.
> - What guests tell the parent about their children (allergies, food needs) is deleted from Perfect Day 30 days after the event; the nightly backups that still hold it expire within about 4 months (`design/08-security-privacy.md` § Backup).

## What gets built, in order

One design; four pieces, each its own plan, PR, deploy and phone check (Andrew agreed the order, 2026-11-23). Piece 1 also lays the shared public-page foundation (below), so its deploy waits for the edge review.

### Shared foundation (built with piece 1)

- **Links.** Each link-only page has a random token (`secrets.token_urlsafe(32)`, as `services/sharing.py` does). The parent must be able to copy the same link again (it's already in a group chat), so unlike diary-sharing invites the token can't be hash-only. *(My call:)* store a SHA-256 hash for lookup plus the token encrypted with the app's existing key machinery (the way Google tokens are kept), so a database dump or backup on its own gives no working links. "Turn link off" and "make a new link" (old one stops working) are both offered.
- **Drafts first** (answered by Andrew's own words, "see if they have any invitations or wishlists **published**"). A wish list or invitation is a draft, seen only by the owner and editors, until they tap **Publish**. Publishing puts it in "Coming up" for the family and creates its link. Unpublishing hides it again and turns the link off. Same draft/published idea as entries.
- **Lifetime.** A link works until a week after its page's date (party date, wish-list date; for "Ask for photos", a week after the link was made), or until the parent turns it off or unpublishes, the diary is deleted, or the owner's account is being deleted or is blocked (same rule as outbound calls in `entry_location.update_place_and_weather`).
- **A diary that went read-only** after a downgrade (`services/read_only.py`) *(my call, from the builder check)*: links already published keep working, so an invitation the school already has doesn't break days before the party; guests can still RSVP, claim and upload. The parent can't create or publish anything new until the diary is writable again, and adding guest photos to entries is blocked like any other edit.
- **Names on stranger-facing pages** *(my call, from the builder check)*. Pages never fall back to the owner's email (`person_name` in `services/sharing.py` does). Publishing anything with a link asks the owner for a display name if they have none. The child's name appears only where the parent typed it (a title, a note); nothing is taken from the diary (`subject_name`, diary name). Wording must stay true when editors exist, e.g. "Only Andrew, and anyone he's chosen to help keep this diary, will see your photos."
- **Visitor addresses** (found by the builder check, confirmed on the NUC 2026-11-23; tracker row 71, fixed first as its own change, Andrew's OK 2026-11-23). Cloudflare's `CF-Connecting-IP` reaches the edge, but Caddy trusts a different address than the FortiGate's, and uvicorn trusts no proxy, so the API sees one address for every visitor. Per-visitor limits here depend on that fix. The web app's server-side calls to the public API must pass the visitor's address on too, or every page view counts against the web container.
- **Public web pages** on `perfectday.andrewlass.com`, opening without sign-in (Approach A below). Every one sends `X-Robots-Tag: noindex, nofollow` and a matching robots meta tag, and `Referrer-Policy: no-referrer`, so clicking a store link on a wish list never hands the token to the shop. Outbound links get `rel="noopener noreferrer"`, and only `http`/`https` URLs are accepted.
- **Link previews** (Open Graph, the reason the web is SSR) show only the page's title and date, for example "You're invited: Emma's 6th birthday · Sat 14 Mar". No image. Chat apps fetch previews from their own servers, so nothing more goes in.
- **Public API** under its own prefix *(my call: `/v1/public/…`)*, no sign-in, its own per-IP rate limits through the existing `slowapi` limiter *(my call for starting values, adjustable after the edge review: page reads 60/min, RSVP and claim writes 20/hour, photo uploads 60/hour)*. Every write is checked against the page's caps (below) as well as the rate limit.
- **Edge review before piece 1 deploys:** Cloudflare WAF and rate-limit rules for the public paths, and the FortiGate WAF/IPS profile, recorded in `deploy/cloudflare.md` and `deploy/nuc.md`. If the review finds we need stricter rules for strangers than for family, fall back to Approach B (its own hostname).
- **Logs: tokens stay out of them** (Andrew, 2026-11-23: "let's keep it out of the logs"). These links last for months, work for anyone, and a guest's private link opens their allergy note, unlike diary invites (one use, 7 days, one email), whose tokens stay logged. Redact the token in Caddy's access log, uvicorn's access log and the path `core/errors.py` logs, in piece 1. Record both rulings side by side in `design/08-security-privacy.md`.
- **Names.** "Invitations" already means diary sharing in the code (table, `/v1/invitations`, model) and "events" means calendar events (`Entry.events`). *(My call:)* call the new thing a **party** internally (`parties`, `/v1/diaries/{id}/parties`); people see "Invitation".
- **Who manages what.** The owner and editors create and manage wish lists, invitations, photo requests and polls, and see RSVPs, claims, answers and guest photos. Viewers see lists and invitations in the diary, claim items and RSVP. Everything belongs to a diary: deleting the diary deletes it all; the account hand-over (#93) moves it with the diary.
- **"Coming up."** At the top of a diary, for everyone who can see the diary, wish lists and invitations whose date is within the next 6 weeks *(my call on the window)*, soonest first. This is what makes the grandparent case work without anyone sending anything.

### Piece 1: wish lists

- A wish list belongs to a diary and has a name and a date ("Emma's 6th birthday", 14 Mar; "Christmas 2026", 25 Dec), in the diary's timezone.
- **Items:** a name, an optional store link, an optional note ("size 6", "any colour"), and an optional "as many as you like" flag. No fetched pictures or prices from store links in this version (it means the NUC fetching shop pages, and it breaks often).
- **A pasted list.** Instead of, or as well as, items: a link to a list kept elsewhere (an Amazon wish list, say), shown as "Emma's list on Amazon". Claiming doesn't apply to that part; the shop handles it.
- **Claiming.** A giver taps "I'm getting this". Givers who aren't signed in type their name (required, short); signed-in family use their account name. Others see the item as **taken**, never by whom. The claimer sees "You're getting this" and can undo it (the browser remembers their claim; if it forgets, the parent can release any claim). An "as many as you like" item is never taken; it shows "2 people are getting this". The parent sees every claim and who made it (Andrew: it's an adults thing).
- **Seen by:** everyone who can see the diary, inside Perfect Day (and in "Coming up"), and anyone the parent gives the share link to. A list attached to an invitation shows on the invitation page through the invitation's link, so it needs no link of its own.
- **After the date:** it leaves "Coming up", the share link stops working a week later, and the list stays readable in the diary.
- **Limits** *(my call)*: no plan limit on wish lists (text only, cheap). Caps: 100 items per list, 50 claims per item.

### Piece 2: invitations and RSVP

- An invitation belongs to a diary: title, date, start and end time, place (a free-text address, shown to anyone with the link, Andrew chose option A), a short note, an optional "RSVP by" date, and either an attached wish list or "No gifts, please" (or neither).
- **RSVP without an account:** a name ("Sam's mum"), Yes / No / Maybe, number of adults and of children, and an allergy or food note. Afterwards the guest gets a private link to change their answer (shown with a Copy button; the browser also remembers it). A guest can delete their RSVP.
- **Family who can see the diary** see the invitation in the diary and in "Coming up", and RSVP signed in, without typing a name.
- **Guests see** the invitation, the wish list, and only a count ("12 coming"). The parent (and editors) see every answer.
- **Changes:** the parent edits; the page always shows the latest. Cancelling shows "This party is cancelled" until the link expires. Until notifications exist (design/09 item 20), guests see changes only when they open the page.
- **Afterwards:** allergy and food notes are deleted 30 days after the party; names and headcounts stay with the invitation until the parent deletes it.
- **Limits:** Free gets 1 invitation a month (Andrew, 2026-11-15) *(my call: counted by creation date in the owner's timezone; Plus and Family unlimited)*. A new `LimitSource` `"party"` and an "Invitations" row in `COMPETITION.md`'s tier table. Each invitation takes up to 100 RSVPs.

### Piece 3: guest photos and "send me photos"

- **Two ways in, one mechanism.** Every invitation has an "Add your photos" section, open from the party's start until its link expires. And separately, on any diary, the parent can tap **Ask for photos**, pick a day, and get a link to text to someone (Andrew's first story: the other mum at someone else's party). It also stops working after a week.
- **Uploading:** the sender gives their name; the page says, for example, "Only Andrew, and anyone he's chosen to help keep this diary, will see your photos. He may add them to a private family diary." (see Names, above). Photos only.
- **The day** for "Ask for photos" can be today or any past day (the other mum's photos arrive days after the party).
- **Limits** *(my call, from the builder check; Andrew to confirm)*: open photo requests at once: Free 1, Plus 5, Family 10 (a new `LimitSource` `"photo_request"` and a `COMPETITION.md` row). Invitations' own upload sections don't count. At most 500 photos waiting for review per diary; past that, uploads are refused with "This diary isn't taking more photos right now".
- **Caps:** 20 per sender, 200 per party or request, 50 MB per file (the existing `photos.MAX_BYTES`). Every photo is re-encoded on the NUC the way `services/avatar.py` does it (decode, turn upright, save without EXIF/XMP/ICC), which drops location and camera data and rejects anything that isn't a picture. *(My call: keep the taken-at time read before stripping, to suggest the right entry; store only the re-encoded copy, longest side at most 4096 px.)* Stored encrypted like every photo.
- **Seen by:** only the parent and editors (Andrew: option A). Never other guests.
- **Review queue:** for each photo, add it to an entry (defaulting to the diary's entry on that day, or a new entry for that day) or delete it. Unreviewed photos are deleted 30 days after upload. Adding counts against the plan's photos per entry (Free: 1). A sender can delete their own photos through their private link until the parent adds them.
- **Not in this project:** sending photos back out to another parent (sharing outward). New backlog row.

### Piece 4: polls

- The parent adds questions to an invitation ("Pizza or pasta?", "Can you stay to help clean up?"): one choice, several choices, or a short text answer. Answered with the RSVP, changeable through the guest's private link.
- Results only to the parent and editors. Text answers follow the allergy-note rule only if the parent marks the question as being about the children *(my call: no marking in this version; text answers are kept with the invitation)*.
- Caps: 10 questions per invitation, 10 choices per question, 500 characters per text answer.

## Goals

1. A grandparent who can see the diary finds an upcoming birthday or Christmas list, and the party invitation, by opening Perfect Day, without the parent sending anything.
2. A school or daycare parent with no account opens the invitation link, sees the details and the wish list, RSVPs, claims a gift and sends photos, without signing up.
3. The parent sees every RSVP, claim and photo in one place, and nothing leaves the diary without their choice.
4. Andrew can ask another parent for photos with one tap and a text message.

## Non-goals

- Email or phone notifications for invitations, reminders, RSVPs or claims. They wait for design/09 item 20 (Andrew, 2026-11-23: add them to the notifications backlog).
- Fetching pictures or prices from store links; buying through Perfect Day.
- Guests seeing other guests' names, answers or photos.
- Sending photos outward to other parents (backlog).
- Recurring events, ticketing, payment, seating.

## Anti-goals

- A link-only page that shows anything from the diary itself.
- Children's photos visible to anyone holding a forwarded link.
- Making the parent re-send or re-type what they already put in Perfect Day.

## Constraints

- Backend enforces every limit and role (CLAUDE.md); the web only hides.
- Deletion cascades (CLAUDE.md): diary deletion removes lists, claims, invitations, RSVPs, polls, guest photos and their stored objects; account deletion through the diary; hand-over moves them with the diary.
- Photos are encrypted before they reach Garage (CLAUDE.md).
- The NUC is shared and small (4 cores, 8 GB): re-encoding runs in the worker, not the request; caps above keep a leaked link from filling the disk.
- No inbound connections beyond the existing web and API hostnames (memory: product principle).
- Each deploy needs Andrew's OK and runs `40-update.sh`; migrations get a pre-deploy dump.

## Approaches

- **Chosen (Andrew, 2026-11-23): A, in the existing app at the existing addresses.** Public pages in the Next.js app, a separate public API prefix with its own limits, Cloudflare and FortiGate in front with a rule review. Reuses encrypted storage, photo checks, plan limits, deploy and backups.
- **Fallback: B, same app on its own hostname** (one label, so Universal SSL covers it). Stricter edge rules for strangers without touching family traffic; costs a DNS name, a FortiGate rule and a less familiar link. Taken only if the edge review shows the need.
- **Rejected: C, public pages on Cloudflare.** Copies other families' details and their children's photos into a second system we don't control, and keeps two systems in step.

## Weighed against the core goal

- **Less parent effort:** serves it. Family find lists and invitations themselves; one link replaces chasing RSVPs and photos. Costs: building the list and invitation once.
- **Family sees it:** serves it ("Coming up"); guest photos bring in pictures the parent couldn't take.
- **Private:** costs it, by Andrew's explicit exception, held to the safeguards above. Nothing from the diary is ever on a link-only page; guest photos reach only the parent.
- **True:** unchanged; nothing is AI-written here.

## How we'll know it works (each piece's phone check)

1. **Wish lists:** as owner, make a list dated within 6 weeks; as the viewer test account, see it under "Coming up" and claim an item; in a private tab with no sign-in, open the share link, see the item as taken, claim another with a name; as owner, see both claims and who made them. Turn the link off: the private tab shows "This link is no longer active".
2. **Invitations:** paste the link into a chat to see the preview (title and date only); RSVP from a signed-out phone with an allergy note; change it through the private link; as owner, see it; as the viewer, RSVP signed in; cancel and see the cancelled page.
3. **Photos:** upload from a signed-out phone; check the stored photo carries no GPS; add one to an entry, delete another; make an "Ask for photos" link and send yourself photos through it.
4. **Polls:** add two questions, answer as a guest, see results as owner only.
Plus each piece's `make test-all`, the dev-stack check, CI, and the smoke test after deploy.

## Left to the builder

Endpoint and response shapes, table layout and migrations, URL paths for the public pages, where "Coming up" and the review queue sit on screen, the exact copy, how the browser remembers a guest (cookie or local storage), the worker job for re-encoding, test layout, and the order of work inside each piece.
