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

import logging
import re

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
    QAReviewOutput,
    ResearchFindingOutput,
    StoryPlanOutput,
    TriageOutput,
)
from orchestrator.agents.runner import AgentRunner
from orchestrator.projects.loader import load_profile
from orchestrator.shared.errors import NonRetryableAgentError
from orchestrator.shared.ids import feature_id
from orchestrator.shared.types import (
    PRD,
    ArchitectReview,
    Brief,
    BugPriority,
    FeedbackEvent,
    FeedbackKind,
    QAResult,
    ResearchFinding,
    ResearchReport,
    ReviewResult,
    Story,
    StoryPlan,
    StoryResult,
    Triage,
    Vote,
)

_log = logging.getLogger(__name__)

# Cap what we feed the reviewer so review-prompt tokens stay bounded (§10). Generous on
# purpose: Sonnet is 1M-context and review tokens are trivial next to the coding pod, so this
# only trips on genuinely huge diffs. When it does trip we truncate PER FILE (never silently
# drop the tail of the diff — that made the reviewer hallucinate whole files as "missing") and
# the reviewer always gets the full changed-file list regardless.
_MAX_REVIEW_DIFF_CHARS = 60000


def _split_diff_by_file(diff: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a unified diff into (preamble, [(path, section), ...]) on `diff --git` markers.
    `path` is the post-image (b/) path; `section` is that file's full hunk text."""
    sections: list[tuple[str, str]] = []
    preamble: list[str] = []
    cur_path: str | None = None
    cur: list[str] = []

    def _flush() -> None:
        if cur_path is not None:
            sections.append((cur_path, "".join(cur)))

    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            _flush()
            m = re.match(r"diff --git a/(.*?) b/(.*?)\s*$", line)
            cur_path = m.group(2) if m else "(unknown path)"
            cur = [line]
        elif cur_path is None:
            preamble.append(line)
        else:
            cur.append(line)
    _flush()
    return "".join(preamble), sections


def _render_diff_for_review(diff: str) -> tuple[str, list[str], list[str]]:
    """Render the diff for the reviewer within the char budget, truncating per file.

    Returns (rendered_diff, changed_files, truncated_files). `changed_files` is ALWAYS the
    complete list (so the reviewer can never mistake a present-but-unshown file for a missing
    one); `truncated_files` names every file whose hunks were cut or omitted to fit the budget.
    """
    diff = diff.strip()
    if not diff:
        return "(the developer produced no diff)", [], []
    preamble, sections = _split_diff_by_file(diff)
    changed = [p for p, _ in sections]
    if not sections:  # unparseable / no per-file structure — flat truncation, still flagged
        if len(diff) > _MAX_REVIEW_DIFF_CHARS:
            return diff[:_MAX_REVIEW_DIFF_CHARS] + "\n…(diff truncated)…", changed, ["(entire diff)"]
        return diff, changed, []

    out: list[str] = [preamble] if preamble.strip() else []
    used = len(preamble) if preamble.strip() else 0
    truncated: list[str] = []
    for path, section in sections:
        remaining = _MAX_REVIEW_DIFF_CHARS - used
        if len(section) <= remaining:
            out.append(section)
            used += len(section)
        elif remaining > 400:  # room for a meaningful partial — include the head, mark truncated
            out.append(section[:remaining] + f"\n…(hunks for {path} truncated to fit review budget)…\n")
            used += remaining
            truncated.append(path)
        else:  # budget exhausted — keep the file visible by name, omit its hunks
            out.append(f"diff --git a/{path} b/{path}\n…(hunks for {path} omitted — review budget exhausted)…\n")
            truncated.append(path)
    return "".join(out), changed, truncated


# Per-story model selection (the architect's "model-selection phase"): a story's
# implementation complexity picks the coding tier the engineering pod builds it with — a
# complex/hard story gets Opus, a routine one gets Sonnet (CLAUDE.md §5/§10: don't run a
# simple button on Opus). Deterministic policy here, not a model picking a model id.
_CODING_TIER_BY_STORY_COMPLEXITY = {"simple": "sonnet", "complex": "opus"}


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
        Story(
            id=f"{prd.feature_id}-S{i + 1}",
            title=s.title,
            estimate=s.estimate,
            # Model-selection phase: the architect's per-story complexity read picks the coding
            # tier (complex -> opus, simple -> sonnet), carried to the pod and the trace.
            tier=_CODING_TIER_BY_STORY_COMPLEXITY[s.complexity],
        )
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

    diff, changed_files, truncated_files = _render_diff_for_review(story_result.diff)
    files_list = "\n".join(f"- {p}" for p in changed_files) or "- (no files changed)"
    truncation_note = ""
    if truncated_files:
        # Traceability: name the elided files in the worker log AND tell the reviewer they are
        # present (not missing) — the truncation false-negative that shipped before this fix.
        _log.warning(
            "code-review diff exceeded %d chars for feature %s; hunks truncated/omitted for: %s "
            "(the reviewer still receives the full changed-file list and a not-missing note)",
            _MAX_REVIEW_DIFF_CHARS, plan.feature_id, ", ".join(truncated_files),
        )
        truncation_note = (
            "\n\nIMPORTANT: the diff was large, so the hunks for these files were truncated or "
            "omitted below ONLY to fit the review budget — they ARE part of this change. Do NOT "
            "report them as missing or undelivered:\n"
            + "\n".join(f"- {p}" for p in truncated_files)
        )
    task_input = (
        f"Planned stories this change must deliver:\n{stories}\n\n"
        f"Developer's note: {story_result.summary or '(none)'}\n\n"
        f"All files changed by this diff (the complete set):\n{files_list}{truncation_note}\n\n"
        f"Unified diff under review:\n{diff}"
    )

    try:
        result = AgentRunner(provider).run(persona, profile, task_input)
    except NonRetryableAgentError as e:
        # The reviewer is an ADVISORY pre-PR quality gate; CI is the HARD gate (§8a, §9.2). If the
        # reasoning model can't return a schema-valid review (observed with Sonnet on the Vercel
        # gateway 2026-06-30, where a reviewer crash killed the whole feature workflow AFTER the
        # ~$1.28 coding pass), degrade to a non-blocking pass instead of raising — the same
        # "return a result, never raise after the work is done" rule the coding pod already honors
        # (§10). We deliberately do NOT degrade to approved=False: a *systematic* parse failure
        # would then fail every re-review and burn a full coding revise per MAX_REVIEW_PASSES — a
        # cost leak. Proceed to open_pr and let CI catch a genuine build break. (Cost of the failed
        # re-ask attempts isn't recoverable from the exception, so it's a minor undercount here.)
        _log.warning(
            "code review unavailable for feature %s (%s); proceeding without a blocking review — "
            "CI remains the hard gate", plan.feature_id, e,
        )
        return ReviewResult(
            approved=True,
            notes="Automated code review was unavailable (reviewer produced no schema-valid "
            "output after bounded re-asks); proceeding without a blocking review — CI is the gate.",
            required_changes=[],
            cost_tokens=0,
            cost_usd=0.0,
        )
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


def qa_review_with_runner(
    provider: ModelProvider, project: str, story_results: list[StoryResult]
) -> QAResult:
    """Real functional QA (Sonnet, reasoning plane) — the verdict recorded for the human at the
    deploy gate. Unlike the stub (which just mirrored each story's status), this weighs the
    developer's summary against the actual diff and the objective build/test status, so an
    optimistic self-report over an empty/broken diff no longer reads as a pass. Single-shot
    structured reasoning via the Agent Runner — cheap, exact cost, no subscription draw. Pure
    (provider injected) for $0 unit testing."""
    persona = get_persona("qa_reviewer")
    profile = load_profile(project)

    blocks = []
    for i, r in enumerate(story_results, 1):
        diff, changed_files, truncated_files = _render_diff_for_review(r.diff)
        files_list = "\n".join(f"- {p}" for p in changed_files) or "- (no files changed)"
        note = ""
        if truncated_files:
            note = (
                "\n(NOTE: large diff — hunks for these files were truncated ONLY to fit the QA "
                "budget; they ARE part of the change, do not treat them as missing: "
                + ", ".join(truncated_files) + ")"
            )
        blocks.append(
            f"=== Attempt {i}: {r.story_id} ===\n"
            f"Objective build/test status: {r.status}\n"
            f"Developer's summary: {r.summary or '(none)'}\n"
            f"Files changed (complete set):\n{files_list}{note}\n"
            f"Unified diff:\n{diff}"
        )
    task_input = "\n\n".join(blocks) or "(no coding attempts to QA)"

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: QAReviewOutput = result.payload
    return QAResult(
        passed=out.passed,
        notes=out.notes,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="qa_review")
async def qa_review_agent(project: str, story_results: list[StoryResult]) -> QAResult:
    """Live functional QA over the pod's attempt(s). Registered under the stub's name so the
    swap is a one-liner. Reasoning plane (MODEL_PROVIDER), not the coding subscription."""
    return qa_review_with_runner(build_provider(), project, story_results)
