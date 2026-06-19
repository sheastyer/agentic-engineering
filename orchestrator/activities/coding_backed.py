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

import os

from temporalio import activity

from orchestrator.agents.coding.agent import CodingAgent
from orchestrator.agents.coding.factory import build_coding_agent, build_sandbox
from orchestrator.agents.coding.pod import implement_and_verify
from orchestrator.agents.coding.pr_target import PRTarget, build_pr_target
from orchestrator.agents.coding.sandbox import Sandbox
from orchestrator.agents.coding.types import CodingTask
from orchestrator.projects.loader import load_profile
from orchestrator.projects.profile import ProjectProfile
from orchestrator.shared.config import CODING_MAX_BUDGET_USD, CODING_MAX_TURNS
from orchestrator.shared.types import FeedbackEvent, PRResult, Story, StoryResult


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
    """Where the pod clones the target from. Always a git clone (depth 1) so only tracked
    files come along — no node_modules/.next bloat. Prefer a local checkout if the profile
    has one (fast), else the git remote."""
    if profile.repo.local_path:
        return os.path.expanduser(profile.repo.local_path), True
    return profile.repo.git_remote, True


def _coding_task(instruction: str, profile: ProjectProfile) -> CodingTask:
    return CodingTask(
        instruction=instruction,
        test_command=profile.stack.test_command,
        conventions=profile.conventions,
        tier="sonnet",
        max_turns=CODING_MAX_TURNS,            # cost cap (subscription) — see config
        max_budget_usd=CODING_MAX_BUDGET_USD,  # hard per-attempt spend ceiling
    )


async def implement_story_with_pod(
    agent: CodingAgent,
    story: Story,
    profile: ProjectProfile,
    *,
    sandbox: Sandbox | None = None,
) -> StoryResult:
    """Run one story through the coding pod (implement -> verify) in a disposable workspace.
    Pure (agent + sandbox injected) for $0 unit testing. QA verdict maps to status; the diff
    is carried up so the PR-open step can assemble it."""
    task = _coding_task(story.title, profile)
    source, from_git = _source_and_fromgit(profile)
    outcome, qa = await implement_and_verify(
        agent, task, source, from_git=from_git, sandbox=sandbox
    )
    return StoryResult(
        story_id=story.id,
        status="done" if qa.passed else "failed",
        pr_ref="",
        diff=outcome.diff,
        summary=outcome.summary,
        cost_tokens=outcome.input_tokens + outcome.output_tokens,
        cost_usd=outcome.cost_usd,
    )


@activity.defn(name="implement_story")
async def implement_story_agent(story: Story, project: str) -> StoryResult:
    """Live coding run. Registered under the stub's name so the swap is a one-liner. A coding
    error returns a failed story (never raises) so it isn't retried 4x at full cost (§10)."""
    profile = load_profile(project)
    try:
        return await implement_story_with_pod(
            build_coding_agent(), story, profile, sandbox=build_sandbox()
        )
    except Exception as exc:  # noqa: BLE001 — deliberate: convert to a failed result, don't retry
        return _failed_story(story.id, exc)


async def fix_bug_with_pod(
    agent: CodingAgent,
    event: FeedbackEvent,
    profile: ProjectProfile,
    *,
    sandbox: Sandbox | None = None,
) -> StoryResult:
    """Bug-path twin of implement_story_with_pod: one coding attempt against the bug report."""
    task = _coding_task(f"{event.title}\n\n{event.body}", profile)
    source, from_git = _source_and_fromgit(profile)
    outcome, qa = await implement_and_verify(
        agent, task, source, from_git=from_git, sandbox=sandbox
    )
    return StoryResult(
        story_id=f"bugfix-{event.id}",
        status="done" if qa.passed else "failed",
        pr_ref="",
        diff=outcome.diff,
        summary=outcome.summary,
        cost_tokens=outcome.input_tokens + outcome.output_tokens,
        cost_usd=outcome.cost_usd,
    )


@activity.defn(name="fix_bug")
async def fix_bug_agent(event: FeedbackEvent) -> StoryResult:
    """Live bug fix. Registered under the stub's name so the swap is a one-liner. A coding
    error returns a failed story (never raises) so it isn't retried 4x at full cost (§10)."""
    profile = load_profile(event.project)
    try:
        return await fix_bug_with_pod(build_coding_agent(), event, profile, sandbox=build_sandbox())
    except Exception as exc:  # noqa: BLE001 — deliberate: convert to a failed result, don't retry
        return _failed_story(f"bugfix-{event.id}", exc)


def open_pr_with_target(
    target: PRTarget,
    project: str,
    branch: str,
    story_results: list[StoryResult],
    profile: ProjectProfile,
) -> PRResult:
    """Assemble the story diffs and open the PR via the injected target. Pure (target
    injected) for $0 unit testing — a LocalPRTarget proves the assembly without pushing."""
    diffs = [r.diff for r in story_results if r.diff.strip()]
    summaries = "\n".join(f"- {r.story_id}: {r.summary}" for r in story_results)
    title = f"[agentic] {project}: {branch}"
    body = f"Automated change from the agentic engineering pod.\n\nStories:\n{summaries}"
    return target.open(
        repo_source=profile.repo.git_remote,
        base_branch=profile.repo.default_branch,
        branch=branch,
        diffs=diffs,
        title=title,
        body=body,
    )


@activity.defn(name="open_pr")
async def open_pr_agent(project: str, branch: str, story_results: list[StoryResult]) -> PRResult:
    """Live PR open. Registered under the stub's name so the swap is a one-liner. The target
    (local dry-run vs real GitHub) is chosen by CODING_PR_TARGET."""
    profile = load_profile(project)
    return open_pr_with_target(build_pr_target(), project, branch, story_results, profile)
