# PRD (v2)

# PRD: Clear Checked Items from Shopping List

## Problem & Context
The Meal Planner's shopping list lets household members check off items as they're purchased. Today, the only way to remove a checked item is to delete it individually (one tap/swipe per item). During or after a shopping trip, a list can easily have 10–20 checked items, making cleanup tedious and error-prone. This is a pure CRUD/UX feature on the existing shopping list surface — it does not involve the chat/agent interface.

## Data Model & Scoping (Confirmed)
This feature operates on the existing `shopping_list_items` table with the following confirmed shape:
- `id` (pk)
- `household_id` (fk, not null) — every list item belongs to exactly one household; there is no separate multi-list-per-household entity today, so "the list" is scoped by `household_id`.
- `name`
- `checked` (boolean, not null, default false)
- `position` (integer, used to preserve display order)
- `created_at` / `updated_at`

There is no `deleted_at`/soft-delete column on this table today, and the existing single-item delete action performs a **hard delete** (`DELETE FROM shopping_list_items WHERE id = $1 AND household_id = $2`). To remain consistent with existing per-item delete semantics, "Clear checked items" also performs a **hard delete** — no soft-delete/`deletedAt` is introduced by this feature. If the architect's confirmation during implementation reveals a different actual schema, engineering should update this section and the query below accordingly, but this document is the design source of truth going in.

## Cross-Member Sync Model (Confirmed)
The shopping list UI today uses client-side refetch-on-focus plus periodic polling (no websocket/real-time push). This feature does not change that model:
- The acting user's UI updates immediately from the mutation response (optimistic removal of checked rows, reconciled with the server response).
- Other household members see the change on their next poll interval or when their client refocuses/refetches, exactly as with any other existing list mutation (add/edit/check/delete). No new invalidation, websocket event, or push mechanism is introduced for this feature.

## Goals
- Let a user remove all checked items from the shopping list in a single action.
- Keep unchecked items untouched and in their original order.
- Make the action safe against accidental data loss (confirmation before destructive bulk delete).
- Reflect the change immediately in the UI for the acting user; other household members see the updated list via the existing polling/refetch-on-focus sync model described above (no new sync infrastructure required).

## Non-Goals
- No "undo" / restore of cleared items (out of scope for this iteration; consistent with hard-delete decision above).
- No selective/partial clearing (e.g., "clear checked items in aisle X").
- No changes to how items get checked/unchecked, added, or edited.
- No real-time multi-device push sync beyond whatever the app already does today (polling/refetch-on-focus).
- No analytics/telemetry beyond what already exists for list mutations.
- No changes to the AI/chat recipe-surfacing flow.
- No special handling for shopping-list regeneration from planned recipes occurring concurrently with a clear operation — see Acceptance Criteria for why this is safely covered by the atomic delete, not treated as a separate case.

## User Stories
1. As a household member finishing a shopping trip, I want to clear all the items I've checked off with one tap, so I don't have to delete them individually.
2. As a household member, I want to be warned before I permanently remove checked items, so I don't accidentally lose my list.
3. As a household member, if the list has no checked items, I don't want a "clear" control cluttering the UI.
4. As a household member, if the clear action fails (e.g., network error), I want to know it failed and see my list unchanged, so I can retry.

## Acceptance Criteria
- A "Clear checked items" button/control is visible on the shopping list screen whenever at least one item on the list is checked, and is **not rendered at all (fully hidden from the DOM, not merely disabled)** when zero items are checked. This is a final decision, not deferred to design preference.
- Tapping the button opens a confirmation dialog stating how many items will be removed (e.g., "Remove 6 checked items?") with "Cancel" and "Remove" actions. This confirmation is always shown regardless of checked-item count (including a count of 1) — no threshold-based skip.
- Tapping "Cancel" closes the dialog and makes no changes to the list.
- Tapping "Remove" triggers a single request to delete all currently-checked items from the list and closes the dialog.
- **Implementation constraint (required, not optional):** the delete must be performed as a single atomic server-side query scoped by household and checked state — e.g. `DELETE FROM shopping_list_items WHERE household_id = $1 AND checked = true` — not a client-driven "fetch checked IDs, then delete by ID" flow. This is required so that:
  - Race condition safety: if the set of checked items changes between dialog-open and confirm (e.g., another household member unchecks an item), only items still checked at the moment the DELETE executes are removed; items unchecked in the meantime are preserved automatically because they no longer match the WHERE clause. No separate "re-check checked state" step is needed.
  - Concurrent list regeneration safety: if the shopping list is regenerated from planned recipes while a clear operation is in flight, newly-inserted items are unaffected as long as they are inserted as `checked = false` (per existing behavior of recipe-generated items). Any newly-inserted item that happens to already be checked at insert time is legitimately included in the delete — this is expected, not a bug, and requires no special-casing beyond the atomic WHERE clause already specified.
- After removal, unchecked items remain on the list, unchanged and in their original relative order (`position` untouched).
- After removal, the "Clear checked items" button disappears (no checked items remain) unless new items are checked afterward.
- The button and confirmation "Remove" action are disabled/show a loading state while the delete request is in flight, preventing duplicate submissions.
- If the delete request fails, the list remains unchanged, a visible error message (e.g., toast) is shown, and the button returns to its normal enabled state for retry.
- The clear action only affects the shopping list of the household making the request (enforced by the `household_id` predicate in the atomic DELETE above); it cannot remove or affect items on another household's list.

## UX Notes
- Placement: button appears in the shopping list header/toolbar area, near existing list-level controls (not per-item), so it reads as a bulk/list-level action.
- Label: "Clear checked items" (avoid ambiguous "Clear" alone, which could be confused with clearing the whole list).
- Confirmation dialog copy should include the count of items to be removed to set clear expectations, since this is irreversible.
- Use the app's existing destructive-action styling (if one exists) for the "Remove" confirmation button, consistent with other delete actions in the app.
- Empty-state: if clearing results in an empty list (all items were checked), show the existing "no items" empty state — no new empty-state copy needed for this feature.
- Button visibility toggles fully (hidden ↔ rendered) rather than disabled ↔ enabled, per the Acceptance Criteria decision above; component tests should assert on presence/absence, not just disabled state.

## Risks / Open Questions
- If, during implementation, the architect finds the actual `shopping_list_items` schema differs from the Data Model section above (e.g., items are scoped to a separate `shopping_list` entity rather than directly to `household_id`, or a soft-delete column already exists elsewhere in the codebase), this PRD's query and scoping details must be updated before coding proceeds — but the default design going in is as stated above, not an open unknown.

---

## Revision history

### v1

# PRD: Clear Checked Items from Shopping List

## Problem & Context
The Meal Planner's shopping list lets household members check off items as they're purchased. Today, the only way to remove a checked item is to delete it individually (one tap/swipe per item). During or after a shopping trip, a list can easily have 10–20 checked items, making cleanup tedious and error-prone. This is a pure CRUD/UX feature on the existing shopping list surface — it does not involve the chat/agent interface.

Assumed current state (to confirm with architect): shopping list items are rows in a `shopping_list_items` table (or similar) scoped to a household/list, with a boolean `checked`/`isChecked` column. Items are added manually or generated from planned recipes. Multiple household members may view/edit the same list.

## Goals
- Let a user remove all checked items from the shopping list in a single action.
- Keep unchecked items untouched and in their original order.
- Make the action safe against accidental data loss (confirmation before destructive bulk delete).
- Reflect the change immediately in the UI for the acting user; other household members see the updated list on their next load/refresh (per existing sync model — see open issues).

## Non-Goals
- No "undo" / restore of cleared items (out of scope for this iteration).
- No selective/partial clearing (e.g., "clear checked items in aisle X").
- No changes to how items get checked/unchecked, added, or edited.
- No real-time multi-device push sync beyond whatever the app already does today.
- No analytics/telemetry beyond what already exists for list mutations.
- No changes to the AI/chat recipe-surfacing flow.

## User Stories
1. As a household member finishing a shopping trip, I want to clear all the items I've checked off with one tap, so I don't have to delete them individually.
2. As a household member, I want to be warned before I permanently remove checked items, so I don't accidentally lose my list.
3. As a household member, if the list has no checked items, I don't want a "clear" control cluttering the UI.
4. As a household member, if the clear action fails (e.g., network error), I want to know it failed and see my list unchanged, so I can retry.

## Acceptance Criteria
- A "Clear checked items" button/control is visible on the shopping list screen whenever at least one item on the list is checked.
- The button is hidden (or disabled, per UX decision below) when zero items are checked.
- Tapping the button opens a confirmation dialog stating how many items will be removed (e.g., "Remove 6 checked items?") with "Cancel" and "Remove" actions.
- Tapping "Cancel" closes the dialog and makes no changes to the list.
- Tapping "Remove" deletes all currently-checked items from the list and closes the dialog.
- After removal, unchecked items remain on the list, unchanged and in their original relative order.
- After removal, the "Clear checked items" button disappears (no checked items remain) unless new items are checked afterward.
- The button and confirmation "Remove" action are disabled/show a loading state while the delete request is in flight, preventing duplicate submissions.
- If the delete request fails, the list remains unchanged, a visible error message (e.g., toast) is shown, and the button returns to its normal enabled state for retry.
- If, between opening the confirmation dialog and confirming, the set of checked items changes (e.g., another household member unchecks an item), the delete operation only removes items that are still checked at the time the delete request is processed server-side (no removal of items unchecked in the meantime).
- The clear action only affects the shopping list of the household making the request; it cannot remove or affect items on another household's list.

## UX Notes
- Placement: button appears in the shopping list header/toolbar area, near existing list-level controls (not per-item), so it reads as a bulk/list-level action.
- Label: "Clear checked items" (avoid ambiguous "Clear" alone, which could be confused with clearing the whole list).
- Confirmation dialog copy should include the count of items to be removed to set clear expectations, since this is irreversible.
- Use the app's existing destructive-action styling (if one exists) for the "Remove" confirmation button, consistent with other delete actions in the app.
- Empty-state: if clearing results in an empty list (all items were checked), show the existing "no items" empty state — no new empty-state copy needed for this feature.

## Risks / Open Questions
- Confirm the exact current data model (table/column names, whether items are scoped by household or by a specific "shopping list" entity) before implementation.
- Confirm whether shopping list state is currently synced across household members via polling, refetch-on-focus, or real-time subscription — this determines how quickly other members see items disappear and whether any additional sync work is needed.
- Decide whether the button should be hidden entirely vs. shown-but-disabled when there are no checked items (affects layout stability); brief does not specify, defaulting to "hidden" unless architect/design prefers otherwise.
- Decide on hard delete vs. soft delete (e.g., `deletedAt`) for cleared items, consistent with existing single-item delete behavior — should match existing per-item delete semantics to avoid inconsistency.
- Confirm whether a confirmation dialog is desired for small counts (e.g., 1 checked item) or only above some threshold — brief implies simplicity, so default is to always confirm regardless of count unless architect disagrees.
