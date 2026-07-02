"""Data contracts that flow between workflow stages.

Plain dataclasses so Temporal's default JSON converter can serialize them across the
activity/workflow boundary and persist them in history. Every stage result carries a
``cost_tokens`` field so the workflow can accumulate spend (CLAUDE.md §10); the stubs
return small canned values and real agents (M3+) will return measured token counts.
"""

from dataclasses import dataclass, field
from enum import StrEnum

# StrEnum, NOT (str, Enum): temporalio 1.28's JSON converter mis-decodes a `(str, Enum)`
# type hint as a list of characters (kind: "bug" -> ['b','u','g']; reproduced on Python
# 3.14, killed a live bug run 2026-07-02 at pm_prioritize). StrEnum round-trips correctly.


class FeedbackKind(StrEnum):
    BUG = "bug"
    FEATURE = "feature"


class Status(StrEnum):
    """Terminal (and a couple of in-flight) workflow states."""

    RUNNING = "running"
    SHIPPED = "shipped"
    REJECTED_BY_COUNCIL = "rejected_by_council"
    CLOSED_DUPLICATE = "closed_duplicate"
    HELD = "held"                # human declined the deploy gate
    ESCALATED = "escalated"      # a human gate timed out
    OVER_BUDGET = "over_budget"  # budget ceiling hit and the override was declined/timed out
    CI_FAILED = "ci_failed"      # the PR's CI was still red after the bounded fix loop; halted before merge
    QA_FAILED = "qa_failed"      # the QA agent failed the pod's output after the bounded fix loop; halted before deploy (symmetric with CI_FAILED)


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------
@dataclass
class FeedbackEvent:
    """Normalized feedback, produced by a project's intake adapter (M5)."""

    id: str
    kind: FeedbackKind
    title: str
    body: str
    submitted_by: str
    project: str  # Project Profile id, e.g. "meal-planner"


# ---------------------------------------------------------------------------
# Feature-request stages
# ---------------------------------------------------------------------------
@dataclass
class Brief:
    summary: str
    problem: str
    target_users: str
    ui_impacting: bool          # gates the conditional UX-mocks stage
    project: str = ""           # Project Profile id, carried so later stages (council) keep context
    complexity: str = ""        # PM's early whole-feature scope read (small|medium|large); drives the Opus→Sonnet downgrade for small features
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Vote:
    voter: str                  # "legal" | "sales" | "human"
    approve: bool
    rationale: str
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CouncilResult:
    votes: list[Vote]
    approved: bool
    escalated: bool             # True if the 72h timer fired before the human voted


@dataclass
class PRD:
    feature_id: str
    version: int
    content: str
    open_issues: list[str] = field(default_factory=list)
    project: str = ""           # Project Profile id, carried for downstream agent context
    complexity: str = ""        # carried from the brief; drives the Opus→Sonnet tier downgrade on small features
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ArchitectReview:
    approved: bool
    pass_no: int
    concerns: list[str] = field(default_factory=list)
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Mocks:
    present: bool
    ref: str                    # pointer into the artifact store (lightweight return)
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ResearchRequest:
    feature_id: str
    prd: PRD
    personas: list[str]
    max_iterations: int = 1


@dataclass
class ResearchFinding:
    persona: str
    sentiment: str              # "positive" | "neutral" | "negative"
    notes: str
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ResearchReport:
    feature_id: str
    findings: list[ResearchFinding]
    overall_sentiment: str
    summary_ref: str
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Story:
    id: str
    title: str
    estimate: int
    tier: str = "sonnet"        # coding-model tier the architect selected for this story
                                # (complex -> opus, simple -> sonnet); sizes the pod's model


@dataclass
class StoryPlan:
    feature_id: str
    stories: list[Story]
    project: str = ""           # Project Profile id, carried so the pod can load the target repo
    complexity: str = ""        # architect's whole-feature scope read (small|medium|large); bounds story count
    context: str = ""           # extra background handed verbatim to the coding agent (e.g. the
                                # bug report body on the bug path); untrusted text, quoted in the prompt
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class StoryResult:
    story_id: str
    status: str                 # "done" | "failed"
    pr_ref: str
    diff: str = ""              # unified diff the pod produced (assembled into the PR)
    summary: str = ""           # short note on what the coding attempt did
    build_status: str = ""      # honest in-sandbox verdict fed to the QA agent: "passed: …" |
                                # "failed: …" | "unavailable: …" (tests can't run there — not a failure)
    tier: str = ""              # coding-model tier that actually ran this attempt (traced so the
                                # audit shows which model tackled the work); "" for stub/non-agent
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class QAResult:
    passed: bool
    notes: str
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class PodResult:
    # One agent implements the whole ordered story plan in a single workspace, so the pod
    # produces exactly ONE coherent result (not a per-story list — that was the retired
    # fan-out design that shipped conflicting/partial diffs).
    story_result: StoryResult
    qa: QAResult
    branch: str
    pr_url: str = ""            # the PR the pod opened (or a local dry-run ref)
    review_approved: bool = True  # final code-review verdict (the reviewer<->developer loop ran BEFORE the PR opened)
    review_notes: str = ""      # the reviewer's final summary, surfaced to the human at the deploy gate
    ci_passed: bool = True      # did the PR's CI go green (after the bounded CI fix loop)? gates the merge. True when CI is unavailable (mock/local runs) so $0 dry-runs aren't blocked.
    ci_url: str = ""            # link to the CI run, surfaced to the human
    ci_notes: str = ""          # CI verdict / failing-check summary
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class CIResult:
    """Verdict from waiting on the opened PR's CI checks (CLAUDE.md §9, gate before merge).

    `status` is "passed" | "failed" | "unavailable" (no real CI — e.g. a mock/local PR target,
    so the gate is a no-op and the pod proceeds). `passed` is True for both "passed" and
    "unavailable" so $0 dry-runs are never blocked; only a real "failed" blocks the merge."""

    status: str
    passed: bool
    failing_summary: str = ""   # which checks failed + a short log excerpt, fed back to the developer
    url: str = ""               # link to the CI run
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class PRResult:
    """What the PR-open step produced — the pod's terminal artifact (a PR, or a local
    dry-run stand-in). `opened` is False when there were no changes to open a PR for."""

    opened: bool
    url: str = ""
    branch: str = ""
    note: str = ""
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class DeployResult:
    deployed: bool
    ref: str                    # PR url / release tag / container digest / merged branch
    note: str = ""
    cost_tokens: int = 0
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Bug stages
# ---------------------------------------------------------------------------
@dataclass
class Triage:
    kind: FeedbackKind
    priority: str               # "P0" | "P1" | "P2" | "P3"
    needs_clarification: bool
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class DedupeResult:
    is_duplicate: bool
    duplicate_of: str = ""
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class BugPriority:
    priority: str
    rationale: str
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ReviewResult:
    approved: bool
    notes: str                  # short human-readable verdict summary (goes into the PR body)
    required_changes: list[str] = field(default_factory=list)  # actionable items the developer must address (empty iff approved)
    cost_tokens: int = 0
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Human gates (M5 — the human-I/O channel, D1: Slack)
# ---------------------------------------------------------------------------
@dataclass
class GateNotice:
    """What a workflow tells the human-I/O channel when it parks at a gate.

    Built inside workflow code (deterministically — from state the workflow already
    holds) and handed to the ``notify_gate`` activity, whose live twin posts it to
    Slack with approve/reject buttons. ``gate`` is one of the names in
    ``orchestrator.humanio.gates`` ("council" | "pm_signoff" | "deploy" | "budget" |
    "clarification"); ``context`` is gate-specific lines for the human (agent votes,
    PR URL + QA/CI verdicts, ...)."""

    workflow_id: str
    gate: str
    title: str                  # the feedback title, so the human knows which request
    project: str
    cost_usd: float = 0.0       # spend so far, surfaced at every gate
    context: list[str] = field(default_factory=list)


@dataclass
class NotifyResult:
    """Outcome of a gate notification. Advisory only: ``delivered=False`` must never
    block the gate — the signal path and the gate's timeout work without Slack."""

    delivered: bool
    note: str = ""
    cost_tokens: int = 0
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Workflow-level state (queryable) + final result
# ---------------------------------------------------------------------------
@dataclass
class WorkflowState:
    stage: str
    status: str
    cost_tokens: int
    cost_usd: float = 0.0
    prd_version: int = 0
    council_approved: bool | None = None
    log: list[str] = field(default_factory=list)
    gate_context: list[str] = field(default_factory=list)  # what the human at the current gate needs (PR URL, verdicts, ...)


@dataclass
class WorkflowResult:
    feedback_id: str
    status: str
    cost_tokens: int
    summary: str
    cost_usd: float = 0.0
    stage_log: list[str] = field(default_factory=list)
