# PRD (v2)

# PRD: Pantry Staples List for Household Profile

## Problem & Context

Generated grocery lists currently include common cooking staples (flour, oil, butter, sugar, salt, etc.) that most households keep stocked at all times. This creates noise: users must manually scan and mentally filter out items they already have, which undermines trust in the list and adds friction to the weekly planning flow.

The meal planner stores a household profile and surfaces recipes via a chat interface. Grocery lists are derived from the ingredients of the recipes planned for the week. This feature lets a household declare a set of "pantry staples" assumed to be on hand, so those items are either excluded from the grocery list or grouped into a clearly-labeled "Assumed on hand" section.

## Goals

- Allow a household to maintain a list of pantry staples as part of its profile.
- When generating a grocery list, reconcile recipe ingredients against the household's pantry staples.
- Present staples that appear in the week's recipes in a separate "Assumed on hand" section, visually distinct from the actionable grocery list, rather than silently dropping them (so users retain visibility and can override).
- Persist the staples list so it applies to all future grocery-list generations for that household.

## Non-Goals

- No quantity/stock tracking — a staple is a boolean "assumed present," not an inventory with amounts.
- No automatic detection, ML-based suggestion, or default seed list of staples. All households — new and existing — start with an empty staples list (see Data Model & Migration).
- No per-recipe or per-week overrides of the staples list; staples are a household-level setting.
- No expiry, shopping history, or replenishment reminders for staples.
- No changes to how recipes are surfaced or planned in chat, beyond the grocery-list output.
- No multi-household sharing or syncing of staple lists.

## User Stories

1. As a household member, I can view my current pantry staples in my household profile.
2. As a household member, I can add an item to my pantry staples (e.g. "olive oil").
3. As a household member, I can remove an item from my pantry staples.
4. As a household member, when I generate a grocery list for the week, recipe ingredients that match my staples appear under a separate "Assumed on hand" section instead of in the main grocery list.
5. As a household member with no staples configured, my grocery list behaves exactly as it does today (no "Assumed on hand" section shown).

## Data Model & Persistence

- Ingredients are stored as structured rows with separate `name`, `quantity`, and `unit` fields (existing recipe schema) — quantities/units are never embedded in the `name` string. The existing grocery-list assembly already reads the `name` field alone for display purposes. Staple matching therefore operates on `name` only; no quantity/unit stripping logic is needed.
- Pantry staples are stored as a `text[]` (Postgres array) column, e.g. `pantry_staples`, on the household profile table via Drizzle. Given there is no per-item metadata (no quantity, no expiry, no notes), a normalized child table is unnecessary overhead; the array column is the chosen schema.
- Staple strings are stored with their original user-entered casing/whitespace for display, but all comparisons (duplicate-check on add, and ingredient matching) are performed against a normalized form (lowercased, trimmed, internal whitespace collapsed).
- **Migration/backfill:** the new `pantry_staples` column is added with a `NOT NULL DEFAULT '{}'` (empty array). All existing households receive an empty staples list on migration — identical to the "zero staples" behavior in Non-Goals/User Story 5. No default/seed list is populated for any household, new or existing.

## Grocery List Assembly

- The grocery list is assembled **server-side**, at generation time, in the same server action/route handler that currently compiles the week's planned-recipe ingredients into the grocery list. The pantry-staples reconciliation (matching + splitting into "Assumed on hand") is implemented as a step in that same server-side pipeline, after ingredients are aggregated and before the response is returned to the client. No client-side reconciliation logic is introduced.
- This keeps the household's staples list (fetched server-side from Postgres) and the matching logic co-located with existing ingredient-aggregation code, and avoids shipping staple data to the client unnecessarily.

## Matching Behavior

- Normalization: lowercase, trim leading/trailing whitespace, collapse internal whitespace to single spaces. Applied identically to staple names and ingredient `name` values before comparison.
- Tokenization: split the normalized string into word tokens using a regex that treats whitespace, hyphens (`-`), commas, slashes (`/`), and parentheses as token boundaries (e.g. `/[\s,\-\/()]+/`). Punctuation is discarded, not treated as part of a token.
- Simple plural handling: before comparing two tokens, strip a single trailing `s` or `es` from each (e.g. `eggs` → `egg`, `tomatoes` → `tomato`). This is a lightweight heuristic, not full stemming, and only applies to whole-token comparison.
- Whole-word / whole-phrase match rule: a staple matches an ingredient if the staple's token sequence appears as a contiguous subsequence of the ingredient's token sequence, after the pluralization normalization above is applied to each token pair being compared.
- Worked examples:
  - Staple `"oil"` (tokens: `[oil]`) vs ingredient `"olive oil"` (tokens: `[olive, oil]`) → match (`oil` is a contiguous 1-token subsequence).
  - Staple `"oil"` vs ingredient `"boil water"` (tokens: `[boil, water]`) → no match (`boil` ≠ `oil`; no substring matching within a token).
  - Staple `"egg"` vs ingredient `"2 large eggs, beaten"` → name field is `"large eggs, beaten"` (quantity `2` lives in the separate `quantity` field, not `name`); tokens: `[large, eggs, beaten]` → `eggs` stems to `egg` → match.
  - Staple `"brown sugar"` (tokens: `[brown, sugar]`) vs ingredient `"light brown sugar"` (tokens: `[light, brown, sugar]`) → match (`[brown, sugar]` is a contiguous subsequence).
  - Staple `"sugar"` vs ingredient `"brown sugar"` → match.
  - Staple `"pepper"` vs ingredient `"red bell pepper (diced)"` → tokens: `[red, bell, pepper, diced]` → match on `pepper`.
- If an ingredient matches more than one configured staple, it is routed to "Assumed on hand" once, and is grouped/labeled under the **longest-token-sequence matching staple** (most specific match wins). Example: staples `"oil"` and `"olive oil"` both configured, ingredient `"olive oil"` → grouped under `"olive oil"`, not `"oil"`.
- If an ingredient matches any staple, it is routed to "Assumed on hand" instead of the main grocery list; otherwise it stays in the main grocery list.

## Assumed-on-Hand Aggregation

- "Assumed on hand" aggregates **once per matched staple per week**, not per recipe or per occurrence. If multiple recipes in the week call for an ingredient matching the same staple (e.g. three recipes each use "olive oil"), the staple appears as a single line item in "Assumed on hand" for the week.
- The label shown in "Assumed on hand" is the household's staple name as entered (original casing), not the matched recipe-ingredient text.
- This mirrors how the main grocery list already aggregates duplicate ingredients across recipes into a single line; no new aggregation mechanism is introduced, only a routing split applied before that existing aggregation/display step.

## Acceptance Criteria

- [ ] The household profile persists a list of pantry staples as a `text[]` column (`pantry_staples`) on the household profile table in Postgres via Drizzle, with a `NOT NULL DEFAULT '{}'` migration so existing households start with an empty list.
- [ ] The profile UI displays the current staples list and provides controls to add and remove individual staple items.
- [ ] Adding a staple that already exists (case-insensitive, trimmed, whitespace-collapsed) is a no-op and does not create a duplicate.
- [ ] Adding an empty or whitespace-only staple is rejected and surfaces an inline validation message.
- [ ] Removing a staple removes it from the persisted list and the UI updates without a full page reload.
- [ ] Grocery-list reconciliation happens server-side, in the existing server-side grocery-list assembly step, operating on the ingredient `name` field only (quantity/unit are separate fields and are not parsed out of `name`).
- [ ] Ingredient-to-staple matching follows the normalization, tokenization, plural-stripping, and contiguous-subsequence rules defined in Matching Behavior, including the worked examples (`oil`/`olive oil` matches, `oil`/`boil` does not, `egg`/`eggs` matches).
- [ ] When an ingredient matches multiple configured staples, it is assigned to exactly one — the staple with the longest token sequence.
- [ ] "Assumed on hand" contains at most one entry per matched staple per week (deduplicated across all recipes planned that week), labeled with the staple's original-cased name.
- [ ] When the household has zero staples, the grocery list output contains no "Assumed on hand" section and matches current behavior exactly.
- [ ] The "Assumed on hand" section is only rendered when it contains at least one item for the current week.
- [ ] Changes to the staples list take effect on the next grocery-list generation without requiring re-planning of recipes.
- [ ] All new code uses TypeScript and follows existing code style; any AI SDK calls use `maxOutputTokens` (not `maxTokens`).
- [ ] The feature ships via PR; no direct pushes to main.

## UX Notes

- Staples management lives within the existing household profile screen — add a labeled "Pantry staples" subsection rather than a new page.
- Input pattern: a single text input with an "Add" affordance; existing staples shown as a list of removable chips/rows. Follow the styling already used elsewhere in the profile form.
- In the grocery list output, render the actionable items first under the existing heading, then the "Assumed on hand" section beneath it, visually de-emphasized (e.g. muted heading + lighter treatment) to signal it's informational, not a to-buy list.
- Empty-state for staples subsection: a short line explaining what staples are ("Items you keep stocked — these won't clutter your grocery list").

## Risks & Open Questions (for the architect)

- Do we need a per-list override to "move an assumed item back to the buy list" for a given week, or is household-level management sufficient for v1? (Currently out of scope per Non-Goals; confirm this remains acceptable after first-run feedback.)
- The plural-stripping heuristic (trailing `s`/`es` removal) is intentionally simple and will mis-handle irregular plurals (e.g. "tomatoes" is handled, but words like "leaves"→"leaf" are not). This is an accepted v1 limitation, not a blocking gap — flag if real recipe data surfaces frequent false negatives.

---

## Revision history

### v1

# PRD: Pantry Staples List for Household Profile

## Problem & Context

Generated grocery lists currently include common cooking staples (flour, oil, butter, sugar, salt, etc.) that most households keep stocked at all times. This creates noise: users must manually scan and mentally filter out items they already have, which undermines trust in the list and adds friction to the weekly planning flow.

The meal planner stores a household profile and surfaces recipes via a chat interface. Grocery lists are derived from the ingredients of the recipes planned for the week. This feature lets a household declare a set of "pantry staples" assumed to be on hand, so those items are either excluded from the grocery list or grouped into a clearly-labeled "Assumed on hand" section.

## Goals

- Allow a household to maintain a list of pantry staples as part of its profile.
- When generating a grocery list, reconcile recipe ingredients against the household's pantry staples.
- Present staples that appear in the week's recipes in a separate "Assumed on hand" section, visually distinct from the actionable grocery list, rather than silently dropping them (so users retain visibility and can override).
- Persist the staples list so it applies to all future grocery-list generations for that household.

## Non-Goals

- No quantity/stock tracking — a staple is a boolean "assumed present," not an inventory with amounts.
- No automatic detection or ML-based suggestion of which items should be staples (beyond an optional static default seed list; see Open Issues).
- No per-recipe or per-week overrides of the staples list; staples are a household-level setting.
- No expiry, shopping history, or replenishment reminders for staples.
- No changes to how recipes are surfaced or planned in chat, beyond the grocery-list output.
- No multi-household sharing or syncing of staple lists.

## User Stories

1. As a household member, I can view my current pantry staples in my household profile.
2. As a household member, I can add an item to my pantry staples (e.g. "olive oil").
3. As a household member, I can remove an item from my pantry staples.
4. As a household member, when I generate a grocery list for the week, recipe ingredients that match my staples appear under a separate "Assumed on hand" section instead of in the main grocery list.
5. As a household member with no staples configured, my grocery list behaves exactly as it does today (no "Assumed on hand" section shown).

## Matching Behavior

- Matching between a recipe ingredient and a staple is case-insensitive and trims surrounding whitespace.
- Matching is done on a normalized ingredient name (lowercased, trimmed). A recipe ingredient counts as a staple match if its normalized name equals a normalized staple name, or if the normalized ingredient name contains the normalized staple name as a whole word (e.g. staple "oil" matches ingredient "olive oil"; staple "sugar" matches "brown sugar"). Substring matches that are not whole-word boundaries do not count (staple "oil" must not match "boil").
- If an ingredient matches any staple, it is routed to "Assumed on hand." Otherwise it stays in the main grocery list.

## Acceptance Criteria

- [ ] The household profile persists a list of pantry staples (list of item name strings) in Postgres via Drizzle, associated with the household.
- [ ] The profile UI displays the current staples list and provides controls to add and remove individual staple items.
- [ ] Adding a staple that already exists (case-insensitive, trimmed) is a no-op and does not create a duplicate.
- [ ] Adding an empty or whitespace-only staple is rejected and surfaces an inline validation message.
- [ ] Removing a staple removes it from the persisted list and the UI updates without a full page reload.
- [ ] When a grocery list is generated and the household has one or more staples, ingredients matching a staple (per the Matching Behavior rules) appear in a separate "Assumed on hand" section and are absent from the main grocery list.
- [ ] Ingredient-to-staple matching is case-insensitive, whitespace-trimmed, and whole-word (staple "oil" matches "olive oil" but not "boil").
- [ ] When the household has zero staples, the grocery list output contains no "Assumed on hand" section and matches current behavior exactly.
- [ ] The "Assumed on hand" section is only rendered when it contains at least one item for the current week.
- [ ] Changes to the staples list take effect on the next grocery-list generation without requiring re-planning of recipes.
- [ ] All new code uses TypeScript and follows existing code style; any AI SDK calls use `maxOutputTokens` (not `maxTokens`).
- [ ] The feature ships via PR; no direct pushes to main.

## UX Notes

- Staples management lives within the existing household profile screen — add a labeled "Pantry staples" subsection rather than a new page.
- Input pattern: a single text input with an "Add" affordance; existing staples shown as a list of removable chips/rows. Follow the styling already used elsewhere in the profile form.
- In the grocery list output, render the actionable items first under the existing heading, then the "Assumed on hand" section beneath it, visually de-emphasized (e.g. muted heading + lighter treatment) to signal it's informational, not a to-buy list.
- Empty-state for staples subsection: a short line explaining what staples are ("Items you keep stocked — these won't clutter your grocery list").

## Risks & Open Questions (for the architect)

- Where is the grocery list assembled today — server-side during generation, or client-side from planned recipes? This determines where the reconciliation logic and "Assumed on hand" split belong.
- How are recipe ingredients currently represented (free-text strings vs. structured names/quantities)? The whole-word matching approach assumes name strings; structured data may allow cleaner matching.
- Should we seed new households with a default staples list (e.g. salt, pepper, oil, flour, sugar, butter) or start empty? Defaults improve first-run value but risk incorrectly hiding items the user does want to buy.
- Do we need a per-list override to "move an assumed item back to the buy list" for a given week, or is household-level management sufficient for v1? (Currently out of scope, but confirm.)
- Schema decision: store staples as a JSON/text array column on the household profile vs. a normalized child table. Given no per-item metadata, an array column is likely sufficient — confirm with existing Drizzle conventions.
