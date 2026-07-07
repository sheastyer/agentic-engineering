"""Activity overrides for branch testing.

Each override is registered under the *same activity name* as a stub (via ``name=``),
so swapping it into the worker's activity list changes what that stage returns without
touching the workflow. Real M3 agents will be swapped in the same way.
"""

from temporalio import activity

from orchestrator.shared.estimates import estimate_coding_run
from orchestrator.shared.types import (
    ArchitectReview,
    Brief,
    CodingEstimate,
    DedupeResult,
    FeedbackEvent,
    FeedbackKind,
    PRD,
    StoryPlan,
    Triage,
    Vote,
)


@activity.defn(name="council_agent_vote")
async def council_vote_reject(persona: str, brief: Brief) -> Vote:
    return Vote(voter=persona, approve=False, rationale="(test) blocked", cost_tokens=1)


@activity.defn(name="architect_review_prd")
async def architect_review_always_reject(prd: PRD, pass_no: int) -> ArchitectReview:
    return ArchitectReview(
        approved=False, pass_no=pass_no, concerns=["(test) never satisfied"], cost_tokens=1
    )


@activity.defn(name="dedupe_check")
async def dedupe_is_duplicate(event: FeedbackEvent) -> DedupeResult:
    return DedupeResult(is_duplicate=True, duplicate_of="bug-001", cost_tokens=1)


@activity.defn(name="triage_feedback")
async def triage_needs_clarification(event: FeedbackEvent) -> Triage:
    return Triage(
        kind=event.kind, priority="P2", needs_clarification=True, cost_tokens=1
    )


@activity.defn(name="estimate_coding_budget")
async def estimate_coding_budget_gated(plan: StoryPlan) -> CodingEstimate:
    # Live-twin stand-in: the same deterministic estimate the stub computes, but
    # gate=True — so tests can drive the pre-pod coding-budget gate on $0 stubs.
    usd, breakdown = estimate_coding_run(plan.stories)
    return CodingEstimate(estimate_usd=usd, gate=True, breakdown=breakdown, cost_tokens=1)


@activity.defn(name="pm_draft_brief")
async def pm_draft_brief_expensive(event: FeedbackEvent) -> Brief:
    # Returns $5 of cost on the very first stage — over the $3 feature ceiling, so the
    # budget gate trips immediately after the brief.
    return Brief(
        summary="(test) expensive brief",
        problem="",
        target_users="",
        ui_impacting=True,
        cost_usd=5.0,
    )
