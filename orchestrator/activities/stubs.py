"""Stubbed activities for M1.

Every activity returns canned, deterministic data and makes **zero LLM calls** — the
whole point of M1 is to exercise the orchestration control flow, gates, timers, and
replay for $0 in tokens (CLAUDE.md §12). In M3 these bodies get swapped, one at a time,
for real calls to the generic Agent Runner; the *signatures* and return types stay put,
so the workflows never change.

These run inside Temporal activities (NOT workflows), so I/O and nondeterminism are
allowed here — but the stubs deliberately do neither.
"""

from temporalio import activity

from orchestrator.shared.estimates import estimate_coding_run
from orchestrator.shared.ids import feature_id as _feature_id
from orchestrator.shared.types import (
    ArchitectReview,
    Brief,
    BugPriority,
    CIResult,
    CodingEstimate,
    DedupeResult,
    DeployResult,
    FeedbackEvent,
    FeedbackKind,
    GateNotice,
    Mocks,
    NotifyResult,
    PRD,
    PRResult,
    ProgressNotice,
    QAResult,
    ResearchFinding,
    ResearchReport,
    ReviewResult,
    ScreenshotSet,
    Story,
    StoryPlan,
    StoryResult,
    Triage,
    Vote,
)


# --- feature-request activities -------------------------------------------------
@activity.defn
async def pm_draft_brief(event: FeedbackEvent) -> Brief:
    return Brief(
        summary=f"Brief for: {event.title}",
        problem="(stub) the problem this feature addresses",
        target_users="(stub) primary user segment",
        ui_impacting=True,  # stub takes the UX-mocks branch; tests can flip this
        project=event.project,
        cost_tokens=120,
    )


@activity.defn
async def council_agent_vote(persona: str, brief: Brief) -> Vote:
    # Stub: agents approve. Tests/real agents can dissent to exercise rejection.
    return Vote(
        voter=persona,
        approve=True,
        rationale=f"(stub) {persona} sees no blocker for '{brief.summary}'",
        cost_tokens=60,
    )


@activity.defn
async def pm_write_prd(brief: Brief) -> PRD:
    return PRD(
        feature_id=_feature_id(brief.summary),
        version=1,
        content=f"(stub) PRD v1 for {brief.summary}",
        open_issues=[],
        project=brief.project,
        cost_tokens=400,
    )


@activity.defn
async def architect_review_prd(prd: PRD, pass_no: int) -> ArchitectReview:
    # Stub approves on the first pass; tests force rejections to drive the bounded loop.
    return ArchitectReview(
        approved=True,
        pass_no=pass_no,
        concerns=[],
        cost_tokens=300,
    )


@activity.defn
async def pm_revise_prd(prd: PRD, review: ArchitectReview) -> PRD:
    return PRD(
        feature_id=prd.feature_id,
        version=prd.version + 1,
        content=f"(stub) PRD v{prd.version + 1} addressing: {', '.join(review.concerns) or 'feedback'}",
        open_issues=[],
        project=prd.project,
        cost_tokens=250,
    )


@activity.defn
async def ux_generate_mocks(prd: PRD) -> Mocks:
    return Mocks(
        present=True,
        ref=f"mocks/{prd.feature_id}",
        content=_mocks_md(prd),
        cost_tokens=200,
    )


def _mocks_md(prd: PRD) -> str:
    """Build the mock document as markdown — a low-fidelity textual wireframe derived from
    the PRD. Rendered to a PDF and uploaded into the run's thread (the old ``artifact://``
    ref was a dead link in Slack). Deterministic; a live UX persona would replace the prose
    with real mocks, filling the same ``Mocks.content`` field."""
    return "\n".join(
        [
            f"# UX mocks — {prd.feature_id}",
            "",
            "_Low-fidelity textual wireframes. (Stub output — a live UX agent would attach"
            " visual mocks here, populating the same field.)_",
            "",
            "## Entry point",
            "The feature is reached from the existing primary navigation; a new affordance"
            " (button/section) is added without displacing current actions.",
            "",
            "## Screen — main view",
            "- **Header:** screen title + a one-line description of the feature.",
            "- **Body:** the primary control(s) the PRD calls for, laid out in a single"
            " scannable column; empty, loading, and populated states are all shown.",
            "- **Footer / actions:** confirm / cancel, with the confirm disabled until the"
            " form is valid.",
            "",
            "## States",
            "- **Empty:** a short prompt explaining what to add and why.",
            "- **Populated:** the entered data rendered back for review, each row editable/removable.",
            "- **Error:** inline validation next to the offending field, non-blocking.",
            "",
            "## Notes for engineering",
            "- Reuse existing components and spacing tokens; no new design primitives.",
            "- Mobile: the single-column layout collapses cleanly; controls stay reachable.",
            "",
            "---",
            "_Derived from PRD:_",
            "",
            (prd.content or "(no PRD content)").strip(),
        ]
    )


@activity.defn
async def consumer_research_persona(persona: str, prd: PRD) -> ResearchFinding:
    return ResearchFinding(
        persona=persona,
        sentiment="positive",
        notes=f"(stub) {persona} would use {prd.feature_id}",
        cost_tokens=90,
    )


@activity.defn
async def synthesize_research(findings: list[ResearchFinding]) -> ResearchReport:
    positives = sum(1 for f in findings if f.sentiment == "positive")
    overall = "positive" if positives * 2 >= len(findings) else "mixed"
    fid = findings[0].notes.split()[-1] if findings else "unknown"
    return ResearchReport(
        feature_id=fid,
        findings=findings,
        overall_sentiment=overall,
        summary_ref="artifact://research/summary",
        cost_tokens=180,
    )


@activity.defn
async def architect_plan_stories(prd: PRD, report: ResearchReport) -> StoryPlan:
    # Tiers canned to show the model-selection phase even on $0 stub runs: the harder backend
    # slice -> opus, the routine frontend slice -> sonnet. The agent-backed twin sets these
    # from the architect's per-story complexity read.
    stories = [
        Story(id=f"{prd.feature_id}-S1", title="(stub) backend slice", estimate=3, tier="opus"),
        Story(id=f"{prd.feature_id}-S2", title="(stub) frontend slice", estimate=2, tier="sonnet"),
    ]
    return StoryPlan(
        feature_id=prd.feature_id, stories=stories, project=prd.project, cost_tokens=350
    )


@activity.defn
async def estimate_coding_budget(plan: StoryPlan) -> CodingEstimate:
    # Stub for the pre-pod coding-budget gate: computes the same deterministic estimate
    # as the agent-backed twin (so the numbers are visible even on dry runs) but returns
    # gate=False — a $0 stub run has nothing to fund, so the workflow never parks here.
    # The live twin (USE_AGENT_CODING=1, coding_backed.py) returns gate=True: a human
    # funds the round (or sets a custom budget) before real coding spends anything.
    usd, breakdown = estimate_coding_run(plan.stories)
    return CodingEstimate(estimate_usd=usd, gate=False, breakdown=breakdown)


@activity.defn
async def implement_stories(plan: StoryPlan) -> StoryResult:
    # Stub for the Agent SDK coding run (M4): one agent implements the whole story plan in
    # one workspace. Always "done" in M1; the agent-backed twin (same name) does the real work.
    return StoryResult(
        story_id=plan.feature_id,
        status="done",
        pr_ref=f"pr://{plan.feature_id}",
        summary="(stub) implemented",
        cost_tokens=800,
    )


@activity.defn
async def qa_review(project: str, story_results: list[StoryResult]) -> QAResult:
    all_done = all(r.status == "done" for r in story_results)
    return QAResult(
        passed=all_done,
        notes="(stub) all checks green" if all_done else "(stub) failing stories",
        cost_tokens=150,
    )


@activity.defn
async def capture_screenshots(project: str, story_results: list[StoryResult]) -> ScreenshotSet:
    # Stub for the post-QA screenshot capture. The live twin (USE_AGENT_CODING=1,
    # coding_backed.py) boots the target's preview stack with the pod's diff applied and
    # screenshots the profile's routes; the stub is a $0 no-op so dry runs stay free.
    # Advisory either way: a missing screenshot never blocks the run.
    return ScreenshotSet(captured=False, note="(stub) no preview capture")


@activity.defn
async def review_diff(plan: StoryPlan, story_result: StoryResult) -> ReviewResult:
    # Stub for the reviewer↔developer loop (runs BEFORE the PR opens). The agent-backed twin
    # runs a reasoning-plane code reviewer over the diff. The stub approves so the loop is a
    # no-op at $0; tests can override it to force a revise pass.
    return ReviewResult(
        approved=True, notes="(stub) LGTM — diff approved", required_changes=[], cost_tokens=90
    )


@activity.defn
async def revise_after_review(
    plan: StoryPlan, story_result: StoryResult, review: ReviewResult
) -> StoryResult:
    # Stub for the developer's revision pass — the agent-backed twin re-runs the coding pod
    # with the reviewer's required changes appended. The stub echoes the prior result as "done".
    return StoryResult(
        story_id=story_result.story_id,
        status="done",
        pr_ref=story_result.pr_ref,
        diff=story_result.diff,
        summary=f"(stub) revised to address: {', '.join(review.required_changes) or 'review feedback'}",
        cost_tokens=800,
    )


@activity.defn
async def open_pr(
    project: str, branch: str, story_results: list[StoryResult], review_summary: str = ""
) -> PRResult:
    # Stub for the pod's PR-open step (M4). The agent-backed twin clones the target, applies
    # the story diffs, and opens a real (or local dry-run) PR. Always "opened" in M1.
    return PRResult(opened=True, url=f"local://pr/{branch}", branch=branch, cost_tokens=10)


@activity.defn
async def await_ci(project: str, branch: str, pr_url: str) -> CIResult:
    # Stub for the CI gate. The agent-backed twin waits on the real PR checks; the stub reports
    # "unavailable" (no real CI for a $0 run) so the pod's CI loop is a no-op. Tests override it
    # to drive the fail->fix->pass path.
    return CIResult(status="unavailable", passed=True, failing_summary="", url=pr_url, cost_tokens=5)


@activity.defn
async def revise_after_ci(plan: StoryPlan, story_result: StoryResult, ci: CIResult) -> StoryResult:
    # Stub for the developer's CI-fix pass — the agent-backed twin re-runs the coding pod with
    # the failing-check summary appended. The stub echoes the prior result as "done".
    return StoryResult(
        story_id=story_result.story_id,
        status="done",
        pr_ref=story_result.pr_ref,
        diff=story_result.diff,
        summary=f"(stub) revised for CI: {ci.failing_summary or 'failing checks'}",
        cost_tokens=800,
    )


@activity.defn
async def update_pr(project: str, branch: str, story_results: list[StoryResult]) -> PRResult:
    # Stub for pushing a CI fix to the existing PR. The agent-backed twin force-updates the
    # branch so the open PR re-runs CI; the stub just reports success.
    return PRResult(opened=True, url=f"local://pr/{branch}", branch=branch, note="(stub) updated", cost_tokens=10)


@activity.defn
async def notify_gate(notice: GateNotice) -> NotifyResult:
    # Stub for the human-I/O channel (M5, D1: Slack). The live twin (ORG_SLACK=1,
    # orchestrator/humanio/notify.py) posts a Block Kit message with approve/reject
    # buttons; the stub is a $0 no-op so tests and stub runs never touch Slack. Advisory
    # either way: the gate's signal + timeout work whether or not this delivers.
    return NotifyResult(delivered=False, note="(stub) no human-I/O channel")


@activity.defn
async def notify_progress(notice: ProgressNotice) -> NotifyResult:
    # Stub for the run's Slack progress thread (M5 human-I/O). The live twin (ORG_SLACK=1)
    # posts each stage into the run's thread and uploads document_md artifacts as PDFs;
    # the stub is a $0 no-op. Advisory either way — a failed post never blocks the run.
    return NotifyResult(delivered=False, note="(stub) no human-I/O channel")


@activity.defn
async def deploy(project: str, branch: str) -> DeployResult:
    # Stub for the Project Profile's deploy target (PR/merge/container). Only ever
    # reached behind the human deploy-approval gate (CLAUDE.md §9.2).
    return DeployResult(deployed=True, ref=f"release://{project}/{branch}", cost_tokens=20)


# --- bug activities -------------------------------------------------------------
@activity.defn
async def triage_feedback(event: FeedbackEvent) -> Triage:
    return Triage(
        kind=event.kind,
        priority="P2",
        needs_clarification=False,  # stub skips the clarification gate; tests flip it
        cost_tokens=40,
    )


@activity.defn
async def dedupe_check(event: FeedbackEvent) -> DedupeResult:
    return DedupeResult(is_duplicate=False, cost_tokens=30)


@activity.defn
async def pm_prioritize_bug(event: FeedbackEvent, triage: Triage) -> BugPriority:
    return BugPriority(priority=triage.priority, rationale="(stub) prioritized", cost_tokens=80)


# (fix_bug / review_fix are gone: the bug path now rides the same EngineeringPodWorkflow
# as features — one pod, two entry points — so the pod's implement/review/QA/PR/CI
# machinery covers bugs too.)


# Registered with the worker. Adding an activity = appending here.
ALL_ACTIVITIES = [
    pm_draft_brief,
    council_agent_vote,
    pm_write_prd,
    architect_review_prd,
    pm_revise_prd,
    ux_generate_mocks,
    consumer_research_persona,
    synthesize_research,
    architect_plan_stories,
    estimate_coding_budget,
    implement_stories,
    qa_review,
    capture_screenshots,
    review_diff,
    revise_after_review,
    open_pr,
    await_ci,
    revise_after_ci,
    update_pr,
    notify_gate,
    notify_progress,
    deploy,
    triage_feedback,
    dedupe_check,
    pm_prioritize_bug,
]
