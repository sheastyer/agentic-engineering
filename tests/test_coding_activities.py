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
from orchestrator.shared.types import Story, StoryResult

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


def _profile(local_path: str = "", git_remote: str = "file:///unused") -> ProjectProfile:
    return ProjectProfile(
        id="fixture",
        name="Fixture",
        description="seeded test target",
        repo=Repo(git_remote=git_remote, default_branch="main", local_path=local_path),
        stack=Stack(languages=["python"], package_manager="pip", test_command=TEST_COMMAND),
        intake=Intake(kind=IntakeKind.MANUAL),
        deploy=Deploy(kind=DeployKind.OPEN_PR),
    )


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

    assert len(pod.story_results) == 1
    assert pod.story_results[0].story_id == "feat-x"
