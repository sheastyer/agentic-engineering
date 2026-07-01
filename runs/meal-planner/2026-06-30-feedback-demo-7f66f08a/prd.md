# PRD (v2)

# PRD: Changelog

## Problem & Context
Users of Meal Planner have no way to discover what has changed between releases. New features, improvements, and fixes ship silently, leaving users unaware of updates that could change how they use the product. This reduces feature adoption and creates repeat support questions.

This PRD adds a read-only changelog surface: a page that lists released versions in reverse-chronological order, each showing the changes bundled in that release.

## Goals
- Provide a single, discoverable page where users can browse product updates organized by version.
- Display each version with its release date and a list of individual changes.
- Categorize each change (Added, Changed, Fixed, Removed) so users can scan quickly.
- Order versions newest-first; order changes within a version by a fixed category order, then by explicit sort position, then by insertion order.
- Source changelog content from a maintainable store (Postgres via Drizzle) so entries can be added over time via migrations without requiring an admin UI.

## Non-Goals
- No admin UI for authoring/editing changelog entries in this iteration. Entries are added exclusively via Drizzle migrations (see Content Publishing Process below).
- No per-user "unread updates" tracking, badges, or notification indicators.
- No email or in-app push notifications about new releases.
- No RSS/Atom feed or public API endpoint for the changelog.
- No filtering, search, or pagination (assumed volume is small; render all versions).
- No Markdown/rich-text rendering of change descriptions beyond plain text (links, images, embeds out of scope).
- No internationalization/localization of changelog content; dates are displayed in UTC.
- No integration with the agentic chat interface (the assistant will not surface changelog content).

## Access & Auth
`/changelog` is a **public, unauthenticated route**, consistent with how changelog/release-notes pages typically work elsewhere (marketing/support value in being linkable and shareable without login). It is excluded from any auth middleware/session-gating applied to the rest of the app. It renders outside the authenticated app shell (no user-specific chrome), but reuses the app's existing footer/nav components for visual consistency.

## Data Model
Two tables via Drizzle ORM over Postgres, using the repo's standard conventions: `uuid` primary keys (`uuid('id').defaultRandom().primaryKey()`) and `timestamp` columns declared with timezone support (`timestamp('col', { withTimezone: true })`), matching the pattern already used for other tables' `created_at` columns. If the architect finds the actual repo convention differs (e.g., serial ids), the migration should be updated to match before merge — but this is the default to build against, not an open question.

**`changelog_versions`**
- `id` (uuid, PK, default random)
- `version` (text, unique, e.g., "1.4.0") — displayed as the version label
- `released_at` (timestamp with time zone, not null) — full timestamp, stored in UTC. Using a timestamp (not date-only) guarantees deterministic ordering even when two versions release on the same calendar date.
- `created_at` (timestamp with time zone, default now)

**`changelog_entries`**
- `id` (uuid, PK, default random)
- `version_id` (FK -> changelog_versions.id, not null, on delete cascade)
- `category` (Postgres enum `changelog_category`, not null; fixed values: `added`, `changed`, `fixed`, `removed`). This is the final, canonical set for this iteration — no additional categories (e.g., "deprecated", "security") are supported; adding one later requires a migration to alter the enum.
- `description` (text, not null) — plain-text description of the change
- `sort_order` (integer, not null, default 0) — primary tiebreaker for ordering within a category
- `created_at` (timestamp with time zone, default now)

### Canonical ordering (authoritative — implement exactly as specified)
- **Versions**: `ORDER BY released_at DESC, created_at DESC, id DESC`.
- **Categories within a version**: fixed display order regardless of data — Added, Changed, Fixed, Removed. A category heading is only rendered if at least one entry in that category exists for that version.
- **Entries within a category**: `ORDER BY sort_order ASC, created_at ASC, id ASC`. The `id ASC` clause guarantees a deterministic result even when `sort_order` and `created_at` both collide.

## Content Publishing Process
Since there is no admin UI, all changelog content ships via Drizzle migrations:
- **Launch content**: a migration (or seed script run once at launch, committed as a migration for reproducibility) inserts the initial `changelog_versions` and `changelog_entries` rows.
- **Future entries**: each new release that warrants a changelog entry includes a small Drizzle migration file (part of that feature's PR, or a dedicated "changelog" PR) that inserts a new `changelog_versions` row (if it's a new version) and its `changelog_entries` rows. This follows the existing PR-based deploy process — no direct writes to production, no out-of-band content tooling.
- This process is documented in the repo's contribution docs so it's discoverable by anyone shipping a release-worthy change.

## User Stories
1. As an end user, I want to open a changelog page so I can see what has changed across releases.
2. As an end user, I want each release grouped by version and dated so I understand when updates shipped.
3. As an end user, I want changes labeled by type (added/changed/fixed/removed) so I can quickly scan for relevant updates.
4. As an end user, I want the newest release shown first so I see the latest updates without scrolling.
5. As an end user, I want to access the changelog without needing to log in.
6. As an end user, when there are no changelog entries, I want a clear empty state rather than a broken or blank page.

## UX Notes
- Route: a new App Router page at `/changelog`, public (no auth guard).
- Navigation entry point: add a "Changelog" link to the app's **existing footer** (the footer already appears on all main pages). This is the only navigation change in scope — no changes to main nav or settings menu.
- Layout: a vertical list of version blocks, newest first per the canonical ordering above. Each version block shows the version label (e.g., "v1.4.0") and the release date formatted as a date (e.g., "June 28, 2026"), computed from `released_at` in UTC, using the app's existing date-formatting utility.
- Within a version block, group changes by category with a visible category label/heading, always in the order Added, Changed, Fixed, Removed. Only render categories that have entries for that version.
- Each change renders as a list item showing its plain-text description.
- Empty state: when no versions exist, show a friendly message: "No release notes yet." — no version blocks or errors.
- Page must be readable/responsive on mobile (tested at 375px width) and desktop, matching existing typography and spacing tokens. No new design system.
- Page is server-rendered (data fetched on the server via Drizzle); no client-side data fetching required. Initial HTML response contains the full rendered changelog content.

## Acceptance Criteria
1. Visiting `/changelog` while unauthenticated (no session) renders the page successfully with a 200 response — no redirect to a login page.
2. Given ≥2 versions exist with distinct `released_at` values, they render top-to-bottom in `released_at DESC` order.
3. Given two versions share the same `released_at` timestamp, they render in `created_at DESC, id DESC` order (deterministic, stable across repeated requests).
4. Each version block displays its `version` label and its release date formatted per the app's existing date-formatting utility, in UTC.
5. Within a version, only categories with ≥1 entry are rendered, and when multiple categories are present they always appear in the order: Added, Changed, Fixed, Removed.
6. Given entries within a category have distinct `sort_order` values, they render in ascending `sort_order` order.
7. Given entries within a category share the same `sort_order`, they render in ascending `created_at` order, then ascending `id` order as final tiebreak.
8. Each rendered entry shows its `description` as plain text (no Markdown/HTML interpretation) inside a list item.
9. When zero rows exist in `changelog_versions`, the page renders the text "No release notes yet." and no version blocks, with no client-side or server-side error.
10. A "Changelog" link is present in the site footer on pages that currently render the footer, and it navigates to `/changelog`.
11. The page renders without horizontal overflow or broken layout at a 375px viewport width and at common desktop widths, using existing typography/spacing tokens.
12. Viewing the page's initial server-rendered HTML (e.g., via "view source" or a fetch without executing JS) contains the full changelog content — confirming no client-side-only data fetching is required.
13. Inserting a `changelog_entries` row with a `category` value outside `added`/`changed`/`fixed`/`removed` fails at the database level (enum constraint violation).
14. After running the launch migration, the page renders at least the seeded launch version(s) and their entries correctly grouped and ordered per the rules above.

## Risks / Notes for the Architect
- Data model above assumes uuid PKs and `timestamp with time zone` columns as the repo's standard convention; confirm against actual schema before merging the migration, and adjust column types/id strategy to match if the repo differs — but do not treat this as blocking design work, since the shape (columns, enum, FK, cascade) is otherwise final.
- Confirm the app's existing date-formatting utility can render a UTC timestamp as a date-only string consistently with other date displays in the app.


---

## Revision history

### v1

# PRD: Changelog

## Problem & Context
Users of Meal Planner have no way to discover what has changed between releases. New features, improvements, and fixes ship silently, leaving users unaware of updates that could change how they use the product. This reduces feature adoption and creates repeat support questions.

This PRD adds a read-only changelog surface: a page that lists released versions in reverse-chronological order, each showing the changes bundled in that release.

## Goals
- Provide a single, discoverable page where users can browse product updates organized by version.
- Display each version with its release date and a list of individual changes.
- Categorize each change (e.g., Added, Changed, Fixed) so users can scan quickly.
- Order versions newest-first; order changes within a version by category then insertion order.
- Source changelog content from a maintainable store (Postgres via Drizzle) so entries can be added over time without code deploys blocking content.

## Non-Goals
- No admin UI for authoring/editing changelog entries in this iteration. Entries are seeded/inserted via migration or seed script.
- No per-user "unread updates" tracking, badges, or notification indicators.
- No email or in-app push notifications about new releases.
- No RSS/Atom feed or public API endpoint for the changelog.
- No filtering, search, or pagination (assumed volume is small; render all versions).
- No Markdown/rich-text rendering of change descriptions beyond plain text (links, images, embeds out of scope).
- No internationalization/localization of changelog content.
- No integration with the agentic chat interface (the assistant will not surface changelog content).

## Data Model
Two tables via Drizzle ORM over Postgres:

**`changelog_versions`**
- `id` (uuid/serial, PK)
- `version` (text, unique, e.g., "1.4.0") — displayed as the version label
- `released_at` (timestamp, not null) — release date
- `created_at` (timestamp, default now)

**`changelog_entries`**
- `id` (uuid/serial, PK)
- `version_id` (FK -> changelog_versions.id, not null, on delete cascade)
- `category` (text/enum, not null; one of: `added`, `changed`, `fixed`, `removed`)
- `description` (text, not null) — plain-text description of the change
- `sort_order` (integer, not null, default 0) — tiebreaker for ordering within a category
- `created_at` (timestamp, default now)

Match existing Drizzle schema conventions in the repo (column naming, id strategy, timestamp helpers).

## User Stories
1. As an end user, I want to open a changelog page so I can see what has changed across releases.
2. As an end user, I want each release grouped by version and dated so I understand when updates shipped.
3. As an end user, I want changes labeled by type (added/changed/fixed/removed) so I can quickly scan for relevant updates.
4. As an end user, I want the newest release shown first so I see the latest updates without scrolling.
5. As an end user, when there are no changelog entries, I want a clear empty state rather than a broken or blank page.

## UX Notes
- Route: a new App Router page at `/changelog`.
- Add a navigation entry point to the changelog (e.g., footer link or existing nav/settings menu) consistent with current navigation patterns. Do not restructure existing navigation.
- Layout: a vertical list of version blocks, newest first. Each version block shows the version label (e.g., "v1.4.0") and the release date (formatted per existing app date formatting).
- Within a version block, group changes by category with a visible category label/heading (Added, Changed, Fixed, Removed). Only render categories that have entries.
- Each change renders as a list item showing its plain-text description.
- Empty state: when no versions exist, show a friendly message (e.g., "No release notes yet.").
- Page must be readable/responsive on mobile and desktop, matching existing typography and spacing tokens. No new design system.
- Page is server-rendered (data fetched on the server via Drizzle); no client-side data fetching required.

## Acceptance Criteria
(See checklist below; each item is testable.)

## Risks / Open Questions for the Architect
- Confirm id strategy (serial vs uuid) and timestamp helpers to match existing Drizzle schema.
- Confirm the exact navigation entry point (footer vs settings menu vs main nav) — brief says UI-impacting but does not specify placement.
- Confirm whether `/changelog` should be publicly accessible (unauthenticated) or gated behind auth like the rest of the app.
- Confirm the canonical set of change categories; PRD assumes added/changed/fixed/removed (Keep a Changelog subset). Adjust if product prefers fewer.
- Confirm content seeding approach for launch (seed script vs migration insert) and how future entries are added given no admin UI.
- Date formatting/timezone: confirm whether `released_at` is stored/displayed as date-only or timestamp, and which timezone is used for display.
