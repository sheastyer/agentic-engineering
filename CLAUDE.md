# Agentic Product & Engineering Org

> **Context handoff for Claude Code.** This document is the initial context for a
> **project-agnostic** system that orchestrates a *simulated software organization*
> (PM, exec council, architect, engineers, QA, consumer research) as autonomous agents.
> The org takes user feedback for **some target product**, triages it, and drives the
> change through brief → council vote → PRD ↔ architect loop → consumer research →
> story breakdown → implementation → QA → gated deploy.
>
> The org is **not tied to any single app.** The target product is an **input** — a
> *Project Profile* (see §3). The same org can be pointed at any repo/idea by supplying
> a new profile; no orchestration code changes. Read this fully before scaffolding.
>
> **▶ Continuing this work?** Read **[`PLAN.md`](./PLAN.md) → "Current state & how to
> continue"** first — it has the live status (M0–M2 done; M3 done — all reasoning personas
> are live, eval-gated agents; **M4 substantially complete** — the engineering pod is wired
> into Temporal and was driven end-to-end from feedback to a **real opened PR**, 2026-06-19),
> how to run things, the provider/billing reality, and the exact next steps. This file (CLAUDE.md) is the
> architecture + invariants reference; PLAN.md is the operational source of truth.

---

## 1. What we're building

A reusable **product/engineering organization as software.** Point it at a project,
feed it feedback (bugs or feature requests), and a network of agents runs the work
through a realistic org process with human gates at the moments that matter.

The deliverable is **the org and its orchestration patterns** — a reference
implementation of enterprise multi-agent orchestration that scales across projects.
Any individual app it improves is a *testbed*, not the product. The first testbed is
the meal-planner (see the pointer note at the bottom), but nothing in the core design
may assume that app.

For feature requests: brief → exec-council vote → PRD ↔ architect loop → synthetic
consumer research → story breakdown → implementation → QA → gated deploy. Bugs follow
a shorter triage → prioritize → fix → review → deploy path.

---

## 2. The one insight that shapes everything: two planes

This system is **two runtimes**, not one. Keep them separate.

- **Orchestration plane** — the long-running, event-driven, human-gated business
  process (triage → council → PM → research → handoffs). It pauses for hours or days
  waiting on a vote or an email reply. This is a **durable-workflow** problem.
- **Execution plane** — the agents that actually read/edit code, run tests, iterate.
  Short-lived but intense. This is a **coding-agent** problem.

Conflating them is the primary failure mode. The orchestration plane is **Temporal**;
the execution plane is the **Claude Agent SDK** invoked from inside Temporal activities.

---

## 3. Project-agnosticism: the Project Profile

The org operates on a target project described entirely by a **Project Profile** — a
versioned config object that is the *only* place project-specific knowledge lives.
Everything else (workflows, personas, runner, invariants) is generic.

A Project Profile supplies:

- **identity** — name, short description, domain context the agents need.
- **repo** — location (git remote and/or local path), default branch, how to clone.
- **stack** — languages, frameworks, package manager, test command, build command.
  (Note: the *target* stack is independent of the org's own language. The first
  testbed is TS/Next.js; the org itself is Python.)
- **intake adapter** — how feedback enters the org for this project (DB table poll,
  webhook, API endpoint, file drop, manual CLI). Normalized into a common feedback
  event the `IntakeRouter` consumes.
- **deploy target** — what "deploy" concretely means here (open a PR, merge, push a
  container, trigger an environment). Always behind a human gate (§9.2).
- **conventions** — coding standards, review rules, anything an agent must honor.
- **secrets/refs** — *references* to credentials in the secret store, never values.

> **Hard rule:** if a piece of knowledge is true only for one target app, it belongs
> in that app's Project Profile — never in workflow, runner, or persona code. Adding a
> new project = writing a new profile, never editing the org.

Open question: do we **clone each target repo into a workspace** the org manages, or
operate against the profile's pointer on demand? Leaning toward "clone into a managed
workspace per workflow run" for sandbox isolation (§9.6), but not yet decided (§12).

---

## 4. Architecture at a glance

```
 target app  ──(feedback via intake adapter)──►  intake queue  ──►  Temporal: IntakeRouter
      ▲                                                                      │
      │ (deploys, PRs — via deploy target)                      ┌───────────┴───────────┐
      │                                                         ▼                       ▼
      └──────────────────────────────────────────  FeatureRequestWorkflow        BugWorkflow
                                                                │
                Project Profile  ──┐          activities call ──►  Agent Runner  ──►  persona registry
                (project-specific  │                              │                  (prompt+tools+model)
                 knowledge)        └──────────►  child workflows ──►  ConsumerResearch (fan-out)
                                                                      EngineeringPod  (Agent SDK in worktrees)
```

- The target app is the **event source** (feedback) and the **deploy target**
  (PRs/releases), reached *only* through its Project Profile's adapters — never by
  sharing its codebase.
- Every persona is an **activity** that calls the Agent Runner. Human gates are
  **signals**. Fan-out groups are **child workflows**.

A visual of the flow lives in [`temporal-feature-flow.html`](./temporal-feature-flow.html)
— the canonical spec for stage order, gates, timers, bounded loops, and child-workflow
boundaries. It reflects the implemented workflows (the code is the source of truth).

---

## 5. Stack & key decisions

| Concern | Decision | Notes |
|---|---|---|
| Orchestration | **Temporal** | **Local dev server first** for M0–M2 (fast, free). Port to self-hosted Proxmox docker-compose + Postgres later. |
| Workflow/agent language | **Python** ✅ confirmed | Best combined support for Temporal SDK + Claude Agent SDK. (Independent of any target app's stack.) |
| Coding agents | **Claude Agent SDK** | The execution engine inside `EngineeringPod` activities. |
| LLM billing | **API credits / pay-as-you-go** ✅ (verified) | **The Claude.ai $20/mo subscription does NOT fund Developer-Platform API/SDK calls** — verified live 2026-06-14: `ant auth login` authenticates fine (OAuth `user:inference`), but the API returns `400 credit balance too low` because API usage bills a *separate* credit pool. So the org needs **API credits / pay-as-you-go** (Console → Plans & Billing) regardless of auth method. The SDK still resolves creds in order (`ANTHROPIC_API_KEY` → `ANTHROPIC_AUTH_TOKEN` → OAuth profile); OAuth-vs-key is just *who* you authenticate as, not *whether* there's credit. Secrets live in env/secret store (§9.3). Alternative provider: Vercel AI Gateway (its own billing). |
| State | Postgres (Temporal) + object store for agent artifacts | Agents write findings to shared storage and return lightweight references, not full payloads. |
| Email / human I/O | TBD (see §12) | Council votes, PM sign-off, user clarifications, deploy approval all arrive as Temporal **signals** behind whatever channel we pick. |

Model tiers (IDs + per-1M-token pricing confirmed against Anthropic docs, 2026-06; exact
strings, no date suffixes):
- **Haiku** `claude-haiku-4-5` — $1 in / $5 out, 200K ctx. Triage, routing, notifications.
- **Sonnet** `claude-sonnet-4-6` — $3 / $15, 1M ctx. Votes, revisions, synthetic users, most coding.
- **Opus** `claude-opus-4-8` — $5 / $25, 1M ctx. PRD authoring, architecture review, story planning.

Cost notes that shape the budget design: the Opus→Haiku spread is only **5×** (tiering
matters for latency/quality more than raw cost); a *feature* run's cost is dominated by
the **engineering pod** (Agent SDK coding), so size the budget cap against the pod, not
the debate. Use **Batches API (−50%)** for the consumer-research fan-out and other
non-latency-sensitive calls, and **prompt caching** (reads ~0.1×) for shared context
(profile, PRD, mocks). The persona **output contract** (§6) is implemented with the
SDK's **structured outputs** (`messages.parse()` / `output_config.format`); `response.usage`
gives exact token counts for dollar-denominated cost accounting (§10).

---

## 6. How an agent is defined

There is **one** generic Agent Runner. Personas are config, not bespoke code. A persona =

- **system prompt** — role, responsibilities, and an explicit **output contract**
  (prefer structured JSON the workflow can branch on). Project-specific context comes
  from the Project Profile, injected at runtime — not baked into the persona.
- **toolset** — the functions it may call (this is the real differentiator; e.g. the
  legal agent gets policy lookup, the engineer gets file/shell/test tools).
- **model tier** — per §5.
- **context/memory policy** — what it sees, what it persists to shared storage.
- **termination condition** — when it's done.

Build the runner so adding the 11th or 20th persona is a new registry entry, never a
new program. The PM, legal, sales, architect, UX, synthetic-user, and engineer agents
all run through the same runner.

### Model-provider abstraction
The runner depends only on a `ModelProvider` interface (`generate_structured(...) →
ProviderResponse`), so the org is **provider-agnostic** and supports bring-your-own
backends. Two ship today, selected by the `MODEL_PROVIDER` env var (default `anthropic`):
- **`anthropic`** — Anthropic Messages SDK with native structured outputs; credentials
  resolve to a **Claude subscription** (OAuth profile) or a direct API key. Adaptive
  thinking + effort applied for sonnet/opus.
- **`vercel`** — Vercel AI Gateway via its OpenAI-compatible endpoint
  (`ai-gateway.vercel.sh/v1`, `AI_GATEWAY_API_KEY`); tiers map to gateway-namespaced
  model ids (`anthropic/claude-…`).

Tiers stay `haiku/sonnet/opus`; cost is computed once in the runner from token usage ×
tier pricing (the gateway may bill with a margin — treated as an estimate).

**Reasoning-plane default = Messages API** (decided): it's the right tool for single-shot
structured reasoning — faster, clean concurrency for fan-out, exact cost, portable. The
**Claude Agent SDK** is *not* the reasoning default (it shells out to a `claude`
subprocess per call — latency/concurrency overhead) but is (a) the **M4 engineering-pod**
runtime and (b) an **optional reasoning provider** for running on a Claude subscription's
monthly Agent SDK credit (`pip install -e .[agent-sdk]`, `MODEL_PROVIDER=claude_agent_sdk`
— planned, not yet built). Note: the Claude.ai subscription does **not** fund the Messages
API (§5) — that path needs API credits.

---

## 7. Workflows

### IntakeRouter
The "loop" is **not** a running session that polls an LLM. A queue consumer receives
each normalized feedback event and calls `client.start_workflow(...)` — `BugWorkflow`
for bugs, `FeatureRequestWorkflow` for features. The system costs nothing while idle.

### FeatureRequestWorkflow (primary focus)
Ordered stages (the HTML diagram is the canonical version once it exists):
1. `pm_draft_brief` (activity)
2. **Exec council** — `council_vote` agents (legal, sales) in parallel + human vote
   via signal, 72h escalation timer → tally (deterministic) → branch on `approved?`
3. `pm_write_prd` → **bounded** PRD ↔ `architect_review_prd` loop (max 3 passes)
4. `ux_generate_mocks` (conditional)
5. `ConsumerResearchWorkflow` (child, parallel fan-out across demographic personas)
6. **PM sign-off** (signal); `revise` loops back into PRD revision
7. `architect_plan_stories`
8. `EngineeringPodWorkflow` (child, orchestrator-worker; Agent SDK per story → QA)
9. **Deploy approval** (signal) → `deploy` (via Project Profile's deploy target) → `SHIPPED`

### BugWorkflow (shorter)
Triage → dedupe → (optional user-clarification signal w/ 7-day timeout) → PM
prioritize → fix (Agent SDK) → review → QA → gated deploy.

---

## 8. Coordination patterns in use
- **Event-driven** — intake queue → start workflow.
- **Sequential handoff** — PM → architect → engineers → QA → deploy.
- **Debate + judge** — the exec council (agents + human voter, deterministic tally).
- **Orchestrator-worker** — the engineering pod.
- **Parallel fan-out** — the consumer-research panel.
- **Human-in-the-loop gates** — council, PM sign-off, deploy, user clarification.

---

## 9. Hard invariants — DO NOT violate

These are non-negotiable. If a task seems to require breaking one, stop and ask.

1. **Determinism boundary.** Workflow code is deterministic orchestration only:
   **no LLM calls, no network/file I/O, no randomness, no wall-clock reads, no
   non-deterministic library calls** inside workflows. *All* of that lives in
   activities. Violating this breaks Temporal replay and recovery.
2. **No unattended production deploys.** A deploy to prod always sits behind a human
   approval signal. Agents never self-ship to prod.
3. **Secrets via environment/secret store only.** Never hardcode the Anthropic API
   key or any credential. Never commit secrets. Project Profiles hold *references*.
4. **Human gates are signals with timeouts**, not blocking polls. Model every
   approval/clarification this way.
5. **Bounded loops only** (§10). Every agent↔agent loop has an explicit cap.
6. **Sandbox coding agents.** Engineering-pod agents run in isolated git worktrees /
   containers, never against the target repo's `main` directly.
7. **Idempotent, retried activities.** Each activity has an explicit retry policy;
   auth-type errors are non-retryable, transient/rate-limit errors are retryable.
8. **No project-specific knowledge in core code.** All of it lives in the Project
   Profile (§3). The org stays generic.

---

## 10. Cost policy (treat as requirements, not suggestions)
- **Never poll an LLM.** Poll the queue (free); invoke a model only on a real event.
- **Tier models** per §5. Don't run triage on Opus.
- **Cache shared context** (Project Profile, PRD, mocks) read by many agents.
- **Bound every agent↔agent loop.** The PRD↔architect loop is capped at 3; any new
  loop needs an explicit cap. Unbounded loops are the main cost leak.
- **Caps compose multiplicatively — budget for the product, not the happy path.** A PM
  "revise" re-runs the *entire* PRD↔architect loop **and** the research fan-out, so the
  worst case is `MAX_SIGNOFF_REVISIONS (2) × MAX_PRD_PASSES (3)` PRD revisions **plus**
  up to 3 research panels × N personas — with PRD authoring on Opus. Size the per-workflow
  ceiling against that product.
- **Per-workflow budget cap.** Each activity returns its cost; the workflow accumulates
  it (in dollars) and trips into a human gate when the ceiling is hit.
- **Lightweight returns.** Subagents persist detail to shared storage and return
  references; never re-ingest large payloads through the parent.
- **The engineering pod dominates a feature's cost — cap it hard.** It runs the Agent SDK on
  the Claude *subscription* (shared 5-hour usage window), so an uncapped pod can drain that
  window in an hour. The guards (in `config.py` / `coding_backed.py`, learned the hard way
  2026-06-18): **one agent implements the whole feature in one workspace** (the ordered story
  list as a single instruction) — no fan-out of parallel agents against separate clones, which
  caused both churn *and* conflicting/partial diffs (coding only story #1 of N shipped a
  feature with no UI); a coding error must return a **failed story, never raise** (a raise = up
  to 4× Temporal retries, each a full coding run — the worst leak); a budget/turn limit is a
  **soft stop** — `claude_sdk` captures the partial diff instead of discarding the whole run
  (a raise after the work is done silently wiped ~12 min of edits before this fix);
  `CODING_MAX_TURNS`/`CODING_MAX_BUDGET_USD` hard-cap that one agent — but high enough to
  *finish* ($0.25/8-turn produced nothing; ~$2.50/70-turn completed a real dark-mode feature at
  ~$1.87). Coding runs on **Sonnet**, not Opus. Default the pod to **mock** ($0) unless coding
  is the point.
- Multi-agent systems run roughly an order of magnitude more tokens than a single
  chat — keep fan-out widths and iteration counts capped.

---

## 11. Proposed repository layout

```
/                      # this CLAUDE.md at root
  /infra               # Temporal (dev server now; docker-compose for Proxmox later), env templates
  /orchestrator
    /workflows         # FeatureRequestWorkflow, BugWorkflow, IntakeRouter, children
    /activities        # thin wrappers: each calls the Agent Runner or a tool
    /agents
      runner.py        # the single generic Agent Runner (provider-agnostic)
      provider.py      # the ModelProvider interface + ProviderResponse
      providers/       # anthropic_provider, vercel_provider, factory (MODEL_PROVIDER)
      registry/        # one file/config per persona (prompt + contract + tier)
      tools/           # tool implementations (policy lookup, repo ops, email, ...)
    /projects          # Project Profiles — one per target app (meal-planner, ...)
    /shared            # artifact store client, cost accounting, signal helpers
  /worker              # Temporal worker entrypoint
  /tests               # workflow tests with mocked activities (replay tests)
```

---

## 12. Build plan — see PLAN.md

The milestone sequence (M0–M5), the per-step **evaluation gates**, the standing
regression suite, and the open-decisions tracker live in **[`PLAN.md`](./PLAN.md)** —
the operational source of truth for what we build next and how we prove it's safe to
continue. M0 (infra), M1 (full skeleton on stubs), and M2 (agent runner + provider
abstraction) are complete; M3 (swap stubs for live, eval-gated agents, cheapest-first) is
complete — every reasoning persona on the feature and bug paths is real. M4 (execution-plane
coding pod) is substantially complete: the pod is wired into Temporal behind `USE_AGENT_CODING`
(agent-backed `implement_story`/`fix_bug`/`open_pr`), runs the Claude Agent SDK on the
subscription in a container sandbox, and was validated end-to-end — a dark-mode feature request
drove brief→council→PRD↔architect→research→sign-off→stories→pod and opened a real GitHub PR.

Two principles from that plan are load-bearing and restated here: **do not call real
models before M3** (prove orchestration on stubs first), and **a milestone is not done
until its exit gate passes** (its own evals + the regression suite green).

---

## 13. Assumptions & open decisions — confirm before scaffolding

> The **live** open-decisions tracker (with the milestone each blocks) is in
> [`PLAN.md`](./PLAN.md#decisions-tracker-resolve-before-the-milestone-that-needs-them).
> The list below is the architectural snapshot.

Resolved:
- ✅ **Language** — Python for orchestrator/agents.
- ✅ **Temporal hosting** — local dev server first; Proxmox docker-compose later.
- ✅ **Project-agnostic** — target project is a Project Profile input, not hardwired.

Still open:
1. **Human I/O channel** — email, Slack, a dashboard? All gate signals route through it.
2. **Model IDs & pricing** — verify current Opus/Sonnet/Haiku model strings and rates
   in Anthropic docs before hardcoding tiers; do not trust stale values.
3. **Billing** — confirm the direct-API-key path and current SDK metering terms.
4. **Repo handling** — clone each target repo into a managed per-run workspace, or
   operate on the profile's pointer on demand? (Leaning managed workspace for §9.6.)
5. **Consumer-research panel size** — N personas and max research iterations (cost cap).
6. ✅ **`temporal-feature-flow.html`** — created; reflects the implemented flow.
7. **Intake adapter (per project)** — for each target app, how feedback is exposed
   (DB table, webhook, API). Captured in that app's Project Profile, not core code.

---

## 14. Definition of done — see PLAN.md

Per-milestone exit gates replace a single "definition of done." See the exit gate of
each milestone in **[`PLAN.md`](./PLAN.md)**, plus the standing regression suite (R1–R5)
that every milestone must keep green.

---

## Reference projects (testbeds — NOT part of the core design)

These are concrete apps the org is pointed at. The org must not assume any of them.

- **meal-planner** — first testbed. Repo: `git@github.com:sheastyer/meal-planner.git`
  (local: `~/Projects/meal-planner`). Stack: **Next.js + TypeScript, Drizzle ORM
  (Postgres), Dockerized.** A barebones agentic meal-planner: takes a household
  profile and, via chat, surfaces recipes to plan the week. Its Project Profile
  (intake adapter + deploy target) is authored in M2; until then it's just a pointer.
