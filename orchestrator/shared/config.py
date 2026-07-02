"""Org-wide constants. Project-specific config lives in a Project Profile (M2), not here."""

# Temporal connection + routing.
TEMPORAL_TARGET = "localhost:7233"
TEMPORAL_NAMESPACE = "default"
TASK_QUEUE = "agentic-org"

# Bounded-loop caps (CLAUDE.md §10 — every agent<->agent loop has an explicit cap).
MAX_PRD_PASSES = 3          # PRD <-> architect review loop
MAX_SIGNOFF_REVISIONS = 2   # PM sign-off -> PRD revision loopback
MAX_QA_FIX_PASSES = 0       # engineering pod QA -> fix loop (0 for the offline meal-planner: its tests can't run in the sandbox, so a fix pass can never go green and would just double coding cost; set 1 when the target has runnable tests)
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
CODING_ACTIVITY_TIMEOUT_MINUTES = 20

# Coding-pod cost controls (CLAUDE.md §10 — the pod dominates a feature's cost). The agent
# runs on the Claude subscription, so an uncapped pod can drain the 5-hour usage window. The
# real spend guards are structural: ONE agent implements the whole feature in one workspace
# (no parallel-agent fan-out), and a coding error returns a failed story instead of raising
# (no Temporal retry storm — see coding_backed). The per-attempt caps below still bound that
# one agent, but must be high enough for it to *finish* — too low (e.g. $0.25/8 turns) and it
# stops mid-task with no committable diff, so the PR comes up empty. ~$1.50/40 turns completed
# a real dark-mode change with headroom. Read activity-side, so plain constants are fine.
CODING_MAX_TURNS = 70
CODING_MAX_BUDGET_USD = 2.50

# Default image for the container coding agent (Option A, D9 — `CODING_AGENT=claude_container`).
# Must carry the `claude` CLI + the target stack's runtime; supply the real one per deployment via
# CODING_AGENT_IMAGE. Plain constant (no env read) so workflows can import this module (R3).
CODING_AGENT_IMAGE_DEFAULT = "agentic-coder:latest"

# Per-workflow budget ceilings in USD (CLAUDE.md §10, decision D7). Lean on purpose:
# the gate is expected to trip on real coding (M4) for a small app, forcing human review.
# Cost is dollar-denominated from real response.usage × tier pricing (see PRICING).
BUDGET_USD = {"feature": 3.00, "bug": 0.50}

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
