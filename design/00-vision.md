# Perfect Day — the core goal

Weigh every design decision against this page. A change that doesn't move Perfect Day closer to the goal below has to justify itself some other way, and a change that works against it needs Andrew's explicit OK. Andrew has other goals too; they'll be added here as he names them.

## The goal, in Andrew's words (2026-11-20, typos fixed)

> I wanted an automated online diary that I didn't have to touch very much. It would pull from my calendar, AI would write about it and automatically put in photos from that day that match me and my daughter. […] I just wanted a way for my wife and my mom to see the things me and my daughter do online without using Facebook and without me having to put a ton of effort and time into it.

> Like Facebook but not out in the open since it's kids. More private and intentful.

## What that means

1. **Almost no effort from the parent who keeps the diary.** The app finds what happened (calendar, photos, weather), writes it up, and gets it to the family. Every step the parent has to take is a cost. Prefer a sensible hidden default to a setting, and a setting to a chore (see also the "magic" principle: busy parents need it to just work).
2. **The family sees it.** The diary is written to be read by a few chosen people: today, Andrew's wife and mom. An entry nobody sees has only done half its job. They read; helping write is a maybe.
3. **Private and deliberate.** Only people the parent invited can see anything, never "anyone with the link", never public, because it's a child's life. Nothing private travels by email: emails carry a link, not photos or entry text. Coordinates go to the owner only.
   - **Exception: link-only pages for people outside the diary** (Andrew, 2026-11-23). An invitation, a shared wish list and a "send me photos" page open for anyone holding their link, because parents from school or daycare need to RSVP, see the details and the wish list, or send photos, without signing up. They stay private in every other way:
     - They show only what the parent put on that page. Never diary entries, diary photos, the diary's places, or other guests' names, answers or photos.
     - The link is long and unguessable, never listed anywhere, and search engines are told not to index it.
     - The parent can turn a link off at any time, and every link stops working a week after its date (a request for photos: a week after it was made).
     - Guests' photos go only to the parent, who decides whether any of them enter the private diary.
     - What guests tell the parent about their children (allergies, food needs) is deleted from Perfect Day 30 days after the event; the nightly backups that still hold it expire within about 4 months.
     - Design: `docs/superpowers/specs/2026-11-23-invitations-and-wish-lists-design.md`.
4. **True.** The AI writes only from facts in the source data, never invented feelings or details. The family is trusting what they read.

## Where it stands (2026-11-23)

- **Calendar → AI entry:** works.
- **Photos from that day, automatically:** blocked on the web. Google closed automatic library access on 2025-03-31. The remaining route is the phone app reading the camera roll (`design/11-mobile-app.md` M-1). Today photos are added by hand.
- **Getting it to the family:** sharing is built and deployed (2026-11-23, PR #93): family see the diary by invitation, as viewers or, on Family, editors. Still to come: email and phone notifications (with email verification), a per-rule choice of how entries get shared, and a co-parent's calendar feeding the diary.

## How to use this page

When weighing a choice, ask:

- Does it reduce or add work for the parent who keeps the diary?
- Does it help the family actually see the entries?
- Does it keep the diary private to the people invited?
- Does it keep what the family reads true to the source data?

Say in the design which of these a choice serves or costs.
