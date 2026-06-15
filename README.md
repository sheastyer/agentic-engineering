# Agentic Engineering — a product org made of agents

> A reusable, project-agnostic **software organization built from autonomous agents** —
> PM, exec council, architect, engineers, QA, consumer research — that takes a piece of
> user feedback and carries it all the way to a shipped change, with humans at the gates
> that matter.
>
> Point it at a repo. Feed it feedback. Let the org work.

---

## The problem with one agent doing everything

The default way to use a coding agent is to hand it a task and ask it to one-shot the
answer: *"add this feature,"* *"fix this bug."* For small, well-scoped work this is
great. But the moment the task is ambiguous, contested, or consequential, a single
agent in a single pass runs into a wall that no amount of prompt-tuning fixes:

- **One perspective.** A lone agent adopts a single frame — usually "implement the
  literal request as fast as possible." It doesn't argue with itself about whether the
  feature is worth building, whether legal or sales would object, whether real users
  would even want it. The blind spots of that one frame become the blind spots of the
  output.
- **No tension, no quality.** Good decisions come from *opposition* — a PM who wants
  scope, an architect who wants simplicity, a skeptic who wants evidence. A single
  agent has no one to push back against, so it rarely discovers the strongest version
  of an idea. It converges on the first plausible one.
- **Everything happens at once, in one head.** Triage, prioritization, design,
  research, implementation, and review collapse into a single undifferentiated context.
  There's no point at which a human can step in, no record of *why* a choice was made,
  no place to say "stop, this needs a vote."
- **It can't wait.** Real product work pauses — for a decision, an approval, a reply
  that comes tomorrow. A one-shot agent has no concept of waiting days for a human
  signal and then resuming exactly where it left off. So the interesting,
  human-gated parts of building software get left out entirely.
- **Unbounded and unaccountable.** Ask one big agent to "just handle it" and you get
  no cost ceiling, no audit trail, no clear handoffs — just a long opaque transcript
  and a bill.

The takeaway: **most real engineering isn't a generation problem, it's a coordination
problem.** And coordination is exactly what a single agent doesn't do.

---

## The reframe: an organization is a perspective machine

Human companies didn't invent PMs, architects, reviewers, legal, and user research
because work is fun to divide up. They invented these roles because **each role is a
distinct lens**, and forcing an idea to survive all of them in sequence is how you get
something worth shipping.

- The **PM** asks *what problem are we actually solving, and for whom?*
- The **exec council** (legal, sales, a human vote) asks *should we even do this?*
- The **architect** asks *what's the simplest design that survives contact with reality?*
- **Consumer research** asks *would real, different people actually want this?*
- The **engineers** ask *how does this actually get built?*
- **QA** asks *where does it break?*

A request that passes through all of these is a fundamentally different — and better —
artifact than the same request handed to one agent that just starts typing.

**Agentic engineering is the practice of recreating that perspective machine out of
agents.** Not one agent that's prompted to "think like a team," but *many* agents,
each with its own role, prompt, tools, model tier, and output contract — that genuinely
hand work to one another, disagree, vote, escalate to a human, and only then proceed.
The intelligence isn't in any single model call. It's in the **structure** that makes
each idea run the gauntlet.

This is the same lesson the broader field keeps re-learning: the leverage is moving from
"a better single prompt" to "a better *process* composed of many modest steps." Teams of
specialized agents outperform one generalist agent on exactly the work that matters most
— the ambiguous, contested, multi-stakeholder kind.

---

## What this project is

A **project-agnostic** implementation of that org. It is not tied to any one app:

- The **org** — workflows, roles (personas), the agent runner, the invariants — is
  generic and reusable.
- The **target project** is just *input*. You describe any repo or idea with a
  **Project Profile** (where the code lives, its stack, how feedback comes in, what
  "deploy" means), and the same org goes to work on it. New idea → new profile →
  never new orchestration code.

So this is less "an AI feature for one app" and more **a product/engineering
organization you can rent out to any of your ideas.** The first idea it's pointed at —
the testbed — is a meal-planner app; nothing in the core assumes it.

---

## How it works (the short version)

Two principles do most of the heavy lifting. The full design is in
[`CLAUDE.md`](./CLAUDE.md); here's the shape:

**1. Two planes, kept strictly separate.**

- An **orchestration plane** — the long-running, human-gated business process that can
  pause for hours or days waiting on a vote or an email, then resume exactly where it
  was. This is a durable-workflow problem (we use **Temporal**).
- An **execution plane** — the agents that actually read and edit code, run tests, and
  iterate. Short-lived but intense. This is a coding-agent problem (we use the
  **Claude Agent SDK**), invoked *from inside* the orchestration plane.

Conflating these two is the classic failure mode. Keeping them apart is what makes the
system both reliable and affordable.

**2. The system costs nothing while it waits.**

It never sits in a loop polling a model. It polls a queue (free) and only invokes a model
when something real happens. Every agent↔agent loop is bounded, every workflow has a
budget ceiling, and the expensive parts (coding agents) run last, behind a human gate.

A feature request flows roughly like this:

```
feedback ─► triage ─► PM brief ─► exec-council vote (agents + human, 72h timer)
        ─► PRD ⇄ architect review (bounded loop) ─► consumer-research panel (fan-out)
        ─► PM sign-off ─► story breakdown ─► engineering pod (code + QA)
        ─► human deploy approval ─► shipped
```

Bugs take a shorter triage → prioritize → fix → review → deploy path. Humans hold the
gates — council vote, sign-off, deploy approval — and everything in between is agents
handing work to agents.

---

## Why bother — what you actually get

- **Better outcomes on hard, ambiguous work**, because every idea survives multiple
  opposing perspectives before any code is written.
- **A real audit trail** — who decided what, when, and why — instead of one opaque
  transcript.
- **Humans in the loop where it counts**, and nowhere it doesn't.
- **Cost control by construction** — tiered models, bounded loops, per-workflow budgets,
  zero cost while idle.
- **Reuse across every idea you have** — the org is the asset; each app is just a profile.

---

## Status

The skeleton works and the agent layer is built — all proven for **$0 in tokens** so far.

- ✅ Vision and architecture defined ([`CLAUDE.md`](./CLAUDE.md)) + [flow diagram](./temporal-feature-flow.html)
- ✅ Key decisions made — Python, Temporal (local dev first), project-agnostic design
- ✅ **M0** — Temporal dev server + worker connected
- ✅ **M1** — full feature + bug workflows on stubs: every gate, timer, bounded loop, child workflow, and replay determinism proven
- ✅ **M2** — generic Agent Runner + **model-provider abstraction** (Anthropic Messages / Vercel gateway, swappable), persona registry, Project Profile loader, per-workflow **dollar budget gate**, workflow-purity lint. Closed live: real triage ran through the gateway inside a Temporal workflow, cost flowing through the budget cap.
- ✅ **M3 (substantially complete)** — every reasoning/judgment persona with real inputs is now a **live, eval-gated agent**, each behind its own `USE_AGENT_*` flag (off by default = $0 stubs). Feature path: brief → council (legal + sales) → PRD authoring → PRD review ⇄ revision → consumer research → story breakdown. Bug path: triage → prioritization. Cheapest-first across Haiku/Sonnet/Opus; each gated by a per-persona eval (schema conformance + deterministic assertions incl. injection-resistance + dollar cost), with an **LLM-judge** for subjective PRD prose (human-calibrated, zero false-pass). Per-call costs all far under the $3-feature / $0.50-bug ceilings.
- ⏳ **M4** — the sandboxed **engineering pod** (Claude Agent SDK in isolated worktrees): the remaining stubs (`fix_bug`, `implement_story`, fix review, QA) are execution-plane coding work. Then **M5** — real intake adapters + human-I/O channel.

53 tests green, ~9s. The discipline is deliberate: **prove the whole control flow with
stubs before spending a single token on a model**, then swap one persona at a time behind an
eval gate. Full milestone plan, eval gates, and a **"Current state & how to continue"
handoff** live in [`PLAN.md`](./PLAN.md).

---

## Running it locally

Python 3.14, virtualenv at `.venv`. From the repo root:

```bash
# tests (workflows, replay, agents, budget gate, invariant lint) — all $0
./.venv/bin/python -m pytest -q

# eval the triage persona with synthesized payloads — $0, no provider needed
./.venv/bin/python -m evals.run --persona triage --provider mock

# run a workflow on stubs (zero LLM): start the dev server + worker, then drive a demo
~/.temporalio/bin/temporal server start-dev --headless &
./.venv/bin/python -m worker.main &
./.venv/bin/python -m cli.run          # feature demo   (--bug for the bug path)
```

To use a **real model**: pick a provider with `MODEL_PROVIDER` (`anthropic` | `vercel`),
put the matching key in `.env` (see `.env.example`), and turn on whichever personas you
want live with their `USE_AGENT_*` flags on the worker — e.g. `USE_AGENT_TRIAGE=1`,
`USE_AGENT_COUNCIL=1`, `USE_AGENT_PRD_AUTHOR=1`, … (each persona has its own flag; unset =
$0 stub). To validate a persona in isolation, run its eval, optionally with the judge:

```bash
# deterministic eval (CON + assertions + cost)
set -a; . ./.env; set +a; MODEL_PROVIDER=vercel ./.venv/bin/python -m evals.run --persona council_legal --provider vercel
# subjective-prose eval with the human-calibrated LLM-judge (PRD authoring)
set -a; . ./.env; set +a; MODEL_PROVIDER=vercel ./.venv/bin/python -m evals.run --persona pm_write_prd --provider vercel --judge
```

Note: the Claude.ai subscription does **not** fund the Anthropic Messages API — that path
needs API credit; the Vercel gateway is the already-working alternative. Details in
[`PLAN.md`](./PLAN.md).

---

## Repo map

| Path | What it is |
|---|---|
| [`CLAUDE.md`](./CLAUDE.md) | Technical source of truth — architecture & invariants. |
| [`PLAN.md`](./PLAN.md) | Milestone sequence + per-step evaluation gates. |
| `README.md` | This file — the vision and the *why*. |
| `orchestrator/` | The org: workflows, activities, agent runner, persona registry, Project Profiles. |
| `worker/` · `cli/` | Temporal worker entrypoint · demo driver. |
| `tests/` | Workflow, replay, agent, budget, and invariant-lint tests. |

---

*Testbed: the [meal-planner](https://github.com/sheastyer/meal-planner) app (Next.js /
TypeScript). It's the first project the org is pointed at — not part of the org itself.*
