"""Org-wide constants. Project-specific config lives in a Project Profile (M2), not here."""

import os

# Temporal connection + routing. TEMPORAL_TARGET is env-overridable because deployed
# processes (worker/listener/intake in the Coolify compose stack) reach Temporal by its
# compose service name, not localhost. Read once at import — a plain string thereafter,
# so workflow code importing this module stays deterministic (R3).
TEMPORAL_TARGET = os.environ.get("TEMPORAL_TARGET", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = "agentic-org"

# Bounded-loop caps (CLAUDE.md §10 — every agent<->agent loop has an explicit cap).
MAX_PRD_PASSES = 3          # PRD <-> architect review loop
MAX_SIGNOFF_REVISIONS = 2   # PM sign-off -> PRD revision loopback
MAX_QA_FIX_PASSES = 1       # engineering pod QA -> fix loop. Safe as an org-wide cap now: a target whose tests can't run in the sandbox declares stack.sandbox_tests=False in its PROFILE, which makes QA report "unavailable" (passed) — so this loop only ever fires on a genuine red from a runnable suite. (The old 0 was meal-planner-specific tuning leaked into org config, §3.)
MAX_REVIEW_PASSES = 1       # engineering pod code-review -> revise loop, BEFORE the PR opens. The reviewer is a cheap reasoning-plane call, but each *revise* pass is a full coding re-run on the Claude subscription (§10) — so this is a hard cost lever; keep it at 1 (one chance to address review) unless coding spend is acceptable.
MAX_CI_FIX_PASSES = 1       # engineering pod CI gate -> fix loop, AFTER the PR opens. The org waits for the PR's real CI to conclude; on failure it feeds the failing checks back to the coding agent, pushes the fix to the SAME PR, and re-checks — bounded here because each pass is a full coding re-run on the subscription PLUS a CI wait (§10). If CI is still red after the cap, the workflow halts (does not merge) for a human.

# CI gate (the org must not progress to merge past a red PR). The await-CI activity polls the
# PR's checks until they conclude; these bound that poll. The activity's start-to-close timeout
# (set in the workflow) must exceed CI_POLL_TIMEOUT. Read activity-side, so plain constants.
CI_POLL_TIMEOUT_MINUTES = 20      # give up waiting for CI to conclude after this
CI_POLL_INTERVAL_SECONDS = 20     # how often to re-query the PR's check status
CI_ACTIVITY_TIMEOUT_MINUTES = 25  # start-to-close for the await_ci activity (> poll timeout)

# Engineering-pod activities run a real coding agent + the target's tests in a sandbox —
# far longer than the 30s default for reasoning activities (common.py). Minutes, not seconds.
# A human-funded coding round (the pre-pod coding-budget gate) scales this with the budget
# (engineering_pod._coding_timeout): all three caps must rise together — dollars, turns, AND
# wall-clock. Learned live 2026-07-07 (run feedback-demo-e5e3b1b5): a $15-funded 5-story run
# hit the flat 20-min StartToClose on attempt 1 and the Temporal retry silently discarded the
# whole paid coding pass. The ceiling bounds how long a hung session can park the workflow.
CODING_ACTIVITY_TIMEOUT_MINUTES = 20
CODING_ACTIVITY_TIMEOUT_MAX_MINUTES = 240

# Post-QA screenshot capture (the pod's visual evidence): clone + apply diff + boot the
# target's preview stack (a compose build can take minutes) + screenshot the routes.
# Advisory work, but it needs coding-scale wall-clock, not the reasoning default.
PREVIEW_ACTIVITY_TIMEOUT_MINUTES = 25

# Coding-pod cost controls (CLAUDE.md §10 — the pod dominates a feature's cost). The agent
# runs on the Claude subscription, so an uncapped pod can drain the 5-hour usage window. The
# real spend guards are structural: ONE pod session owns the whole feature in one workspace
# (single-writer invariant — in orchestrator mode it dispatches per-story implementer
# subagents strictly one at a time; never a parallel-clone fan-out), and a coding error
# returns a failed story instead of raising (no Temporal retry storm — see coding_backed).
# The per-attempt caps below bound the session: MAX_TURNS bounds the lead AND each subagent
# individually; MAX_BUDGET_USD caps the WHOLE tree (the SDK aggregates subagent spend into
# total_cost_usd). They must be high enough to *finish* — too low (e.g. $0.25/8 turns) and it
# stops mid-task with no committable diff, so the PR comes up empty. ~$1.50/40 turns completed
# a real dark-mode change with headroom. For genuinely heavy lifts, raise BOTH of these and
# the BUDGET_USD workflow ceiling below — otherwise the budget gate trips to a human, which
# is the designed failure mode, not an error. Read activity-side, so plain constants are fine.
CODING_MAX_TURNS = 70
CODING_MAX_BUDGET_USD = 2.50

# Coding-cost estimator — feeds the pre-pod **coding-budget gate** (§9.4): before a live
# coding round runs, the org shows the human this estimate and they fund it (or set their
# own budget, or halt). The approved amount replaces CODING_MAX_BUDGET_USD for that run,
# so a heavy lift can be funded up front instead of dying mid-run at the default cap.
# Heuristics, deliberately coarse: base = lead-session/workspace overhead, per-story cost
# by the tier the architect assigned. Calibrated against the one real datapoint (the
# 2026-06 dark-mode feature: ~$1.87 ≈ base + one sonnet story); refine as live runs land.
CODING_EST_BASE_USD = 0.75
CODING_EST_STORY_USD = {"haiku": 0.50, "sonnet": 1.25, "opus": 2.50}

# Default image for the container coding agent (Option A, D9 — `CODING_AGENT=claude_container`).
# Must carry the `claude` CLI + the target stack's runtime; supply the real one per deployment via
# CODING_AGENT_IMAGE. Plain constant (no env read) so workflows can import this module (R3).
CODING_AGENT_IMAGE_DEFAULT = "agentic-coder:latest"

# Per-workflow budget ceilings in USD (CLAUDE.md §10, decision D7). Lean on purpose:
# the gate trips into a human override rather than silently spending. The bug ceiling
# rose $0.50 → $2.50 when the bug path started riding the engineering pod (2026-07-02):
# a real coding pass alone runs ~$1–2, so the old ceiling tripped on every live bug.
BUDGET_USD = {"feature": 3.00, "bug": 2.50}

# Per-1M-token pricing by model tier (Anthropic docs, 2026-06; decision D2). Used for
# dollar-denominated cost accounting regardless of provider (the Vercel gateway may bill
# with a margin — treat these as the estimate).
PRICING = {
    "haiku": {"model": "claude-haiku-4-5", "input": 1.00, "output": 5.00},
    # Sonnet 5 (GA 2026-06-09) is the current Sonnet — supersedes the legacy claude-sonnet-4-6.
    # Standard rate $3/$15; introductory $2/$10 runs through 2026-08-31. We keep the standard
    # rate for cost estimates so the budget isn't under-counted once intro pricing ends.
    "sonnet": {"model": "claude-sonnet-5", "input": 3.00, "output": 15.00},
    "opus": {"model": "claude-opus-4-8", "input": 5.00, "output": 25.00},
}

# Reasoning-plane provider: **Vercel AI Gateway only** (decided 2026-07-02 — one provider
# per plane; the earlier anthropic/vercel matrix is retired). The coding plane runs on the
# Claude subscription via the Agent SDK (CODING_* knobs below), never through the gateway.
VERCEL_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
VERCEL_MODELS = {  # tier -> gateway model id (gateway uses dot versioning for X.Y models)
    "haiku": "anthropic/claude-haiku-4.5",
    "sonnet": "anthropic/claude-sonnet-5",  # Sonnet 5 is a single-integer version (no dot)
    "opus": "anthropic/claude-opus-4.8",
}

# Human-gate timeouts (CLAUDE.md §9.4 — gates are signals WITH timeouts).
# Hours/days; the council timeout escalates-and-tallies, the others halt as ESCALATED.
COUNCIL_TIMEOUT_HOURS = 72
SIGNOFF_TIMEOUT_DAYS = 7
DEPLOY_TIMEOUT_DAYS = 7
CLARIFICATION_TIMEOUT_DAYS = 7
BUDGET_OVERRIDE_TIMEOUT_DAYS = 7

# Consumer-research panel (CLAUDE.md §13.5 — moves into the Project Profile in M2).
DEFAULT_RESEARCH_PERSONAS = (
    "budget-conscious",
    "time-constrained professional",
    "power user",
    "first-time user",
)
