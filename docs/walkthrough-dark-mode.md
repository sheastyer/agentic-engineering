# A real run, end to end: "add a dark mode toggle" → an opened PR

This is a **verbatim trace of one actual run** of the org — not a mock-up. A single piece of
feedback (*"Add a dark mode theme toggle"*) entered the [meal-planner](https://github.com/sheastyer/meal-planner)
target app and walked the whole feature pipeline — PM brief → exec council → PRD ⇄ architect
loop → consumer research → story breakdown → engineering pod — and came out the other end as a
real pull request. Every artifact below is quoted from the persisted `trace_artifacts` table
(see [persistence](#how-this-was-captured)).

- **Workflow id:** `feedback-demo-8d930b55` · **Date:** 2026-06-19
- **Reasoning:** live on the Vercel AI Gateway · **Coding:** Claude Agent SDK on a Claude subscription
- **Outcome:** 🟢 **[meal-planner PR #4](https://github.com/sheastyer/meal-planner/pull/4)** — a complete dark-mode feature (accessible toggle, FOUC-prevention, system-preference default, component refactor, Playwright tests)
- **Cost:** ~$0.38 reasoning + ~$1.87 coding

> New to the project? Read the [README](../README.md) for *why*, and
> [reference.md](./reference.md) for *how it's built*. This doc is *what one run actually looked like*.

### The stages, in order

| # | Stage | Agent (tier) | What it produced |
|---|---|---|---|
| 1 | [Feedback](#1-feedback-the-input) | — | the input |
| 2 | [PM brief](#2-pm-brief) | `pm_draft_brief` (Opus) | problem framing, target users, "is it UI-impacting?" |
| 3 | [Exec council](#3-exec-council-vote) | `council_legal` + `council_sales` (Sonnet) + 🧑 | two approvals + a human vote |
| 4 | [PRD ⇄ architect loop](#4-prd--architect-review-3-passes) | `pm_write_prd` (Opus) ⇄ `architect_review_prd` (Opus) | a PRD, rejected **twice**, approved on pass 3 |
| 5 | [UX mocks](#5-ux-mocks) | `ux_generate_mocks` (stub) | mock reference (conditional on UI-impacting) |
| 6 | [Consumer research](#6-consumer-research-panel) | `consumer_researcher` ×4 (Sonnet) | four synthetic reactions → "mixed" |
| 7 | [PM sign-off](#7-pm-sign-off) | 🧑 | human approved |
| 8 | [Story breakdown](#8-story-breakdown) | `architect_plan_stories` (Opus) | 10 estimated stories |
| 9 | [Engineering pod](#9-engineering-pod) | one Claude Agent SDK agent (Sonnet) | the whole feature, one diff |
| 10 | [PR opened + deploy gate](#10-pr-opened--deploy-gate) | `open_pr` + 🧑 | **PR #4**, then SHIPPED |

---

## 1. Feedback (the input)

A single normalized feedback event entered via the CLI driver:

> **Add a dark mode theme toggle to the app**

`kind=feature`, `project=meal-planner`. The `IntakeRouter` started a `FeatureRequestWorkflow`.

---

## 2. PM brief

The **`pm_draft_brief`** persona (Opus) reframed the raw request into a structured brief:

- **Summary:** Add a user-facing dark mode theme toggle to the Meal Planner app, allowing users to switch between light and dark visual themes, with the preference persisted across sessions.
- **Problem:** The app currently offers only a single (light) theme, which can cause eye strain in low-light environments and lacks a preference many users expect. There is no way to switch to a dark visual theme.
- **Target users:** All Meal Planner users interacting with the app's chat interface, particularly those using the app in low-light conditions or who prefer dark themes.
- **UI-impacting?** **Yes** → this is the flag that later turns on the conditional UX-mocks stage.

Note "toggle" is preserved here — it survives all the way to the story breakdown. (In an earlier
run it was a `CODING_MAX_STORIES=1` cap, *not* the reasoning, that dropped the toggle — see the
[postmortem](#appendix-the-bug-this-run-fixed).)

---

## 3. Exec council vote

Two agent council members judged the brief through their own lens, **in parallel**, then a human
cast the decisive vote.

**🏛️ Legal (`council_legal`, Sonnet) — APPROVE:**
> A dark mode toggle involves only UI theme preferences and local/session persistence of a non-sensitive user setting; there are no regulated data categories, health claims, third-party IP concerns, or meaningful privacy risks implicated. No concrete legal or compliance issue is present.

**💰 Sales (`council_sales`, Sonnet) — APPROVE:**
> Dark mode is a widely expected baseline feature that meaningfully reduces churn from users who find the light-only experience uncomfortable, and its absence can make the product feel unpolished compared to competing tools. Persisted preference adds a small but real retention signal with negligible opportunity cost.

**🧑 Human vote — APPROVE** (the driver auto-approves gates so the demo runs unattended; in a real
deployment this is a Temporal signal a human sends). The human vote is decisive; the agent votes
are advisory. → **Council approved.**

---

## 4. PRD ⇄ architect review (3 passes)

This is the heart of the "perspective machine": the PM authors a PRD, the architect tries to break
it, and the loop repeats (bounded at `MAX_PRD_PASSES=3`) until the architect approves. It took **all
three passes** — a great example of the org improving its own output.

### PRD v1 (`pm_write_prd`, Opus)

The PM authored a full PRD and was honest about what it *didn't* know, leaving six **open questions
for the architect** (SSR-vs-FOUC mechanism, dark palette source, component-refactor scope, etc.).

### → Architect review, pass 1 (`architect_review_prd`, Opus) — ❌ REJECTED, 5 concerns

The architect refused to approve and raised concrete, buildable objections. Abridged:

1. **FOUC approach undecided** — "localStorage cannot be read during SSR, so without picking an approach the engineer cannot satisfy the no-flash criterion. Pick one (cookie is the only way to get SSR-correct first paint)…"
2. **No dark palette** — "the acceptance criteria require WCAG AA contrast (>=4.5:1) in dark mode… without defined dark CSS variable values, the contrast criterion is untestable and the work is unscoped."
3. **Unbounded refactor scope** — "'switches the entire visible UI' is unbounded without an enumerated list of in-scope components/pages."
4. **"in-progress chat state" is ambiguous** — "what counts as in-progress chat state (unsent input text, streaming response, scroll position)?"
5. **System-preference reactivity left open** — "determines whether a matchMedia listener is needed and must be settled."

### PRD v2 (`pm_revise_prd`, Sonnet)

The PM resolved every concern: chose the **cookie** mechanism, supplied a concrete **dark palette
table**, enumerated **7 in-scope surfaces**, defined "in-progress chat state" precisely, and decided
**not** to be reactive to mid-session OS changes.

### → Architect review, pass 2 — ❌ REJECTED, 5 *new* concerns

The architect went deeper on the now-concrete design and found subtler problems:

1. **FOUC claim inconsistent with first-visit behavior** — "`Sec-CH-Prefers-Color-Scheme` is only sent if the server has previously opted in via `Accept-CH`/`Critical-CH`… not supported by Safari/Firefox at all. On the very first request the header is absent, so… a dark-OS first-time user will see a light flash."
2. **Cookie-write mechanism under-specified** — could conflict with the "no full page reload" criterion.
3. **Contrast verification self-conflicting** — "Section 6 mandates an automated CI check using `@accessibility-checker`… against a rendered Storybook/Playwright snapshot. The repo conventions mention no Storybook."
4. **Streaming-preservation needs a concrete mechanism** — "state explicitly that toggling must not cause a React re-mount, navigation, or revalidation."
5. **Shared-component refactor migration risk** — "changing a shared component's tokens will affect every consumer, including out-of-scope pages."

### PRD v3 (`pm_revise_prd`, Sonnet) → 📄 [full final PRD](./walkthrough-dark-mode/prd-final.md)

The PM resolved all five: a **two-tier FOUC strategy** (client hints + an inline-script fallback), a
**client-side-only cookie write** (no server action, to protect streaming), **Playwright + `@axe-core/playwright`**
(explicitly no Storybook), and an explicit **shared-component migration rule** with a light-mode snapshot test.

### → Architect review, pass 3 — ✅ **APPROVED** (0 concerns)

Three rounds of adversarial review turned a sketch into a buildable, edge-case-aware spec. **This is
the value the structure adds** — a single agent asked to "add dark mode" produces none of this rigor.

---

## 5. UX mocks

Because the brief was `ui_impacting=True`, the workflow ran the conditional `ux_generate_mocks` stage
(a stub in this build) → `artifact://mocks/feat-add-a-user-facing-dark-mode-them`.

---

## 6. Consumer research panel

A `ConsumerResearchWorkflow` child fanned out **four synthetic personas** in parallel, each reacting
to the PRD *in character*:

- **First-time user — 🟢 positive:** *"the app already picks up my system's dark mode without me having to dig through settings — that's exactly what I'd want on day one. The toggle in the header looks easy to find… I'd notice if it was missing."*
- **Time-constrained professional — ⚪ neutral:** *"dark mode is nice for late-night meal planning… But honestly this does nothing to make planning faster… Would've rather seen effort go into something that actually saves me time."*
- **Power user — ⚪ neutral:** *"I'd rather see cross-device sync — storing my preference in my household profile instead of just a cookie… The FOUC handling and client-hint implementation is solid engineering, but cookie-only persistence feels like a half-measure. Ship it, but put profile-level persistence on the roadmap."*
- **Budget-conscious — ⚪ neutral:** *"this doesn't really do anything for my grocery budget or help me find cheaper recipes. It's a nice-to-have, but I care way more about the app helping me stretch my dollars."*

**Synthesis:** overall sentiment **mixed** — useful signal that dark mode is table-stakes polish, not
a retention driver, and that *cross-device sync* is the feature users actually want next.

---

## 7. PM sign-off

The human PM-sign-off gate (a Temporal signal) was **approved**, so the workflow proceeded to story
planning. (A `revise` here would have looped back into the PRD revision flow, bounded by
`MAX_SIGNOFF_REVISIONS`.)

---

## 8. Story breakdown

The **`architect_plan_stories`** persona (Opus) broke the approved PRD into **10 estimated stories**:

| # | Story | Est. |
|---|---|---|
| 1 | Define CSS variable token system and refactor light theme to variables on `:root` / `[data-theme="light"]` | 3 |
| 2 | Add `[data-theme="dark"]` dark palette tokens with specified hex values | 2 |
| 3 | Implement cookie-based SSR theme: root layout reads `mealplanner-theme` cookie and renders `<html data-theme>` | 3 |
| 4 | Add client-hint headers (`Accept-CH`/`Critical-CH`) and server-side `Sec-CH-Prefers-Color-Scheme` read for first-visit default | 3 |
| 5 | Add synchronous inline FOUC-prevention fallback script in `<head>` for non-client-hint browsers | 2 |
| 6 | **Build accessible theme toggle control** in app shell/header with client-side `data-theme` mutation and cookie write | 3 |
| 7 | Refactor in-scope surfaces (shell, nav, chat, input, recipe cards, modal) to use CSS variable tokens | 5 |
| 8 | Refactor shared form input and button components to CSS variable tokens | 3 |
| 9 | Add Playwright + axe-core contrast verification test for both themes | 3 |
| 10 | Add Playwright test verifying chat-state preservation on toggle + a light-mode visual snapshot | 3 |

> **Observation (logged as a follow-up):** ten stories — including client-hint headers and axe-core
> contrast tests — is *thorough to the point of over-engineering* for "add a toggle." The architect's
> ambition here is the main driver of coding cost. A per-feature complexity signal would rein it in.

---

## 9. Engineering pod

The `EngineeringPodWorkflow` ran **one** Claude Agent SDK agent (Sonnet) that implemented the **whole
ordered story plan in a single disposable workspace** — so it lands as one coherent diff (rather than
parallel agents producing conflicting partial diffs). The agent's own closing summary:

> *"All hardcoded shadows are replaced. `npm test` passes cleanly. Here's a complete summary of
> everything implemented: ## What was implemented — Stories 1–2: CSS Variable Token System (light +
> dark) — Moved all palette tokens to `:root, [data-theme='light']` and added a complete
> `[data-theme='dark']` block with carefully chosen dark hex values (`--bg: #111110`, `--surface:
> #1c1c1e`, `--sage: #6aab7a`, …)…"*

- **Cost:** $1.87 (Claude subscription) · **Turns:** within the 70-turn / $2.50 cap
- **QA:** the pod's QA runs the target's `npm test` inside a `--network none` container; meal-planner
  has no offline-runnable test there, so QA reports `failed` — but the **diff is real and complete**,
  and the PR opens regardless (deploy stays human-gated). The `status: failed` is an honest
  environment limitation, not a code failure.

---

## 10. PR opened + deploy gate

The pod's `open_pr` step pushed the branch and ran `gh pr create`:

### 🟢 [meal-planner PR #4 — Dark Mode Theme Toggle](https://github.com/sheastyer/meal-planner/pull/4)

`+566 / −41` across **13 files** — the complete feature:

| File | What |
|---|---|
| `components/theme-toggle.tsx` | **the accessible toggle** (the artifact the feedback asked for) |
| `app/layout.tsx` | cookie-based SSR theme + inline FOUC-prevention script |
| `app/globals.css` | light/dark CSS variable token system |
| `app/calendar/page.tsx`, `app/calendar/[date]/page.tsx`, `components/ui.tsx` | in-scope surface refactor |
| `next.config.ts` | client-hint headers |
| `tests/theme.spec.ts`, `playwright.config.ts` | Playwright contrast + chat-state tests |

The branch carries a per-run tag (`agentic/…-8d930b55`) so re-runs don't collide. The **deploy-approval**
human gate was then approved → status **SHIPPED**.

> Honest note: the agent also pulled in **Playwright + a `package-lock.json` change** — test-infra
> scope creep driven by the coding prompt's "`npm test` must pass" rule. Logged as a follow-up.

---

## How this was captured

The reasoning above is not reconstructed — it's read straight from a database:

- The dev server runs with a **persistent DB** (`temporal server start-dev --db-filename .localdata/temporal-dev.db`), so workflow history survives restarts.
- `cli.trace <workflow-id> --save .localdata/artifacts.db` decodes every stage's activity result and writes it to a SQLite `trace_artifacts(workflow_id, scope, seq, activity, payload_json, saved_at)` table.

Re-print or query any run:

```bash
./.venv/bin/python -m cli.trace feedback-demo-8d930b55                       # pretty-print
./.venv/bin/python -m cli.trace feedback-demo-8d930b55 --save .localdata/artifacts.db
sqlite3 .localdata/artifacts.db \
  "select scope, activity from trace_artifacts where workflow_id='feedback-demo-8d930b55' order by scope, seq"
```

---

## Appendix: the bug this run fixed

An earlier run of the *same* feedback opened [PR #3](https://github.com/sheastyer/meal-planner/pull/3) —
which had the dark CSS but **no toggle**. The trace proved the reasoning was never at fault (the
architect's plan always contained a toggle story); the gap was a `CODING_MAX_STORIES=1` cost cap that
coded only story #1 and deferred the rest, plus a budget cap that *discarded* the agent's partial work
on a stop. Both are fixed: the pod now runs **one agent over the whole plan**, and a budget/turn stop
is a **soft stop** that keeps the partial diff. Full postmortem in [PLAN.md](../PLAN.md) (M4 progress)
and the cost lesson in [CLAUDE.md §10](../CLAUDE.md).
