# PRD (v1)

# PRD: Skill Level & Equipment-Aware Recipe Suggestions

## Problem & Context
The meal-planner agent currently suggests recipes based on the household profile (e.g., dietary preferences, household size) but has no awareness of the household's **cooking skill level** or **available equipment**. This produces impractical suggestions: recipes that require a grill for an apartment cook with only a stovetop, or advanced techniques (e.g., deboning, tempering chocolate) for a beginner. Users must mentally filter these out, eroding trust in the agent.

This feature extends the household profile to capture two new attributes — cooking skill level and available equipment — and threads them into the agent's context so suggestions respect the household's actual capabilities. For example, avoid grill recipes for an apartment cook and favor griddle recipes for a Blackstone owner.

## Goals
- Add a **cooking skill level** field to the household profile (single enum value).
- Add an **available equipment** field to the household profile (multi-select from a fixed catalog).
- Persist both fields via Drizzle/Postgres and expose them through the profile edit UI.
- Feed both fields into the agent's prompt context so recipe suggestions respect skill and equipment.
- Ensure the agent explicitly avoids suggesting recipes requiring equipment the household lacks, and prefers recipes suited to owned specialized equipment.

## Non-Goals
- No free-text/custom equipment entry beyond the fixed catalog (deferred).
- No per-recipe metadata schema or structured recipe database changes — the agent reasons over skill/equipment via prompting, not a filtered query.
- No inference of skill/equipment from chat history or usage.
- No onboarding flow redesign; fields are added to the existing profile edit surface.
- No migration UI/backfill prompt for existing households beyond safe nullable defaults.
- No per-member skill levels — skill is a single household-level value.

## User Stories
1. As a household member, I can set my cooking skill level in my profile so suggestions match my ability.
2. As a household member, I can select the equipment I own from a list so suggestions only use tools I have.
3. As a Blackstone/griddle owner, I see recipes that make good use of my specialized equipment.
4. As an apartment cook without a grill/oven, I do not receive recipes that require equipment I don't have.
5. As a returning user with an existing profile, my profile still loads and works even though I haven't set these new fields yet.

## Data Model
Extend the existing household profile table (Drizzle schema) with:
- `cookingSkillLevel`: nullable Postgres enum `beginner | intermediate | advanced`. Nullable so existing rows are valid; treated as "unspecified" when null.
- `equipment`: nullable JSONB array of enum string keys from a fixed catalog. Null/empty treated as "unspecified".

Fixed equipment catalog (initial set — keys stable, labels display-only):
`stovetop`, `oven`, `microwave`, `outdoor_grill`, `griddle` (e.g., Blackstone), `air_fryer`, `slow_cooker`, `instant_pot`, `blender`, `stand_mixer`, `food_processor`, `toaster_oven`.

Add a Drizzle migration (nullable columns, no data backfill required). Migration lands via PR.

## Agent Integration
- Include skill level and equipment (with human-readable labels) in the agent's system/context message when generating suggestions.
- Instruct the agent to: (a) not suggest recipes requiring equipment absent from the list when equipment is specified; (b) prefer recipes leveraging specialized owned equipment; (c) keep techniques within the stated skill level (beginner → simple techniques, no advanced knife work/specialty methods).
- When skill or equipment is unspecified (null/empty), the agent behaves as today (no additional constraint) — do not block suggestions.
- Continue using AI SDK v6 conventions: use `maxOutputTokens` (not `maxTokens`) in generateText/streamText calls.

## UX Notes
- Profile edit screen gains two controls, grouped under a "Cooking" or "Kitchen" section:
  - **Skill level**: single-select (radio group or dropdown) with three options and short helper text (e.g., "Beginner — simple recipes and basic techniques").
  - **Equipment**: multi-select checkboxes/chips using catalog labels. "Stovetop" and "Oven" reasonable defaults visually but not pre-checked (leave unspecified to avoid false negatives).
- Both fields are optional; no validation blocking save on empty.
- Match existing profile form styling and save/submit flow; keep changes minimal and focused.
- No new page/route; extend existing profile edit surface.

## Risks / Open Questions (for architect)
- Prompt reliability: the agent may occasionally violate constraints since filtering is prompt-based, not query-based. Acceptable for barebones scope, but architect should confirm no structured recipe filtering is expected.
- Equipment catalog is hardcoded — where should the canonical catalog (keys + labels) live so both UI and agent prompt share one source of truth?
- Should `stovetop` be implicitly assumed present even when equipment is unspecified, or strictly "unknown"? Current spec treats unspecified as no constraint.
- Enum vs. text column for skill level in Postgres — confirm preferred Drizzle pattern matching existing schema conventions.
- Token budget: appending equipment labels + skill to context is small, but confirm it fits within existing `maxOutputTokens`/context limits.

## Acceptance Criteria
- [ ] Household profile schema includes a nullable `cookingSkillLevel` field accepting only `beginner`, `intermediate`, or `advanced`.
- [ ] Household profile schema includes a nullable `equipment` field storing an array of keys from the fixed catalog.
- [ ] A Drizzle migration adds both columns as nullable with no required backfill; existing rows remain valid.
- [ ] The profile edit UI shows a single-select skill level control with the three options and helper text.
- [ ] The profile edit UI shows a multi-select equipment control listing all catalog items by label.
- [ ] Saving the profile with both fields empty succeeds without validation errors.
- [ ] Saving persists selected skill level and equipment, and they reload correctly on reopening the profile.
- [ ] When equipment is specified, the agent's context includes the owned equipment and it does not suggest recipes requiring equipment absent from the list.
- [ ] When a specialized item (e.g., griddle) is selected, the agent's context instructs preference for recipes leveraging it.
- [ ] When skill level is specified, the agent's context includes it and constrains technique complexity accordingly.
- [ ] When skill and equipment are both unspecified, agent behavior is unchanged from current baseline.
- [ ] All agent calls use `maxOutputTokens` (no `maxTokens`) so CI passes.
- [ ] Changes land via PR; no direct pushes to main.