# PRD: Dark Mode Theme Toggle — v3 (architect-approved)

> **Artifact** from the run in [walkthrough-dark-mode.md](../walkthrough-dark-mode.md).
> Authored by the `pm_write_prd` persona (Opus), revised twice by `pm_revise_prd` (Sonnet) in
> response to the `architect_review_prd` persona (Opus). This is **v3** — the version the
> architect approved on pass 3. Reproduced verbatim from the persisted `trace_artifacts` table.

## 1. Problem & Context

The Meal Planner app currently ships with a single light theme. Users in low-light environments experience eye strain, and many users expect a dark theme as a baseline preference in modern web apps. There is currently no mechanism to switch themes, and no persistence of any visual preference.

This feature adds a user-facing toggle that lets users switch between light and dark themes. The selected preference must persist across sessions (page reloads and return visits) so the user does not have to re-select it each time.

The app is TypeScript + Next.js (App Router) with Drizzle ORM over Postgres. This feature is UI-impacting and touches the global layout/shell.

## 2. Goals

- Provide a visible, accessible toggle control to switch between **light** and **dark** themes.
- Apply the chosen theme across the entire app, including the chat interface and any surrounding shell/navigation.
- Persist the user's preference across sessions on the same browser.
- On first load, before any user interaction, respect the OS-level preference (`prefers-color-scheme`) as the default; once the user explicitly chooses, their explicit choice takes precedence.
- Avoid a flash of incorrect theme (FOUC) on initial page load.

## 3. Non-Goals (explicitly out of scope)

- **No** persistence of theme preference to the database / household profile. Persistence is client-side only via a cookie (see Section 6). Cross-device sync is out of scope.
- **No** themes beyond light and dark (no "system" as a separately selectable third option in the toggle UI; system preference is only the initial default).
- **No** custom color picking, accent colors, or per-component theming.
- **No** redesign of existing components beyond what is required to make them render correctly in dark mode.
- **No** changes to the agentic meal-planning logic, recipe surfacing, or household profile model.
- **No** server-side per-user theme storage or auth-related changes.
- **No** live reactivity to mid-session OS theme changes (see Section 7).

## 4. User Stories

1. As a user in a low-light environment, I want to switch the app to a dark theme so that I reduce eye strain while planning meals.
2. As a user who prefers dark interfaces, I want the app to remember my theme choice so that it stays dark every time I return without me re-selecting it.
3. As a first-time user whose OS is set to dark mode, I want the app to open in dark mode by default so it matches my system expectations.
4. As a user, I want the toggle to be easy to find and operate (keyboard and screen-reader accessible) so I can change themes regardless of input method.

## 5. Acceptance Criteria

- A theme toggle control is visible in the persistent app shell/header on all primary pages, including the chat interface.
- Activating the toggle switches the entire visible UI across the in-scope surfaces listed in Section 6 (chat interface, shell/header, navigation, recipe cards, form inputs, and buttons) between light and dark themes.
- When no explicit preference has been set, the initial theme matches the OS `prefers-color-scheme` setting (dark OS => dark theme on first load) using the hybrid mechanism described in Section 6. On browsers that do not support client hints (Safari, Firefox, and pre-negotiation Chrome), a small inline script is the accepted fallback and does not constitute FOUC.
- After a user explicitly selects a theme, that explicit choice persists across page reloads in the same browser.
- After a user explicitly selects a theme, that choice persists across a new session (closing and reopening the tab/browser) in the same browser.
- Once an explicit choice exists, it takes precedence over the OS `prefers-color-scheme` value.
- On initial page load there is no visible flash of the wrong theme (FOUC) before the correct theme is applied, subject to the client-hint negotiation caveat in Section 6.
- The toggle is operable via keyboard (focusable, activatable with Enter/Space) and exposes an accessible name and current state to assistive technology.
- Text and interactive elements meet WCAG AA contrast (>= 4.5:1 for normal text) in both light and dark themes, verified using the CSS variable values defined in Section 6.
- Theme switching does not trigger a full page reload and does not interrupt or clear in-progress chat state. "In-progress chat state" is defined as: (a) any unsent text present in the chat input field, (b) any streaming/in-flight assistant response currently rendering, and (c) the chat scroll position. All three must be preserved when the theme is toggled. Toggling must not cause a React re-mount, a client-side navigation, or a revalidation of the chat route (see Section 6 for the cookie-write approach that satisfies this).

## 6. UX Notes

### Persistence, FOUC Prevention, and First-Visit Default

Persistence **must** use an HTTP cookie (name: `mealplanner-theme`, value: `light` | `dark`, max-age 1 year, SameSite=Lax). `localStorage` is **not** used for this feature.

**Cookie-present case (all visits after the first explicit choice):** The root layout Server Component reads the cookie, derives the `data-theme` attribute value (`light` or `dark`), and renders `<html data-theme="...">` before any JavaScript executes. No inline script is needed for this case.

**First-visit case (no cookie present) — two-tier approach:**

1. *Tier 1 — Client Hints (Chrome/Edge, after negotiation):* The app must send `Accept-CH: Sec-CH-Prefers-Color-Scheme` and `Critical-CH: Sec-CH-Prefers-Color-Scheme` response headers from the root route (configured in `next.config.js` via `headers()` or equivalent middleware). On Chrome/Edge, after the first round-trip, the browser will include `Sec-CH-Prefers-Color-Scheme` on subsequent requests, allowing the server to render the correct `data-theme` with no client-side script.
2. *Tier 2 — Inline script fallback (Safari, Firefox, and the very first Chrome/Edge request before negotiation):* When no cookie is set and no client hint is available, the server renders `<html data-theme="light">` as a safe default and injects a **small, synchronous inline script** in `<head>` — before any stylesheet or body content — that reads `window.matchMedia('(prefers-color-scheme: dark)').matches` and, if true, immediately sets `document.documentElement.setAttribute('data-theme', 'dark')`. Because this script runs synchronously before paint, it corrects the attribute before the browser renders any pixels, satisfying the no-FOUC requirement on all browsers. This is the only inline script in the feature.

*Accepted limitation:* On the very first request from a Chrome/Edge browser (before client-hint negotiation completes), the server cannot detect OS preference, so the server sends `data-theme="light"` and the inline script corrects it client-side. This one-round-trip correction is invisible to users (script runs before paint) and is the accepted behavior.

**Toggle interaction — client-side cookie write without reload:**
When the user activates the toggle, the client must perform both of the following steps synchronously, with no page navigation, no React re-mount, and no revalidation of any route:
1. Directly mutate `document.documentElement.setAttribute('data-theme', newTheme)` to apply the new theme immediately.
2. Write the cookie directly via `document.cookie = 'mealplanner-theme=<value>; max-age=31536000; path=/; SameSite=Lax'` so that the next SSR request will read the correct value.

A server action is **not** used to write the cookie on toggle, because a server action would trigger a round-trip that could disrupt streaming chat responses. The cookie is written entirely client-side. Because the `data-theme` mutation happens synchronously in the same event handler, there is no re-render or re-mount of chat components; streaming state, unsent input text, and scroll position are all preserved.

### Contrast Verification

**Tool:** Automated contrast checking runs in CI via a **Playwright** test (no Storybook — Storybook is not present in the repo and will not be introduced). The Playwright test renders the app in a headless browser, sets `data-theme` to each value in turn, and uses an accessibility audit library (e.g. `axe-playwright` / `@axe-core/playwright`) to assert contrast. This is the single required verification method.

**Contrast pairs to verify (both themes):**

| Foreground token | Background token | Minimum ratio | Notes |
|---|---|---|---|
| `--color-text-primary` | `--color-bg` | 4.5:1 | Body text |
| `--color-text-primary` | `--color-surface` | 4.5:1 | Text on cards/inputs |
| `--color-accent-text` | `--color-accent` | 4.5:1 | Text on accent buttons/links |
| `--color-text-secondary` | `--color-bg` | 3:1 | Helper/placeholder text — treated as UI component text under WCAG 1.4.11 (non-text contrast), exempt from 4.5:1 body-text requirement; 3:1 minimum applies |
| `--color-text-secondary` | `--color-surface` | 3:1 | Same exemption as above |

`--color-text-secondary` (#A0A0A0 in dark) is explicitly classified as placeholder/helper text and is tested at the 3:1 threshold (WCAG 1.4.11) rather than 4.5:1.

### Dark Color Palette

The dark theme palette is defined as follows via CSS custom properties on `[data-theme="dark"]`. These values are the source of truth for contrast verification:

| Token | Dark value | Usage |
|---|---|---|
| `--color-bg` | `#121212` | Page / shell background |
| `--color-surface` | `#1E1E1E` | Cards, inputs, modals |
| `--color-border` | `#333333` | Dividers, input borders |
| `--color-text-primary` | `#E8E8E8` | Body text, labels |
| `--color-text-secondary` | `#A0A0A0` | Helper text, placeholders (3:1 threshold) |
| `--color-accent` | `#7CB9F4` | Links, primary buttons, focus rings |
| `--color-accent-text` | `#0A0A0A` | Text on accent-colored backgrounds |

All existing light-theme tokens must be refactored to use these same CSS variable names on `[data-theme="light"]` (or `:root` for the light default).

### In-Scope Components / Surfaces

1. App shell / header (including the theme toggle itself)
2. Navigation / sidebar (if present)
3. Chat interface — message list, user bubbles, assistant bubbles, streaming indicator
4. Chat input area — textarea, send button
5. Recipe cards surfaced in chat or as standalone list
6. Primary modal/dialog (household profile edit, if it exists in current codebase)
7. Global form inputs and buttons (shared component variants, not one-off usages)

**Shared component migration rule (Surface 7):** Shared form inputs and buttons are in scope for token refactoring. Out-of-scope pages that consume these shared components will implicitly gain dark-mode styling as a side effect. This is **acceptable and expected**, provided out-of-scope pages remain visually identical in light mode (the light-theme token values must not change). Engineers must include a light-mode visual snapshot test (Playwright screenshot) covering at least one out-of-scope consumer of Surface 7 components.

### Other UX Details

- **Placement:** Toggle lives in the persistent app header/shell so it is reachable from every page including chat.
- **Control type:** A single toggle (switch or icon button, e.g. sun/moon icon).
- **Theming mechanism:** Apply via `data-theme` attribute on `<html>` and CSS variables as defined above.
- **Accessibility:** Provide an `aria-label` or visible label and reflect state (e.g. `aria-pressed` or switch semantics). Ensure focus styles are visible in both themes.
- **Transitions:** A subtle CSS transition on color changes is acceptable but optional; must not cause layout shift.

## 7. Risks & Resolved Questions

- **SSR vs. FOUC — RESOLVED:** Two-tier approach: (1) `Accept-CH`/`Critical-CH` client hints for Chrome/Edge after negotiation, (2) synchronous inline script in `<head>` as fallback for Safari/Firefox and pre-negotiation first requests. Cookie-present case requires no script.
- **Dark palette — RESOLVED:** Dark CSS variable values are specified in Section 6. `--color-text-secondary` is tested at 3:1.
- **Coverage of existing components — RESOLVED:** In-scope surfaces enumerated in Section 6. Shared Surface 7 components may implicitly cover out-of-scope pages in dark mode; acceptable provided light-mode parity is confirmed via snapshot test.
- **System-preference reactivity — RESOLVED (Decision: NOT reactive):** No `matchMedia` listener at runtime. OS preference evaluated only at request time.
- **Scope of "system" option — RESOLVED:** Light/dark toggle only. No three-way selector.
- **Persistence key/storage — RESOLVED:** Cookie only (`mealplanner-theme`). No `localStorage`.
- **Cookie write on toggle — RESOLVED:** `document.cookie` write only (no server action).
- **Contrast verification tooling — RESOLVED:** Playwright + `@axe-core/playwright` in CI. No Storybook.
- **Shared component side effects — RESOLVED:** Out-of-scope pages gain implicit dark support as an accepted side effect; light-mode parity confirmed via snapshot.
