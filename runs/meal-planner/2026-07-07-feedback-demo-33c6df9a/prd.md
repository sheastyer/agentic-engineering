# PRD (v1)

# PRD: Pantry Staples List

## 1. Problem & Context

Generated shopping lists currently include common pantry staples (salt, oil, flour, pepper, sugar, etc.) that most households already keep on hand. This clutters the list with items users don't actually need to buy, reducing trust in the list and forcing manual filtering. Users today have no way to declare what staples they keep on hand.

This feature lets a household maintain a list of pantry staples as part of their profile. When a shopping list is generated, any ingredient matching a declared staple is moved out of the main buy-list and shown in a separate, clearly-labeled **'Assumed on hand'** section at the bottom of the list.

## 2. Goals

- Allow a household to add, view, and remove pantry staples on their profile.
- Persist staples as part of the household profile (Postgres via Drizzle).
- When generating a shopping list, exclude ingredients that match a declared staple from the main list.
- Display excluded staples in an 'Assumed on hand' section at the bottom of the shopping list.
- Preserve existing behavior for households that have no staples defined (list is unchanged; no empty section shown).

## 3. Non-Goals (explicitly out of scope)

- No quantity, unit, or expiry tracking for staples — staples are name-only.
- No auto-suggestion, ML inference, or agent-driven detection of staples.
- No pre-seeded/default staples list for new households.
- No per-recipe or per-week override of staples.
- No fuzzy/semantic matching beyond the normalization rule defined in acceptance criteria (no synonyms like 'olive oil' == 'oil').
- No changes to recipe surfacing or the chat interface itself.
- No sharing/import/export of staples between households.

## 4. User Stories

1. As a household member, I want to add a staple (e.g. 'salt') to my profile so future shopping lists don't ask me to buy it.
2. As a household member, I want to see my current staples on the profile page so I know what is being filtered out.
3. As a household member, I want to remove a staple so it reappears on shopping lists if I stop keeping it on hand.
4. As a household member, when I view a generated shopping list, I want staples I've declared to appear under an 'Assumed on hand' section rather than in the items I need to buy.

## 5. Acceptance Criteria

See the checklist below (also enumerated in the structured field).

- A household profile can store zero or more pantry staples, each a non-empty string name.
- Staples are persisted in Postgres via Drizzle and associated with exactly one household.
- The profile UI shows a 'Pantry staples' section listing all current staples for the household.
- A user can add a staple via the profile UI; on success it appears in the list without a full page reload.
- A user can remove a staple via the profile UI; on success it disappears from the list without a full page reload.
- Adding a staple whose normalized name already exists for the household does not create a duplicate (add is idempotent; UI reflects a single entry).
- Name matching for both dedupe and shopping-list filtering is case-insensitive and trims leading/trailing whitespace (normalization = lowercase + trim).
- Attempting to add an empty or whitespace-only staple is rejected and shows a validation message; nothing is persisted.
- When a shopping list is generated, any ingredient whose normalized name matches a declared staple is excluded from the main buy-list.
- Excluded staples that actually appeared in the plan are listed under an 'Assumed on hand' section rendered at the bottom of the shopping list.
- If no plan ingredient matches any declared staple, the 'Assumed on hand' section is not rendered.
- If a household has no staples defined, the shopping list renders exactly as it did before this feature (no section, no filtering).
- Staples belonging to one household never affect another household's shopping list (household-scoped isolation).
- The 'Assumed on hand' section lists staple names only (no quantities/units), consistent with the name-only model.

## 6. Key UX Notes

- **Profile page:** Add a 'Pantry staples' section. A single text input + 'Add' button, and the current staples rendered as a list of chips/rows each with a remove ('x') control. Match existing profile-section styling and component patterns.
- **Validation feedback:** Inline message for empty/duplicate input; do not block the rest of the form.
- **Shopping list:** The 'Assumed on hand' section appears below the main buy-list, visually de-emphasized (e.g. muted heading + smaller/secondary text) to signal 'informational, not to buy'. Items are plain names.
- **Empty states:** If there are no staples, show a brief helper line (e.g. 'Add items you always keep on hand to keep them off your shopping list.') rather than an empty container.
- Keep changes minimal and consistent with existing App Router pages and components.

## 7. Risks / Open Questions for the Architect

- **Data model:** Staples as a separate `pantry_staples` table (household_id FK + name, unique on (household_id, normalized_name)) vs. a JSON/array column on the profile. A join table gives clean dedupe/isolation; a column is simpler. Recommend the architect confirm which fits current schema conventions.
- **Matching fidelity:** Normalization is lowercase+trim only. Ingredients like 'olive oil' won't match staple 'oil', and 'flour' won't match 'all-purpose flour'. This is intentionally out of scope but may generate user confusion — confirm acceptable for v1.
- **Where filtering runs:** Should staple filtering happen at shopping-list assembly time (server), keeping the raw plan intact, or be baked into stored list data? Recommend server-side at render/assembly so changing staples reflects on the next list without migration.
- **Ingredient name shape:** Depends on how ingredients are currently represented (structured name vs. free text). If free text, matching reliability varies — architect should confirm the ingredient field used for comparison.
- **Section for staples not in the plan:** Current spec only lists staples that actually appeared in the plan. Confirm we should NOT list all declared staples regardless of the plan.
- **AI SDK note:** No LLM call is required for this feature; if any generation code is touched, ensure `maxOutputTokens` is used (not `maxTokens`) per current AI SDK v6 conventions to avoid CI failure.
