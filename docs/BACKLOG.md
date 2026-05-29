# Backlog

Loose collection of future improvements not currently scoped into a plan.

## UX / Navigation

- **Global navigation toolbar.** Add a persistent top or side nav with links to common destinations (Diaries, Photos, account/settings) so users don't have to navigate via per-page buttons. Currently each route adds its own ad-hoc links in the page header (e.g., `/diaries/[diaryId]` has Photos, Deleted entries, Auto-Creation Rules buttons in the action row). A shared layout component would reduce duplication and make the app feel more cohesive. Likely belongs in `apps/web/src/app/layout.tsx` or a new `<AppNav />` client component used across authenticated routes.
