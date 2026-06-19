"""Org-wide constants. Project-specific config lives in a Project Profile (M2), not here."""

# Temporal connection + routing.
TEMPORAL_TARGET = "localhost:7233"
TEMPORAL_NAMESPACE = "default"
TASK_QUEUE = "agentic-org"

# Bounded-loop caps (CLAUDE.md §10 — every agent<->agent loop has an explicit cap).
MAX_PRD_PASSES = 3          # PRD <-> architect review loop
MAX_SIGNOFF_REVISIONS = 2   # PM sign-off -> PRD revision loopback
MAX_QA_FIX_PASSES = 0       # engineering pod QA -> fix loop (0 for the offline meal-planner: its tests can't run in the sandbox, so a fix pass can never go green and would just double coding cost; set 1 when the target has runnable tests)

# Engineering-pod activities run a real coding agent + the target's tests in a sandbox —
# far longer than the 30s default for reasoning activities (common.py). Minutes, not seconds.
CODING_ACTIVITY_TIMEOUT_MINUTES = 20

# Coding-pod cost controls (CLAUDE.md §10 — the pod dominates a feature's cost). The agent
# runs on the Claude subscription, so an uncapped pod can drain the 5-hour usage window:
#  • CODING_MAX_STORIES — how many stories the pod actually codes per run; the rest are
#    recorded as "deferred" (a $0 marker), so a feature can't spawn N parallel agents.
#  • CODING_MAX_TURNS / CODING_MAX_BUDGET_USD — hard per-attempt caps handed to the SDK.
# Constants (no env) so the workflow stays deterministic; the per-attempt caps are read
# activity-side in coding_backed. Single story is the real spend guard (no parallel-agent
# fan-out, no retry storm); the per-attempt caps must still be high enough for the agent to
# *finish* — too low (e.g. $0.25/8 turns) and it stops mid-task with no committable diff, so
# the PR comes up empty. ~$1.50/40 turns completed a real dark-mode change with headroom.
CODING_MAX_STORIES = 1
CODING_MAX_TURNS = 40
CODING_MAX_BUDGET_USD = 1.50

# Per-workflow budget ceilings in USD (CLAUDE.md §10, decision D7). Lean on purpose:
# the gate is expected to trip on real coding (M4) for a small app, forcing human review.
# Cost is dollar-denominated from real response.usage × tier pricing (see PRICING).
BUDGET_USD = {"feature": 3.00, "bug": 0.50}

# Per-1M-token pricing by model tier (Anthropic docs, 2026-06; decision D2). Used for
# dollar-denominated cost accounting regardless of provider (the Vercel gateway may bill
# with a margin — treat these as the estimate).
PRICING = {
    "haiku": {"model": "claude-haiku-4-5", "input": 1.00, "output": 5.00},
    "sonnet": {"model": "claude-sonnet-4-6", "input": 3.00, "output": 15.00},
    "opus": {"model": "claude-opus-4-8", "input": 5.00, "output": 25.00},
}

# Model provider selection (resolved at runtime by agents/providers/factory.py, NOT here —
# workflows import this module, so it must stay free of env reads / I/O). Bring-your-own:
# set MODEL_PROVIDER=anthropic|vercel. Same model tiers either way.
DEFAULT_MODEL_PROVIDER = "anthropic"
VERCEL_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
VERCEL_MODELS = {  # tier -> gateway model id (gateway uses dot versioning, verified via /v1/models)
    "haiku": "anthropic/claude-haiku-4.5",
    "sonnet": "anthropic/claude-sonnet-4.6",
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
