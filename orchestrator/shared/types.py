"""Data contracts that flow between workflow stages.

Plain dataclasses so Temporal's default JSON converter can serialize them across the
activity/workflow boundary and persist them in history. Every stage result carries a
``cost_tokens`` field so the workflow can accumulate spend (CLAUDE.md §10); the stubs
return small canned values and real agents (M3+) will return measured token counts.
"""

from dataclasses import dataclass, field
from enum import Enum


class FeedbackKind(str, Enum):
    BUG = "bug"
    FEATURE = "feature"


class Status(str, Enum):
    """Terminal (and a couple of in-flight) workflow states."""

    RUNNING = "running"
    SHIPPED = "shipped"
    REJECTED_BY_COUNCIL = "rejected_by_council"
    CLOSED_DUPLICATE = "closed_duplicate"
    HELD = "held"                # human declined the deploy gate
    ESCALATED = "escalated"      # a human gate timed out
    OVER_BUDGET = "over_budget"  # budget ceiling hit and the override was declined/timed out


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


@dataclass
class StoryPlan:
    feature_id: str
    stories: list[Story]
    project: str = ""           # Project Profile id, carried so the pod can load the target repo
    complexity: str = ""        # architect's whole-feature scope read (small|medium|large); bounds story count
    cost_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class StoryResult:
    story_id: str
    status: str                 # "done" | "failed"
    pr_ref: str
    diff: str = ""              # unified diff the pod produced (assembled into the PR)
    summary: str = ""           # short note on what the coding attempt did
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
    notes: str
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


@dataclass
class WorkflowResult:
    feedback_id: str
    status: str
    cost_tokens: int
    summary: str
    cost_usd: float = 0.0
    stage_log: list[str] = field(default_factory=list)
