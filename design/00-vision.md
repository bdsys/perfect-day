# Perfect Day — the core goal

Weigh every design decision against this page. A change that doesn't move Perfect Day closer to the goal below has to justify itself some other way, and a change that works against it needs Andrew's explicit OK. Andrew has other goals too; they'll be added here as he names them.

## The goal, in Andrew's words (2026-11-20, typos fixed)

> I wanted an automated online diary that I didn't have to touch very much. It would pull from my calendar, AI would write about it and automatically put in photos from that day that match me and my daughter. […] I just wanted a way for my wife and my mom to see the things me and my daughter do online without using Facebook and without me having to put a ton of effort and time into it.

> Like Facebook but not out in the open since it's kids. More private and intentful.

## What that means

1. **Almost no effort from the parent who keeps the diary.** The app finds what happened (calendar, photos, weather), writes it up, and gets it to the family. Every step the parent has to take is a cost. Prefer a sensible hidden default to a setting, and a setting to a chore (see also the "magic" principle: busy parents need it to just work).
2. **The family sees it.** The diary is written to be read by a few chosen people: today, Andrew's wife and mom. An entry nobody sees has only done half its job. They read; helping write is a maybe.
3. **Private and deliberate.** Only people the parent invited can see anything, never "anyone with the link", never public, because it's a child's life. Nothing private travels by email: emails carry a link, not photos or entry text. Coordinates go to the owner only.
4. **True.** The AI writes only from facts in the source data, never invented feelings or details. The family is trusting what they read.

## Where it stands (2026-11-20)

- **Calendar → AI entry:** works.
- **Photos from that day, automatically:** blocked on the web. Google closed automatic library access on 2025-03-31. The remaining route is the phone app reading the camera roll (`design/11-mobile-app.md` M-1). Today photos are added by hand.
- **Getting it to the family:** being designed (Phase 3 #19, diary sharing: viewers by invitation, email and phone notifications, a per-rule choice of how entries get shared).

## How to use this page

When weighing a choice, ask:

- Does it reduce or add work for the parent who keeps the diary?
- Does it help the family actually see the entries?
- Does it keep the diary private to the people invited?
- Does it keep what the family reads true to the source data?

Say in the design which of these a choice serves or costs.
