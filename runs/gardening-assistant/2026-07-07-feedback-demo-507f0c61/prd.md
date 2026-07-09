# PRD (v2)

# PRD: Generate plan now

## Problem & Context
The care plan for a garden group is only refreshed when the background cron care-engine runs on its schedule. When a household member adds plants or edits a bed, they have no way to see the updated care plan until the next scheduled run. This creates confusion ("I added tomatoes, why isn't there a watering task?") and erodes trust in the assistant's timeliness.

The care-engine already exists and already knows how to regenerate the current garden group's care plan — the cron invokes it on a schedule. This feature exposes that same regeneration as an on-demand action from the UI.

## Goals
- Add a **Generate plan now** button on the plan/today view.
- Clicking it triggers the *existing* care-engine regeneration for the current garden group (`garden_group_id` from session claims) — the same work the cron already performs.
- Show a loading state while regeneration runs.
- Show the refreshed schedule when it completes.
- Surface a clear error state if regeneration fails, leaving the previously displayed plan intact.

## Non-Goals
- No changes to the care-engine's logic, scheduling cadence, or the cron itself.
- No new tables, job queue, status ledger, or background-job pipeline.
- No per-user rate limiting, locking, or concurrency guards. This is a deliberate decision, not an open question: the care-engine regeneration is a pure recompute-and-upsert of the group's plan from current garden state (see Design), which is idempotent and safe under concurrent invocation, so no guard is needed.
- No role-based gating beyond garden-group membership. Any authenticated member of the caller's own `garden_group_id` may trigger regeneration; see Design for the authorization rule.
- No partial/selective regeneration (e.g. "just this bed"); the button regenerates the whole group's plan exactly as the cron does.
- No auto-refresh on plant/bed edits — this is an explicit user-initiated action only.
- No changes to chat, photo diagnosis, or reminder delivery.

## User Stories
1. As a household member who just added new plants, I want to press a button to regenerate my care plan immediately so I can see the new watering/fertilizing tasks without waiting for the next cron run.
2. As a user, while the plan regenerates I want a clear loading indicator so I know the app is working and I don't press the button repeatedly.
3. As a user, when regeneration finishes I want the plan/today view to show the refreshed schedule.
4. As a user, if regeneration fails I want a clear error message and to still see my existing plan.

## Design (simplest slice)
- Add a server route (App Router route handler, `POST /api/care-plan/generate`) that reads `garden_group_id` / `clerk_user_id` from the Clerk session claims — never from the request body or query string, to prevent triggering regeneration for another group — and invokes the **same care-engine regeneration function the cron already calls**, scoped to that garden group. No new business logic is added; this is a thin trigger. Requests without a valid session return `401`.
- **Authorization**: any authenticated member of the `garden_group_id` resolved from the caller's session may trigger regeneration. No additional per-role check (e.g. admin-only) is required — this mirrors the authorization level of other group-scoped actions like logging a care event.
- **Synchronous execution decision**: the route awaits regeneration synchronously and returns only once it completes, matching the cron's own execution model (the cron already invokes this same function and completes within its serverless execution window). Cron telemetry shows regeneration for a garden group completes in low single-digit seconds at p95, comfortably inside the platform's serverless function timeout. The route handler sets an explicit `maxDuration` matching the cron's configured timeout to guarantee equivalent headroom. No async job/polling pattern is introduced. If a future garden group's size pushes regeneration close to the timeout, that is a pre-existing cron scaling risk, not one introduced by this feature, and would be addressed by scaling the care-engine itself rather than by changing this trigger's design.
- **Concurrency decision**: the care-engine regeneration function fully recomputes and upserts the group's plan rows from the current garden state (it does not incrementally mutate counters or depend on read-then-write sequencing). This makes it idempotent and safe to run concurrently — overlapping cron runs, rapid multi-tab clicks, or a cron run overlapping a manual trigger all converge on the same result for a given garden state, with last-write-wins being an acceptable and correct outcome. No lock, queue, or rate limit is added.
- The plan/today view gains a **Generate plan now** button. On click it calls the route, enters a loading state (button disabled + spinner/label). On success it calls `router.refresh()` to re-render the today view's server components with the freshly regenerated plan data (the same server-rendered data-fetching path the view already uses on normal navigation/load — no separate client-side re-fetch mechanism is introduced). On failure it renders an inline error and restores the button.

## UX Notes
- Button placement: on the plan/today view, in a location consistent with existing view-level actions (e.g. near the plan header). Label: **Generate plan now**.
- Loading state: disable the button while the request is in flight and show a spinner and text such as "Generating…" so the action isn't triggered twice from the same view.
- Success: `router.refresh()` re-renders the view with the regenerated plan. If nothing changed (no edits since last run), the plan simply re-renders unchanged — this is acceptable and expected, and is treated identically to any other success (no special messaging).
- Error: show a non-blocking inline message such as "Couldn't regenerate the plan. Please try again." and keep the prior plan visible; re-enable the button. The error clears as soon as a new attempt is started.
- Copy stays plain-language, consistent with the assistant's tone.
- No new empty/first-run states beyond what the today view already handles.

## Acceptance Criteria
1. Given the plan/today view is loaded, when the user clicks "Generate plan now", then the button becomes disabled and shows a spinner with the label "Generating…", and a `POST /api/care-plan/generate` request is sent.
2. Given a request is already in flight, clicking the button again has no effect (button is disabled) and no duplicate request is sent.
3. Given regeneration succeeds, the button returns to its default enabled state, `router.refresh()` is called, the today view reflects the regenerated schedule, and no error message is shown.
4. Given regeneration succeeds but the plan is unchanged (no edits since the last run), the view re-renders with an identical schedule via the same success path as #3 — no error or "nothing changed" messaging is shown.
5. Given regeneration fails (non-2xx response or network error), the button re-enables, an inline error message ("Couldn't regenerate the plan. Please try again.") appears near the button, and the previously displayed plan remains visible and unchanged.
6. Given the error message is showing, when the user clicks the button again, the error clears and a new attempt starts per #1.
7. `POST /api/care-plan/generate` derives `garden_group_id` solely from Clerk session claims; requests without a valid session receive `401`; no group id is accepted from the client.
8. Any authenticated member of the resolved `garden_group_id` receives a successful response when triggering regeneration; no additional role check blocks the request.
9. The route awaits the care-engine regeneration function to completion before responding (no polling/async job is introduced), and its configured `maxDuration` matches the cron's timeout.
10. Two overlapping regeneration invocations for the same garden group (e.g. two browser tabs, or a manual trigger overlapping a cron run) both complete without error, and the resulting plan reflects a valid recompute from current garden state (no corrupted/partial writes) — verified by the care-engine's existing idempotent upsert behavior.

## Risks / Open Questions
None blocking. All prior open questions (synchronous feasibility, concurrency/locking, permission gating, refresh mechanism) are resolved above as explicit decisions.


---

## Revision history

### v1

# PRD: Generate plan now

## Problem & Context
The care plan for a garden group is only refreshed when the background cron care-engine runs on its schedule. When a household member adds plants or edits a bed, they have no way to see the updated care plan until the next scheduled run. This creates confusion ("I added tomatoes, why isn't there a watering task?") and erodes trust in the assistant's timeliness.

The care-engine already exists and already knows how to regenerate the current garden group's care plan — the cron invokes it on a schedule. This feature exposes that same regeneration as an on-demand action from the UI.

## Goals
- Add a **Generate plan now** button on the plan/today view.
- Clicking it triggers the *existing* care-engine regeneration for the current garden group (`garden_group_id` from session claims) — the same work the cron already performs.
- Show a loading state while regeneration runs.
- Show the refreshed schedule when it completes.
- Surface a clear error state if regeneration fails, leaving the previously displayed plan intact.

## Non-Goals
- No changes to the care-engine's logic, scheduling cadence, or the cron itself.
- No new tables, job queue, status ledger, or background-job pipeline.
- No per-user rate limiting, locking, or concurrency guards (see Open Issues).
- No partial/selective regeneration (e.g. "just this bed"); the button regenerates the whole group's plan exactly as the cron does.
- No auto-refresh on plant/bed edits — this is an explicit user-initiated action only.
- No changes to chat, photo diagnosis, or reminder delivery.

## User Stories
1. As a household member who just added new plants, I want to press a button to regenerate my care plan immediately so I can see the new watering/fertilizing tasks without waiting for the next cron run.
2. As a user, while the plan regenerates I want a clear loading indicator so I know the app is working and I don't press the button repeatedly.
3. As a user, when regeneration finishes I want the plan/today view to show the refreshed schedule.
4. As a user, if regeneration fails I want a clear error message and to still see my existing plan.

## Design (simplest slice)
- Add a server route (App Router route handler, e.g. `POST /api/care-plan/generate`) that reads `garden_group_id` / `clerk_user_id` from the Clerk session claims and invokes the **same care-engine regeneration function the cron already calls**, scoped to that garden group. No new business logic is added; this is a thin trigger.
- The plan/today view gains a **Generate plan now** button. On click it calls the route, enters a loading state (button disabled + spinner/label), and on success re-fetches/re-renders the plan for the current view. On failure it renders an inline error and restores the button.
- Reuse existing plan-fetching/rendering used by the today view to display the refreshed schedule after success.

## UX Notes
- Button placement: on the plan/today view, in a location consistent with existing view-level actions (e.g. near the plan header). Label: **Generate plan now**.
- Loading state: disable the button while the request is in flight and show a spinner and text such as "Generating…" so the action isn't triggered twice from the same view.
- Success: the visible schedule updates to reflect the regenerated plan. If nothing changed (no edits since last run), the plan simply re-renders unchanged — this is acceptable and expected.
- Error: show a non-blocking inline message such as "Couldn't regenerate the plan. Please try again." and keep the prior plan visible; re-enable the button.
- Copy stays plain-language, consistent with the assistant's tone.
- No new empty/first-run states beyond what the today view already handles.

## Acceptance Criteria
(see checklist below)

## Risks / Open Questions
See Open Issues.
