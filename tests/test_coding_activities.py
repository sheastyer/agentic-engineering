"""M4 wiring: the agent-backed engineering-pod activities, proven at $0.

Mirrors test_coding_pod.py but exercises the *activity-layer* pure functions the workflow
calls — implement_story_with_pod (agent + sandbox injected) and open_pr_with_target (PR
target injected). A MockCodingAgent + LocalPRTarget keep these token-free, auth-free, and
free of any external side effect (no push, no gh, no docker).

The fixture is a real on-disk git repo (the agent path git-clones its source, the way it
clones the meal-planner profile's local_path), seeded with the same broken `add` the
execution-plane tests use, so a correct edit turns the target's own tests green.
"""

import os
import subprocess
import sys

import pytest

from orchestrator.activities.coding_backed import (
    implement_story_with_pod,
    open_pr_with_target,
)
from orchestrator.agents.coding.agents.mock import MockCodingAgent
from orchestrator.agents.coding.pr_target import LocalPRTarget
from orchestrator.agents.coding.types import FileEdit
from orchestrator.projects.profile import (
    Deploy,
    DeployKind,
    Intake,
    IntakeKind,
    ProjectProfile,
    Repo,
    Stack,
)
from orchestrator.shared.types import DeployResult, PRResult, Story, StoryResult

TEST_COMMAND = f"{sys.executable} -m pytest -q verify.py"
FIX = FileEdit(path="mathlib.py", find="return a - b", replace="return a + b")

_MATHLIB = """def add(a, b):
    return a - b  # seeded bug
"""
_VERIFY = """from mathlib import add


def test_add():
    assert add(2, 3) == 5
"""


def _git(args: str, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        f"git -c user.email=t@t -c user.name=t {args}",
        cwd=cwd, shell=True, capture_output=True, text=True,
    )


def _seeded_git_repo(tmp_path) -> str:
    """A committed git repo with the seeded bug — stands in for a profile's local_path/remote."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mathlib.py").write_text(_MATHLIB)
    (repo / "verify.py").write_text(_VERIFY)
    _git("init -q", str(repo))
    _git("add -A", str(repo))
    _git("commit -q -m baseline", str(repo))
    return str(repo)


def _profile(
    local_path: str = "",
    git_remote: str = "file:///unused",
    deploy_kind: DeployKind = DeployKind.OPEN_PR,
) -> ProjectProfile:
    return ProjectProfile(
        id="fixture",
        name="Fixture",
        description="seeded test target",
        repo=Repo(git_remote=git_remote, default_branch="main", local_path=local_path),
        stack=Stack(languages=["python"], package_manager="pip", test_command=TEST_COMMAND),
        intake=Intake(kind=IntakeKind.MANUAL),
        deploy=Deploy(kind=deploy_kind),
    )


class _FakeRemote:
    """In-memory model of the PR remote, mirroring GitHubPRTarget's check-before-act
    idempotency: open and merge are keyed on the *branch* and each fires at most once for a
    given branch, so a re-invocation (a Temporal retry after a crash) is a no-op."""

    name = "fake"

    def __init__(self) -> None:
        self.prs: dict[str, str] = {}     # branch -> url
        self.merged: set[str] = set()
        self.creates = 0
        self.merges = 0

    def open(self, *, repo_source, base_branch, branch, diffs, title, body) -> PRResult:
        if branch in self.prs:
            return PRResult(opened=True, url=self.prs[branch], branch=branch, note="existing (idempotent)")
        self.creates += 1
        url = f"https://fake/pr/{branch}"
        self.prs[branch] = url
        return PRResult(opened=True, url=url, branch=branch, note="opened")

    def merge(self, *, repo_source, base_branch, branch) -> DeployResult:
        if branch in self.merged:
            return DeployResult(deployed=True, ref=branch, note="already merged (idempotent)")
        self.merges += 1
        self.merged.add(branch)
        return DeployResult(deployed=True, ref=branch, note="merged")


async def test_implement_story_done_when_fix_makes_tests_pass(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    agent = MockCodingAgent(edits=[FIX])
    result = await implement_story_with_pod(
        agent, Story(id="S1", title="Fix add()", estimate=1), _profile(local_path=repo)
    )
    assert result.status == "done", result.summary
    assert result.diff.strip() and "return a + b" in result.diff


async def test_implement_story_failed_when_noop_attempt_leaves_bug(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    agent = MockCodingAgent(edits=[])  # changes nothing -> QA must catch it
    result = await implement_story_with_pod(
        agent, Story(id="S1", title="Fix add()", estimate=1), _profile(local_path=repo)
    )
    assert result.status == "failed"


def test_open_pr_applies_diff_locally_without_pushing(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    # Produce a real diff (the fix) against the committed baseline.
    (tmp_path / "repo" / "mathlib.py").write_text(_MATHLIB.replace("return a - b", "return a + b"))
    diff = _git("diff", repo).stdout
    _git("checkout -- mathlib.py", repo)  # restore: the clone source stays at the buggy baseline
    assert "return a + b" in diff

    profile = _profile(git_remote=repo)  # LocalPRTarget clones repo_source = git_remote
    pr = open_pr_with_target(
        LocalPRTarget(), "fixture", "agentic/fix",
        [StoryResult(story_id="S1", status="done", pr_ref="", diff=diff, summary="fix add")],
        profile,
    )
    assert pr.opened and pr.branch == "agentic/fix"
    checkout = pr.url[len("file://"):].split("#")[0]
    assert "return a + b" in (open(os.path.join(checkout, "mathlib.py")).read())


def test_open_pr_not_opened_when_no_diffs(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    pr = open_pr_with_target(
        LocalPRTarget(), "fixture", "agentic/empty",
        [StoryResult(story_id="S1", status="failed", pr_ref="", diff="", summary="nothing")],
        _profile(git_remote=repo),
    )
    assert pr.opened is False


def test_open_pr_idempotent_on_branch_key():
    """M4 idempotency (§9.7): re-running open_pr with the same branch key opens at most one PR
    (a Temporal retry after a crash must not create a duplicate)."""
    fake = _FakeRemote()
    profile = _profile()
    results = [StoryResult(story_id="S1", status="done", pr_ref="", diff="patch", summary="s")]
    first = open_pr_with_target(fake, "fixture", "agentic/feat-1", results, profile)
    second = open_pr_with_target(fake, "fixture", "agentic/feat-1", results, profile)
    assert fake.creates == 1            # opened once, not twice
    assert first.url == second.url      # the retry returns the existing PR


def test_deploy_merge_idempotent_on_branch_key():
    """M4 idempotency: a MERGE-kind deploy merges the branch at most once across retries."""
    from orchestrator.activities.coding_backed import deploy_with_target

    fake = _FakeRemote()
    profile = _profile(deploy_kind=DeployKind.MERGE)
    a = deploy_with_target(fake, "fixture", "agentic/feat-1", profile)
    b = deploy_with_target(fake, "fixture", "agentic/feat-1", profile)
    assert fake.merges == 1             # merged once, not twice
    assert a.deployed and b.deployed


def test_deploy_open_pr_kind_does_not_merge():
    """A non-MERGE deploy kind (the PR is the deliverable) ships without touching the remote."""
    from orchestrator.activities.coding_backed import deploy_with_target

    fake = _FakeRemote()
    res = deploy_with_target(fake, "fixture", "agentic/feat-1", _profile(deploy_kind=DeployKind.OPEN_PR))
    assert res.deployed and fake.merges == 0


def test_local_pr_target_merge_is_a_dry_run():
    """The default (off-by-default-real) target merges as a dry run — deploy is reachable at $0."""
    res = LocalPRTarget().merge(repo_source="file:///unused", base_branch="main", branch="agentic/x")
    assert res.deployed and "dry-run" in res.note


async def test_implement_plan_codes_whole_feature_in_one_pass(tmp_path):
    """The pod implements the WHOLE plan with one agent in one workspace — every story's
    instruction reaches the agent (not just story #1), and it lands as one diff."""
    from orchestrator.activities.coding_backed import _plan_instruction, implement_plan_with_pod
    from orchestrator.shared.types import StoryPlan

    repo = _seeded_git_repo(tmp_path)
    plan = StoryPlan(
        feature_id="feat-x", project="fixture",
        stories=[
            Story(id="S1", title="Fix the add() helper", estimate=1),
            Story(id="S2", title="Wire it into the UI", estimate=2),
        ],
    )
    # Both story titles must reach the agent as one ordered instruction.
    instr = _plan_instruction(plan)
    assert "Fix the add() helper" in instr and "Wire it into the UI" in instr

    result = await implement_plan_with_pod(MockCodingAgent(edits=[FIX]), plan, _profile(local_path=repo))
    assert result.status == "done", result.summary
    assert result.story_id == "feat-x" and "return a + b" in result.diff


async def test_coding_error_becomes_failed_story_not_a_retry(monkeypatch):
    """Cost guard (§10): a coding error must return a *failed* StoryResult, never raise —
    otherwise Temporal retries the activity 4x, each retry burning a full coding run."""
    import orchestrator.activities.coding_backed as cb
    from orchestrator.shared.types import StoryPlan

    async def boom(*a, **k):
        raise RuntimeError("simulated agent crash at result-collection")

    monkeypatch.setattr(cb, "implement_plan_with_pod", boom)
    plan = StoryPlan(feature_id="feat-x", stories=[Story(id="S1", title="x", estimate=1)], project="meal-planner")
    result = await cb.implement_stories_agent(plan)
    assert result.status == "failed"
    assert "simulated agent crash" in result.summary


@pytest.mark.asyncio
async def test_pod_review_loop_revises_before_opening_pr():
    """The reviewer↔developer loop runs BEFORE the PR opens: a reviewer that rejects once then
    approves must drive exactly one developer revision, and the PR is opened from the revised
    (approved) diff with the review verdict recorded on the PodResult ($0 — stub overrides)."""
    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from orchestrator.shared.config import TASK_QUEUE
    from orchestrator.shared.types import PRResult, ReviewResult, StoryPlan
    from orchestrator.workflows.engineering_pod import EngineeringPodWorkflow
    from tests.helpers import TEMPORAL_CLI, activities_with

    calls = {"review": 0, "revise": 0, "pr_review_summary": None}

    @activity.defn(name="review_diff")
    async def review_reject_then_approve(plan: StoryPlan, story_result: StoryResult) -> ReviewResult:
        calls["review"] += 1
        if calls["review"] == 1:
            return ReviewResult(approved=False, notes="needs work",
                                required_changes=["persist the toggle"], cost_tokens=1)
        return ReviewResult(approved=True, notes="LGTM after revision", cost_tokens=1)

    @activity.defn(name="revise_after_review")
    async def revise(plan: StoryPlan, story_result: StoryResult, review: ReviewResult) -> StoryResult:
        calls["revise"] += 1
        assert review.required_changes == ["persist the toggle"]  # the feedback reaches the developer
        return StoryResult(story_id=plan.feature_id, status="done", pr_ref="",
                           diff="revised diff", summary="addressed review", cost_tokens=1)

    @activity.defn(name="open_pr")
    async def capture_pr(project: str, branch: str, story_results, review_summary: str = "") -> PRResult:
        calls["pr_review_summary"] = review_summary  # the final verdict lands in the PR body
        return PRResult(opened=True, url=f"local://pr/{branch}", branch=branch, cost_tokens=1)

    plan = StoryPlan(
        feature_id="feat-x", project="meal-planner",
        stories=[Story(id="S1", title="Add dark-mode toggle", estimate=2)],
    )
    overrides = {"review_diff": review_reject_then_approve,
                 "revise_after_review": revise, "open_pr": capture_pr}

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE,
            workflows=[EngineeringPodWorkflow], activities=activities_with(overrides),
        ):
            pod = await env.client.execute_workflow(
                EngineeringPodWorkflow.run, plan, id="pod-review-loop-test", task_queue=TASK_QUEUE
            )

    assert calls["revise"] == 1                       # exactly one revision (capped loop)
    assert calls["review"] == 2                       # reviewed the original, then the revision
    assert pod.review_approved is True                # PR opened only after approval
    assert pod.story_result.diff == "revised diff"    # PR carries the revised, reviewed diff
    assert calls["pr_review_summary"] == "LGTM after revision"


def test_no_ci_checker_is_unavailable_and_update_reapplies_locally(tmp_path):
    """The default CI checker (no real CI) reports 'unavailable' (passed) so $0 runs never block,
    and update_pr re-applies the diff via the LocalPRTarget without pushing."""
    from orchestrator.activities.coding_backed import update_pr_with_target
    from orchestrator.agents.coding.ci import NoCIChecker

    verdict = NoCIChecker().await_conclusion(repo_source="x", branch="b", timeout_s=1, interval_s=1)
    assert verdict.status == "unavailable" and verdict.passed is True

    repo = _seeded_git_repo(tmp_path)
    (tmp_path / "repo" / "mathlib.py").write_text(_MATHLIB.replace("return a - b", "return a + b"))
    diff = _git("diff", repo).stdout
    _git("checkout -- mathlib.py", repo)
    pr = update_pr_with_target(
        LocalPRTarget(), "fixture", "agentic/fix",
        [StoryResult(story_id="S1", status="done", pr_ref="", diff=diff, summary="ci fix")],
        _profile(git_remote=repo),
    )
    assert pr.opened and "return a + b" in open(os.path.join(pr.url[len("file://"):].split("#")[0], "mathlib.py")).read()


async def _run_pod_with_ci(overrides, wf_id):
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from orchestrator.shared.config import TASK_QUEUE
    from orchestrator.shared.types import StoryPlan
    from orchestrator.workflows.engineering_pod import EngineeringPodWorkflow
    from tests.helpers import TEMPORAL_CLI, activities_with

    plan = StoryPlan(feature_id="feat-x", project="meal-planner",
                     stories=[Story(id="S1", title="Add feedback button", estimate=2)])
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(env.client, task_queue=TASK_QUEUE,
                          workflows=[EngineeringPodWorkflow], activities=activities_with(overrides)):
            return await env.client.execute_workflow(
                EngineeringPodWorkflow.run, plan, id=wf_id, task_queue=TASK_QUEUE
            )


@pytest.mark.asyncio
async def test_pod_ci_gate_fixes_red_ci_then_proceeds():
    """The CI gate (after open_pr): a PR whose CI fails once then passes must drive exactly one
    developer CI-fix + PR update, and the pod proceeds with ci_passed=True from the revised diff."""
    from temporalio import activity

    from orchestrator.shared.types import CIResult, PRResult, StoryPlan

    calls = {"ci": 0, "revise": 0, "update": 0}

    @activity.defn(name="await_ci")
    async def ci_fail_then_pass(project: str, branch: str, pr_url: str) -> CIResult:
        calls["ci"] += 1
        if calls["ci"] == 1:
            return CIResult(status="failed", passed=False, failing_summary="E2E axe contrast 1.06:1", url=pr_url)
        return CIResult(status="passed", passed=True, url=pr_url)

    @activity.defn(name="revise_after_ci")
    async def revise_ci(plan: StoryPlan, story_result: StoryResult, ci: CIResult) -> StoryResult:
        calls["revise"] += 1
        assert "contrast" in ci.failing_summary  # the failing checks reach the developer
        return StoryResult(story_id=plan.feature_id, status="done", pr_ref="", diff="ci-fixed diff", summary="fixed CI")

    @activity.defn(name="update_pr")
    async def update(project: str, branch: str, story_results) -> PRResult:
        calls["update"] += 1
        return PRResult(opened=True, url=f"local://pr/{branch}", branch=branch)

    pod = await _run_pod_with_ci(
        {"await_ci": ci_fail_then_pass, "revise_after_ci": revise_ci, "update_pr": update},
        "pod-ci-fix-test",
    )
    assert calls["revise"] == 1 and calls["update"] == 1  # exactly one bounded fix pass
    assert calls["ci"] == 2                                # checked, fixed, re-checked
    assert pod.ci_passed is True
    assert pod.story_result.diff == "ci-fixed diff"        # PR carries the CI-fixed diff


@pytest.mark.asyncio
async def test_pod_ci_gate_gives_up_after_cap_with_ci_not_passed():
    """If CI stays red past MAX_CI_FIX_PASSES, the pod returns ci_passed=False (the parent then
    halts before merging) — it never reports green for a red PR."""
    from temporalio import activity

    from orchestrator.shared.types import CIResult, PRResult, StoryPlan

    @activity.defn(name="await_ci")
    async def ci_always_fail(project: str, branch: str, pr_url: str) -> CIResult:
        return CIResult(status="failed", passed=False, failing_summary="still failing", url=pr_url)

    @activity.defn(name="revise_after_ci")
    async def revise_ci(plan: StoryPlan, story_result: StoryResult, ci: CIResult) -> StoryResult:
        return StoryResult(story_id=plan.feature_id, status="done", pr_ref="", diff="attempted fix", summary="tried")

    @activity.defn(name="update_pr")
    async def update(project: str, branch: str, story_results) -> PRResult:
        return PRResult(opened=True, url=f"local://pr/{branch}", branch=branch)

    pod = await _run_pod_with_ci(
        {"await_ci": ci_always_fail, "revise_after_ci": revise_ci, "update_pr": update},
        "pod-ci-giveup-test",
    )
    assert pod.ci_passed is False
    assert pod.ci_notes == "still failing"


@pytest.mark.asyncio
async def test_pod_runs_one_agent_for_the_whole_plan():
    """The pod is a single coding pass over the ordered plan (no per-story fan-out): one
    story_result keyed by the feature id, and a PR opened from it."""
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from orchestrator.shared.config import TASK_QUEUE
    from orchestrator.shared.types import StoryPlan
    from orchestrator.workflows.engineering_pod import EngineeringPodWorkflow
    from tests.helpers import TEMPORAL_CLI, activities_with

    stories = [Story(id=f"S{i}", title=f"slice {i}", estimate=1) for i in range(1, 4)]
    plan = StoryPlan(feature_id="feat-x", stories=stories, project="meal-planner")

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE,
            workflows=[EngineeringPodWorkflow], activities=activities_with(),
        ):
            pod = await env.client.execute_workflow(
                EngineeringPodWorkflow.run, plan, id="pod-single-agent-test", task_queue=TASK_QUEUE
            )

    assert pod.story_result.story_id == "feat-x"
