"""Runner-backed activities — the M3 swap targets (one real persona at a time).

This is the bridge between the generic Agent Runner and the workflow's plain dataclasses:
the runner returns a Pydantic contract instance; the activity adapts it into the
replay-serialized workflow type (orchestrator/shared/types.py), carrying the real dollar
cost. The core logic is a plain function so it can be unit-tested with a fake client for
$0; the @activity.defn wrapper supplies the real (lazy-built) client at runtime.

NOT yet registered in the worker's ALL_ACTIVITIES — swapping it in (under the stub's
activity name) is the M3 step, done once live auth is available. Until then the stub
remains the default so M1/M2 stay green and token-free.
"""

from temporalio import activity

from orchestrator.agents.provider import ModelProvider
from orchestrator.agents.providers.factory import build_provider
from orchestrator.agents.registry import COUNCIL_PERSONA_BY_VOTER, get_persona
from orchestrator.agents.registry.contracts import (
    ArchitectReviewOutput,
    BriefOutput,
    BugPriorityOutput,
    CodeReviewOutput,
    CouncilVoteOutput,
    PRDAuthoringOutput,
    PRDRevisionOutput,
    ResearchFindingOutput,
    StoryPlanOutput,
    TriageOutput,
)
from orchestrator.agents.runner import AgentRunner
from orchestrator.projects.loader import load_profile
from orchestrator.shared.ids import feature_id
from orchestrator.shared.types import (
    PRD,
    ArchitectReview,
    Brief,
    BugPriority,
    FeedbackEvent,
    FeedbackKind,
    ResearchFinding,
    ResearchReport,
    ReviewResult,
    Story,
    StoryPlan,
    StoryResult,
    Triage,
    Vote,
)

# A diff can be large; cap what we feed the reviewer so review-prompt tokens stay bounded
# (§10). The head of a unified diff carries the substance — file headers + the edits.
_MAX_REVIEW_DIFF_CHARS = 14000


def _tier_for(default_tier: str, complexity: str) -> str:
    """Reasoning cost lever (§10): the Opus stages (PRD authoring, architect review, story
    planning) dominate a feature's reasoning tokens, but a small, well-scoped feature doesn't
    need Opus — downgrade it to Sonnet. Medium / large / unknown complexity keep the default
    tier, so anything non-trivial is unaffected."""
    if default_tier == "opus" and complexity == "small":
        return "sonnet"
    return default_tier


def triage_with_runner(provider: ModelProvider, event: FeedbackEvent) -> Triage:
    """Real triage via the Agent Runner. Pure (provider injected) for $0 unit testing."""
    profile = load_profile(event.project)
    persona = get_persona("triage")
    task_input = f"Title: {event.title}\n\n{event.body}"

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: TriageOutput = result.payload
    return Triage(
        kind=FeedbackKind(out.kind),
        priority=out.priority,
        needs_clarification=out.needs_clarification,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="triage_feedback")
async def triage_feedback_agent(event: FeedbackEvent) -> Triage:
    """Live triage. Provider chosen by MODEL_PROVIDER env (default anthropic = your
    subscription). Registered under the stub's name so the M3 swap is a one-liner."""
    return triage_with_runner(build_provider(), event)


def draft_brief_with_runner(provider: ModelProvider, event: FeedbackEvent) -> Brief:
    """Real PM brief authoring (Opus) — feature-path stage 1. The model owns the brief
    fields; `project` is carried from the event so downstream stages keep context. Pure
    (provider injected) for $0 unit testing."""
    profile = load_profile(event.project)
    persona = get_persona("pm_draft_brief")
    task_input = f"Feature request:\nTitle: {event.title}\n\n{event.body}"

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: BriefOutput = result.payload
    return Brief(
        summary=out.summary,
        problem=out.problem,
        target_users=out.target_users,
        ui_impacting=out.ui_impacting,
        complexity=out.complexity,  # early scope signal — drives the Opus→Sonnet downgrade downstream
        project=event.project,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="pm_draft_brief")
async def pm_draft_brief_agent(event: FeedbackEvent) -> Brief:
    """Live PM brief authoring. Registered under the stub's name so the swap is a one-liner."""
    return draft_brief_with_runner(build_provider(), event)


def council_vote_with_runner(provider: ModelProvider, voter: str, brief: Brief) -> Vote:
    """Real council vote via the Agent Runner. `voter` is the workflow's voter id
    ("legal"/"sales"); it selects the lens-specific persona. Pure for $0 unit testing."""
    persona = get_persona(COUNCIL_PERSONA_BY_VOTER[voter])
    profile = load_profile(brief.project)
    task_input = (
        f"Brief summary: {brief.summary}\n"
        f"Problem: {brief.problem}\n"
        f"Target users: {brief.target_users}\n"
        f"UI-impacting: {brief.ui_impacting}"
    )

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: CouncilVoteOutput = result.payload
    return Vote(
        voter=voter,
        approve=out.approve,
        rationale=out.rationale,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="council_agent_vote")
async def council_agent_vote_agent(persona: str, brief: Brief) -> Vote:
    """Live council vote. Registered under the stub's name so the swap is a one-liner.
    `persona` is the workflow's voter id (legal/sales)."""
    return council_vote_with_runner(build_provider(), persona, brief)


def research_finding_with_runner(provider: ModelProvider, demographic: str, prd: PRD) -> ResearchFinding:
    """Real synthetic-consumer finding via the Agent Runner. `demographic` is the panel
    descriptor the persona embodies. Pure (provider injected) for $0 unit testing."""
    persona = get_persona("consumer_researcher")
    profile = load_profile(prd.project)
    task_input = (
        f"Demographic to embody: {demographic}\n\n"
        f"Proposed feature (PRD):\n{prd.content}"
    )

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: ResearchFindingOutput = result.payload
    return ResearchFinding(
        persona=demographic,
        sentiment=out.sentiment,
        notes=out.notes,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="consumer_research_persona")
async def consumer_research_persona_agent(persona: str, prd: PRD) -> ResearchFinding:
    """Live synthetic-consumer finding. Registered under the stub's name; `persona` is the
    panel demographic descriptor (DEFAULT_RESEARCH_PERSONAS)."""
    return research_finding_with_runner(build_provider(), persona, prd)


def author_prd_with_runner(provider: ModelProvider, brief: Brief) -> PRD:
    """Real PRD authoring (Opus) via the Agent Runner. feature_id/version/project are set
    deterministically here so the workflow contract stays predictable; the model owns the
    PRD body. Pure (provider injected) for $0 unit testing."""
    persona = get_persona("pm_write_prd")
    profile = load_profile(brief.project)
    task_input = (
        f"Feature brief:\n"
        f"- Summary: {brief.summary}\n"
        f"- Problem: {brief.problem}\n"
        f"- Target users: {brief.target_users}\n"
        f"- UI-impacting: {brief.ui_impacting}"
    )

    result = AgentRunner(provider).run(
        persona, profile, task_input, tier=_tier_for(persona.tier, brief.complexity)
    )
    out: PRDAuthoringOutput = result.payload
    return PRD(
        feature_id=feature_id(brief.summary),
        version=1,
        content=out.content,
        open_issues=out.open_issues,
        project=brief.project,
        complexity=brief.complexity,  # carried so the architect stages reuse the same tier lever
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="pm_write_prd")
async def pm_write_prd_agent(brief: Brief) -> PRD:
    """Live PRD authoring. Registered under the stub's name so the swap is a one-liner."""
    return author_prd_with_runner(build_provider(), brief)


def revise_prd_with_runner(provider: ModelProvider, prd: PRD, review: ArchitectReview) -> PRD:
    """Real PRD revision via the Agent Runner. The version bump and identity fields
    (feature_id, project) are set deterministically here — only the prose comes from the
    model — so the workflow's versioning stays predictable. Pure for $0 unit testing."""
    persona = get_persona("pm_revise_prd")
    profile = load_profile(prd.project)
    concerns = "\n".join(f"- {c}" for c in review.concerns) or "- (general revision requested)"
    task_input = (
        f"Current PRD (v{prd.version}):\n{prd.content}\n\n"
        f"Concerns to resolve:\n{concerns}"
    )

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: PRDRevisionOutput = result.payload
    return PRD(
        feature_id=prd.feature_id,
        version=prd.version + 1,
        content=out.content,
        open_issues=out.open_issues,
        project=prd.project,
        complexity=prd.complexity,  # preserved across revisions so the tier lever persists
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="pm_revise_prd")
async def pm_revise_prd_agent(prd: PRD, review: ArchitectReview) -> PRD:
    """Live PRD revision. Registered under the stub's name so the swap is a one-liner."""
    return revise_prd_with_runner(build_provider(), prd, review)


def review_prd_with_runner(provider: ModelProvider, prd: PRD, pass_no: int) -> ArchitectReview:
    """Real architect review (Opus) of a PRD, driving the bounded PRD↔architect loop. The
    workflow owns `pass_no` (loop control) so it's set here, not by the model; the model
    owns the approve/concerns judgment. Pure (provider injected) for $0 unit testing."""
    persona = get_persona("architect_review_prd")
    profile = load_profile(prd.project)
    open_issues = "\n".join(f"- {i}" for i in prd.open_issues) or "- (none)"
    task_input = (
        f"PRD (v{prd.version}) under review:\n{prd.content}\n\n"
        f"Open issues flagged by the author:\n{open_issues}"
    )

    result = AgentRunner(provider).run(
        persona, profile, task_input, tier=_tier_for(persona.tier, prd.complexity)
    )
    out: ArchitectReviewOutput = result.payload
    return ArchitectReview(
        approved=out.approved,
        pass_no=pass_no,
        concerns=out.concerns,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="architect_review_prd")
async def architect_review_prd_agent(prd: PRD, pass_no: int) -> ArchitectReview:
    """Live architect PRD review. Registered under the stub's name so the swap is a one-liner."""
    return review_prd_with_runner(build_provider(), prd, pass_no)


def plan_stories_with_runner(provider: ModelProvider, prd: PRD, report: ResearchReport) -> StoryPlan:
    """Real architect story breakdown (Opus). Story ids are minted deterministically here
    (`{feature_id}-S{n}`) so the workflow's downstream references stay predictable; the model
    owns titles/estimates and how the work is sliced. Pure for $0 unit testing."""
    persona = get_persona("architect_plan_stories")
    profile = load_profile(prd.project)
    task_input = (
        f"Approved PRD (v{prd.version}):\n{prd.content}\n\n"
        f"Consumer-research sentiment: {report.overall_sentiment} "
        f"(from {len(report.findings)} personas)"
    )

    result = AgentRunner(provider).run(
        persona, profile, task_input, tier=_tier_for(persona.tier, prd.complexity)
    )
    out: StoryPlanOutput = result.payload
    stories = [
        Story(id=f"{prd.feature_id}-S{i + 1}", title=s.title, estimate=s.estimate)
        for i, s in enumerate(out.stories)
    ]
    return StoryPlan(
        feature_id=prd.feature_id,
        stories=stories,
        project=prd.project,  # carried so the engineering pod can load the target profile
        complexity=out.complexity,  # scope signal — bounds story count, traced for cost analysis
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="architect_plan_stories")
async def architect_plan_stories_agent(prd: PRD, report: ResearchReport) -> StoryPlan:
    """Live architect story planning. Registered under the stub's name so the swap is a one-liner."""
    return plan_stories_with_runner(build_provider(), prd, report)


def prioritize_bug_with_runner(provider: ModelProvider, event: FeedbackEvent, triage: Triage) -> BugPriority:
    """Real PM bug prioritization (Haiku) — bug-path. The model sees the report and the triage
    read and sets the final priority. Pure (provider injected) for $0 unit testing."""
    profile = load_profile(event.project)
    persona = get_persona("pm_prioritize_bug")
    task_input = (
        f"Bug report:\nTitle: {event.title}\n\n{event.body}\n\n"
        f"Triage read: kind={triage.kind.value}, initial priority={triage.priority}"
    )

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: BugPriorityOutput = result.payload
    return BugPriority(
        priority=out.priority,
        rationale=out.rationale,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="pm_prioritize_bug")
async def pm_prioritize_bug_agent(event: FeedbackEvent, triage: Triage) -> BugPriority:
    """Live PM bug prioritization. Registered under the stub's name so the swap is a one-liner."""
    return prioritize_bug_with_runner(build_provider(), event, triage)


def review_diff_with_runner(
    provider: ModelProvider, plan: StoryPlan, story_result: StoryResult
) -> ReviewResult:
    """Real code review (Sonnet, reasoning plane) of the pod's diff — the reviewer half of the
    pod's reviewer↔developer loop that runs BEFORE the PR opens. Reviewing a diff is single-shot
    structured reasoning, so it runs through the Agent Runner (not the coding pod): cheap, exact
    cost, no subscription-window draw. Pure (provider injected) for $0 unit testing."""
    persona = get_persona("code_reviewer")
    profile = load_profile(plan.project)
    stories = "\n".join(f"{i}. {s.title}" for i, s in enumerate(plan.stories, 1))
    diff = story_result.diff.strip() or "(the developer produced no diff)"
    if len(diff) > _MAX_REVIEW_DIFF_CHARS:
        diff = diff[:_MAX_REVIEW_DIFF_CHARS] + "\n…(diff truncated for review)…"
    task_input = (
        f"Planned stories this change must deliver:\n{stories}\n\n"
        f"Developer's note: {story_result.summary or '(none)'}\n\n"
        f"Unified diff under review:\n{diff}"
    )

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: CodeReviewOutput = result.payload
    return ReviewResult(
        approved=out.approved,
        notes=out.summary,
        required_changes=out.required_changes,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="review_diff")
async def review_diff_agent(plan: StoryPlan, story_result: StoryResult) -> ReviewResult:
    """Live code review of the pod's diff. Registered under the stub's name so the swap is a
    one-liner. Reasoning plane (MODEL_PROVIDER), not the coding subscription."""
    return review_diff_with_runner(build_provider(), plan, story_result)
