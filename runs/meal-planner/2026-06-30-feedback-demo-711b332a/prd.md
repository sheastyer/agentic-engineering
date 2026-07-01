# PRD (v1)

# Changelog Feature PRD

## Problem & Context

Meal Planner ships updates regularly, but end users have no in-app way to learn what changed between releases. New features go unnoticed, and users can't tell whether a bug they hit was already fixed. This erodes trust and reduces feature adoption.

This PRD adds a read-only, in-app changelog that organizes changes by version so each release's contents are visible to users.

## Goals

- Provide an in-app page where users can view changes organized by release version.
- Each version entry shows: version identifier, release date, and a list of changes.
- Each change is categorized (e.g., Added, Fixed, Changed) so users can scan quickly.
- Versions are displayed newest-first.
- Changelog content is maintainable by developers as part of the normal PR workflow (no admin UI needed).

## Non-Goals

- No admin/editor UI for authoring changelog entries (content is committed by developers).
- No per-user "what's new since you last visited" tracking, badges, or unread indicators.
- No email/push notifications about releases.
- No localization/translation of changelog text (English only for v1).
- No filtering, search, or pagination of versions.
- No integration with the chat/agentic meal-planning flow.
- No automatic generation of changelog content from git history or commits.

## Source of Truth & Data Model

Changelog content is stored as a static, version-controlled data file in the repo (TypeScript module), not in Postgres. This keeps the feature read-only, avoids a migration, and lets changes land via PR alongside the release they describe.

Proposed shape (`lib/changelog/data.ts`):

```ts
export type ChangeCategory = 'Added' | 'Changed' | 'Fixed';

export interface ChangelogEntry {
  category: ChangeCategory;
  description: string;
}

export interface ChangelogRelease {
  version: string;      // e.g. "1.4.0"
  date: string;         // ISO 8601 date, e.g. "2026-06-28"
  entries: ChangelogEntry[];
}

export const changelog: ChangelogRelease[] = [ /* newest first */ ];
```

## User Stories

1. As a Meal Planner user, I want to open a changelog page so I can see what has changed in the app.
2. As a user, I want changes grouped by version with a release date so I understand which release introduced a change.
3. As a user, I want changes labeled by category (Added/Changed/Fixed) so I can quickly find new features versus bug fixes.
4. As a user, I want the most recent release shown first so the latest information is immediately visible.
5. As a developer, I want to add a new release entry by editing a single data file in a PR so updating the changelog is part of the normal release flow.

## Acceptance Criteria

See the flat checklist in the `acceptance_criteria` field; criteria are duplicated here for the implementer:

- A changelog page is reachable at `/changelog` via the App Router.
- The page renders all releases from the changelog data source, ordered newest-first by version/date.
- Each release displays its version identifier and release date.
- Within a release, each change displays its category label and description text.
- When the changelog data source is empty, the page renders an empty-state message (e.g., "No changes to show yet.") and does not error.
- The changelog is read-only; the page exposes no controls to add, edit, or delete entries.
- A navigation affordance links users to `/changelog` (e.g., a footer or menu link).
- The page is keyboard-navigable and uses semantic headings (version as a heading, categories grouped accessibly).
- No database migration or Drizzle schema change is introduced by this feature.
- The page renders without authentication if the rest of the app's public pages do; otherwise it follows the app's existing auth convention for app pages.
- The build passes CI (no use of removed AI SDK v5 APIs; this feature should not touch AI SDK code at all).

## UX Notes

- Route: `/changelog` (App Router page, server component; static rendering since data is static).
- Layout: page title "Changelog"; a vertical list of release sections, newest at top.
- Each release section: version + date as the section header (e.g., "1.4.0 — Jun 28, 2026"); entries grouped under the header.
- Category presentation: each entry shows a small category label/badge (Added / Changed / Fixed) followed by the description. Use distinct but subtle styling consistent with the existing design system; do not introduce a new color palette.
- Empty state: friendly single-line message when no releases exist.
- Match existing typography, spacing, and component patterns already used in the app; keep changes minimal and focused.
- Add one navigation link to the changelog from the app's existing navigation (footer preferred to avoid crowding primary nav); confirm placement with the existing nav structure.

## Risks / Open Questions

- Ordering: should ordering be derived from the array order (developer-maintained) or sorted at render time by semver/date? Sorting by semver requires a comparison helper; array-order is simpler but error-prone. Recommend sorting by date descending with version as a tiebreaker — architect to confirm.
- Auth context: does the app currently gate all pages behind auth, or are there public pages? This determines whether `/changelog` should be public or authenticated. Needs confirmation against current routing/middleware.
- Navigation placement: exact location of the changelog link (footer vs. account menu vs. settings) depends on existing nav components — architect to confirm.
- Date formatting: confirm preferred display format and whether to use an existing date utility/locale rather than introducing a new dependency.
- Future scale: static data file is fine for v1; if release volume grows large, rendering all releases on one page may warrant pagination later (explicitly out of scope now).