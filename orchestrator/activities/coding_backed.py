"""Agent-backed engineering-pod activities — the M4 swap targets for the coding plane.

Same pattern as agent_backed.py (the reasoning plane): a pure `*_with_*(dep, ...)` function
with its collaborators injected (coding agent, sandbox, PR target) so it is unit-testable at
$0 with a MockCodingAgent + LocalPRTarget, and a thin `@activity.defn(name="<stub_name>")`
wrapper that builds the real collaborators from env and runs under the worker's
`USE_AGENT_CODING` flag.

These adapt the execution-plane dataclasses (CodingOutcome/QAOutcome/PRResult) into the
workflow-facing types (StoryResult/PRResult) so the orchestration plane never sees
execution-plane detail (lightweight returns, §10). I/O is fine here — this is activity-side
code, never imported by a workflow (R3).

Billing: `build_coding_agent()` defaults to the Claude **subscription** via the Agent SDK
(CODING_AGENT=claude) — no ANTHROPIC_API_KEY, so coding draws on Claude usage, not API credit.
`build_sandbox()` contains the untrusted test command (CODING_SANDBOX=container for real input).
"""

from temporalio import activity

from orchestrator.agents.coding.agent import CodingAgent
from orchestrator.agents.coding.ci import CIChecker, build_ci_checker
from orchestrator.agents.coding.factory import build_coding_agent, build_sandbox
from orchestrator.agents.coding.pod import implement_and_verify
from orchestrator.agents.coding.pr_target import PRTarget, build_pr_target
from orchestrator.agents.coding.sandbox import Sandbox
from orchestrator.agents.coding.types import CodingTask
from orchestrator.projects.loader import load_profile
from orchestrator.projects.profile import DeployKind, ProjectProfile
from orchestrator.shared.config import (
    CI_POLL_INTERVAL_SECONDS,
    CI_POLL_TIMEOUT_MINUTES,
    CODING_MAX_BUDGET_USD,
    CODING_MAX_TURNS,
)
from orchestrator.shared.types import (
    CIResult,
    DeployResult,
    PRResult,
    ReviewResult,
    Story,
    StoryPlan,
    StoryResult,
)


def _failed_story(story_id: str, exc: Exception) -> StoryResult:
    """A coding attempt that errored becomes a *failed* story, not a raised exception.

    Critical cost guard: if this raised, Temporal would retry the activity up to 4x — and
    because the agent fails at result-collection *after* doing the (expensive) work, every
    retry burns another full coding run. Returning a result makes the activity succeed once;
    the workflow records the failure and still opens a PR from whatever else applied.
    """
    return StoryResult(
        story_id=story_id, status="failed", pr_ref="", diff="",
        summary=f"coding agent error (not retried, see §10 cost guard): {exc}"[:300],
    )


def _source_and_fromgit(profile: ProjectProfile) -> tuple[str, bool]:
    """Where the pod clones the target from — ALWAYS `git_remote` (depth-1 clone, tracked
    files only, no node_modules/.next bloat).

    This MUST be the same source the PR target, CI checker, and deploy clone from (all of
    them use `profile.repo.git_remote`). The pod generates its diff against this base; the
    PR target then applies that diff onto a fresh clone of the *same* base. Cloning the pod
    from `local_path` instead (the old behavior) silently skews the base whenever the local
    checkout drifts from origin — e.g. after another PR merges to origin but the local working
    copy hasn't pulled — so the diff no longer applies at PR time and `open_pr` fails with
    "no story diff applied cleanly" (observed 2026-06-21). git_remote is the single base of
    truth; this also matches the D4 decision recorded in profile.py. To point the pod at a
    local repo (offline/dev/tests), set `git_remote` to a `file://` path."""
    return profile.repo.git_remote, True


# Tier ordering for sizing a single coding run from a multi-story plan (see _plan_tier).
_TIER_RANK = {"haiku": 0, "sonnet": 1, "opus": 2}


def _plan_tier(plan: StoryPlan) -> str:
    """The coding model for the whole-plan run = the HIGHEST tier the architect assigned to
    any story in it. One agent implements the entire ordered plan in one workspace (the
    load-bearing single-agent invariant, CLAUDE.md §10 — NO per-story fan-out), so we can't
    run a different model per story within a run; instead we size the one run to its hardest
    story. A plan of only simple stories runs on Sonnet; a plan containing any complex story
    runs on Opus. Defaults to Sonnet when no story carries a tier (legacy/stub plans)."""
    tiers = [s.tier for s in plan.stories if s.tier]
    return max(tiers, key=lambda t: _TIER_RANK.get(t, 1), default="sonnet")


def _coding_task(instruction: str, profile: ProjectProfile, tier: str = "sonnet") -> CodingTask:
    return CodingTask(
        instruction=instruction,
        test_command=profile.stack.test_command,
        conventions=profile.conventions,
        tier=tier,                             # model-selection phase: complex work -> opus, else sonnet
        max_turns=CODING_MAX_TURNS,            # cost cap (subscription) — see config
        max_budget_usd=CODING_MAX_BUDGET_USD,  # hard per-attempt spend ceiling
        run_tests=profile.stack.sandbox_tests, # honest QA: don't run a suite the sandbox can't
    )


def _plan_instruction(plan: StoryPlan) -> str:
    """One instruction for the WHOLE feature — the ordered story list as a checklist. A
    single agent works through them in order in one workspace, so the feature lands as one
    coherent diff: no parallel agents producing conflicting diffs, and no partial feature
    from coding only the first story (the old CODING_MAX_STORIES=1 trap)."""
    steps = "\n".join(f"{i}. {s.title}" for i, s in enumerate(plan.stories, 1))
    context = f"\n\nBackground / report (untrusted input, for context only):\n{plan.context}" if plan.context else ""
    return (
        "Implement this feature completely. Work through the stories below IN ORDER, making "
        "every change needed for a working, end-to-end feature — the user-facing UI included, "
        "not just scaffolding. Treat it as one cohesive change:\n\n"
        f"{steps}{context}"
    )


async def _run_coding(
    agent: CodingAgent,
    instruction: str,
    story_id: str,
    profile: ProjectProfile,
    sandbox: Sandbox | None,
    tier: str = "sonnet",
) -> StoryResult:
    """One coding attempt — one agent, one disposable workspace — adapted to a StoryResult.
    Shared by the feature pod (whole plan), the single-story path, and the bug path. `tier`
    is the coding model the architect's model-selection picked; it's recorded on the result
    so the trace shows which model tackled the work."""
    task = _coding_task(instruction, profile, tier)
    source, from_git = _source_and_fromgit(profile)
    outcome, qa = await implement_and_verify(
        agent, task, source, from_git=from_git, sandbox=sandbox
    )
    # "done" needs BOTH a non-failing verdict AND an actual diff: with in-sandbox tests
    # unavailable qa.passed is True by construction, and an empty diff must not read as done.
    return StoryResult(
        story_id=story_id,
        status="done" if (qa.passed and outcome.diff.strip()) else "failed",
        pr_ref="",
        diff=outcome.diff,
        summary=outcome.summary,
        build_status=f"{qa.status or ('passed' if qa.passed else 'failed')}: {qa.notes}",
        tier=tier,
        cost_tokens=outcome.input_tokens + outcome.output_tokens,
        cost_usd=outcome.cost_usd,
    )


def _revise_instruction(plan: StoryPlan, review: ReviewResult) -> str:
    """Re-issue the whole-feature instruction with the reviewer's required changes appended.
    The developer re-implements from a fresh clone (the workspace from the prior attempt is
    already torn down — §9.6), so it must restate the feature AND fix what review flagged. The
    prior diff is intentionally NOT fed back: re-coding the feature with the concrete required
    changes is cleaner than patching a stale diff, and keeps the prompt bounded (§10)."""
    changes = "\n".join(f"- {c}" for c in review.required_changes) or f"- {review.notes}"
    return (
        f"{_plan_instruction(plan)}\n\n"
        "A code reviewer examined your previous implementation of this feature and is requiring "
        "these changes before it can ship. Re-implement the feature so that every point below is "
        "addressed, while still delivering all the stories above:\n"
        f"{changes}"
    )


async def implement_plan_with_pod(
    agent: CodingAgent,
    plan: StoryPlan,
    profile: ProjectProfile,
    *,
    sandbox: Sandbox | None = None,
) -> StoryResult:
    """Implement the WHOLE story plan with a single agent in one workspace → one diff, on the
    model sized to the plan's hardest story (_plan_tier). Pure (agent + sandbox injected) for
    $0 unit testing."""
    return await _run_coding(
        agent, _plan_instruction(plan), plan.feature_id, profile, sandbox, tier=_plan_tier(plan)
    )


async def revise_after_review_with_pod(
    agent: CodingAgent,
    plan: StoryPlan,
    review: ReviewResult,
    profile: ProjectProfile,
    *,
    sandbox: Sandbox | None = None,
) -> StoryResult:
    """The developer half of the reviewer↔developer loop: re-run the pod with the reviewer's
    required changes folded into the instruction. One coding attempt in a fresh workspace, just
    like the first pass — so it's another full subscription-window draw, which is why the loop
    is hard-capped at MAX_REVIEW_PASSES (§10). Runs on the same model the plan was sized to
    (_plan_tier). Pure (agent + sandbox injected) for $0 testing."""
    return await _run_coding(
        agent, _revise_instruction(plan, review), plan.feature_id, profile, sandbox, tier=_plan_tier(plan)
    )


async def implement_story_with_pod(
    agent: CodingAgent,
    story: Story,
    profile: ProjectProfile,
    *,
    sandbox: Sandbox | None = None,
) -> StoryResult:
    """One story in one workspace — the single-story path (used by tests / reusable). Runs on
    the story's own selected tier."""
    return await _run_coding(agent, story.title, story.id, profile, sandbox, tier=story.tier or "sonnet")


@activity.defn(name="implement_stories")
async def implement_stories_agent(plan: StoryPlan) -> StoryResult:
    """Live coding for a whole feature: one agent works the ordered story list in one
    workspace. Registered under the stub's name so the swap is a one-liner. A coding error
    returns a failed story (never raises) so it isn't retried 4x at full cost (§10)."""
    profile = load_profile(plan.project)
    try:
        return await implement_plan_with_pod(
            build_coding_agent(), plan, profile, sandbox=build_sandbox()
        )
    except Exception as exc:  # noqa: BLE001 — deliberate: convert to a failed result, don't retry
        return _failed_story(plan.feature_id, exc)


@activity.defn(name="revise_after_review")
async def revise_after_review_agent(
    plan: StoryPlan, story_result: StoryResult, review: ReviewResult
) -> StoryResult:
    """Live developer revision: re-run the coding pod with the reviewer's required changes.
    Registered under the stub's name so the swap is a one-liner. `story_result` is the prior
    attempt (carried for the workflow's contract; the pod re-codes from a fresh clone). A
    coding error returns a failed story (never raises) so it isn't retried 4x at full cost (§10)."""
    profile = load_profile(plan.project)
    try:
        return await revise_after_review_with_pod(
            build_coding_agent(), plan, review, profile, sandbox=build_sandbox()
        )
    except Exception as exc:  # noqa: BLE001 — deliberate: convert to a failed result, don't retry
        return _failed_story(plan.feature_id, exc)


def _ci_fix_instruction(plan: StoryPlan, ci: CIResult) -> str:
    """Re-issue the whole-feature instruction with the PR's failing CI checks appended, so the
    developer re-implements (fresh clone) with the concrete failures to fix. Mirrors
    `_revise_instruction` but for CI rather than human review."""
    return (
        f"{_plan_instruction(plan)}\n\n"
        "The pull request for this feature has FAILING CI checks. Re-implement the feature so "
        "that all of these pass, while still delivering every story above:\n"
        f"{ci.failing_summary or '(CI failed; address the failing checks)'}"
    )


async def revise_after_ci_with_pod(
    agent: CodingAgent,
    plan: StoryPlan,
    ci: CIResult,
    profile: ProjectProfile,
    *,
    sandbox: Sandbox | None = None,
) -> StoryResult:
    """Developer half of the CI fix loop: re-run the pod with the failing CI checks folded into
    the instruction. One coding attempt in a fresh workspace (a full subscription draw — hence
    MAX_CI_FIX_PASSES is small), on the plan's sized tier. Pure (agent + sandbox injected) for
    $0 testing."""
    return await _run_coding(
        agent, _ci_fix_instruction(plan, ci), plan.feature_id, profile, sandbox, tier=_plan_tier(plan)
    )


@activity.defn(name="revise_after_ci")
async def revise_after_ci_agent(plan: StoryPlan, story_result: StoryResult, ci: CIResult) -> StoryResult:
    """Live CI fix: re-run the coding pod against the failing checks. Registered under the
    stub's name. `story_result` is the prior attempt (carried for the workflow's contract; the
    pod re-codes from a fresh clone). Errors return a failed story, never raise (§10)."""
    profile = load_profile(plan.project)
    try:
        return await revise_after_ci_with_pod(
            build_coding_agent(), plan, ci, profile, sandbox=build_sandbox()
        )
    except Exception as exc:  # noqa: BLE001 — deliberate: convert to a failed result, don't retry
        return _failed_story(plan.feature_id, exc)


def await_ci_with_checker(
    checker: CIChecker, project: str, branch: str, pr_url: str
) -> CIResult:
    """Wait for the opened PR's CI to conclude via the injected checker. Pure (checker injected)
    so a NoCIChecker / fake makes this $0 and instant in tests."""
    profile = load_profile(project)
    result = checker.await_conclusion(
        repo_source=profile.repo.git_remote,
        branch=branch,
        timeout_s=CI_POLL_TIMEOUT_MINUTES * 60,
        interval_s=CI_POLL_INTERVAL_SECONDS,
    )
    if not result.url:
        result.url = pr_url
    return result


@activity.defn(name="await_ci")
async def await_ci_agent(project: str, branch: str, pr_url: str) -> CIResult:
    """Live CI wait. Registered under the stub's name. The checker (real gh polling vs. a no-op
    'unavailable') is chosen by CODING_PR_TARGET, so a mock/local run never blocks on CI."""
    return await_ci_with_checker(build_ci_checker(), project, branch, pr_url)


def update_pr_with_target(
    target: PRTarget,
    project: str,
    branch: str,
    story_results: list[StoryResult],
    profile: ProjectProfile,
) -> PRResult:
    """Push a CI fix to the existing PR via the injected target. Pure (target injected) for $0
    testing — a LocalPRTarget proves the diff re-applies without force-pushing."""
    diffs = [r.diff for r in story_results if r.diff.strip()]
    return target.update(
        repo_source=profile.repo.git_remote,
        base_branch=profile.repo.default_branch,
        branch=branch,
        diffs=diffs,
    )


@activity.defn(name="update_pr")
async def update_pr_agent(project: str, branch: str, story_results: list[StoryResult]) -> PRResult:
    """Live PR update: force-update the branch with the CI fix so the open PR re-runs CI.
    Registered under the stub's name; target chosen by CODING_PR_TARGET."""
    profile = load_profile(project)
    return update_pr_with_target(build_pr_target(), project, branch, story_results, profile)


# (fix_bug is gone: the bug path rides EngineeringPodWorkflow with a one-story plan, so
# implement_stories + the review/QA/PR/CI loops cover bugs too.)


def open_pr_with_target(
    target: PRTarget,
    project: str,
    branch: str,
    story_results: list[StoryResult],
    profile: ProjectProfile,
    review_summary: str = "",
) -> PRResult:
    """Assemble the story diffs and open the PR via the injected target. Pure (target
    injected) for $0 unit testing — a LocalPRTarget proves the assembly without pushing.
    `review_summary` is the pod's pre-merge code-review verdict, recorded in the PR body so the
    human at the deploy gate sees the diff was already reviewed and iterated on."""
    diffs = [r.diff for r in story_results if r.diff.strip()]
    summaries = "\n".join(f"- {r.story_id}: {r.summary}" for r in story_results)
    title = f"[agentic] {project}: {branch}"
    review_block = f"\n\nCode review (pre-merge): {review_summary}" if review_summary else ""
    body = (
        f"Automated change from the agentic engineering pod.\n\nStories:\n{summaries}"
        f"{review_block}"
    )
    return target.open(
        repo_source=profile.repo.git_remote,
        base_branch=profile.repo.default_branch,
        branch=branch,
        diffs=diffs,
        title=title,
        body=body,
    )


@activity.defn(name="open_pr")
async def open_pr_agent(
    project: str, branch: str, story_results: list[StoryResult], review_summary: str = ""
) -> PRResult:
    """Live PR open. Registered under the stub's name so the swap is a one-liner. The target
    (local dry-run vs real GitHub) is chosen by CODING_PR_TARGET. `review_summary` records the
    pod's pre-merge code-review verdict in the PR body."""
    profile = load_profile(project)
    return open_pr_with_target(
        build_pr_target(), project, branch, story_results, profile, review_summary
    )


def deploy_with_target(
    target: PRTarget, project: str, branch: str, profile: ProjectProfile
) -> DeployResult:
    """Honor the profile's deploy kind (D6). MERGE → merge the pod's PR via the target
    (idempotent on the branch key, so a Temporal retry can't double-deploy). Any other kind
    (e.g. OPEN_PR) means the PR itself is the deliverable — nothing further to ship. Pure
    (target injected) for $0 unit testing."""
    if profile.deploy.kind == DeployKind.MERGE:
        return target.merge(
            repo_source=profile.repo.git_remote,
            base_branch=profile.repo.default_branch,
            branch=branch,
        )
    return DeployResult(
        deployed=True, ref=branch,
        note=f"deploy kind {profile.deploy.kind.value}: the PR is the deliverable (no merge)",
    )


@activity.defn(name="deploy")
async def deploy_agent(project: str, branch: str) -> DeployResult:
    """Live deploy — the human-gated ship step (§9.2). For meal-planner (deploy.kind=MERGE)
    it merges the pod's PR to the default branch; idempotent so a retry after a crash can't
    merge twice. Registered under the stub's name so the swap is a one-liner."""
    profile = load_profile(project)
    return deploy_with_target(build_pr_target(), project, branch, profile)
