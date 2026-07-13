"""FeatureRequestWorkflow behavior on stubs — happy path and the key branches.

Uses a real local dev server (instant stub activities, real wall-clock) so the day-long
sign-off/deploy timeouts never fire mid-test and signal ordering is deterministic.
"""

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.shared.config import MAX_PRD_PASSES, TASK_QUEUE
from orchestrator.shared.types import Status
from orchestrator.workflows.feature_request import FeatureRequestWorkflow
from tests import mock_activities as mock
from tests.helpers import ALL_WORKFLOWS, TEMPORAL_CLI, activities_with, feature_event, wait_until

GET_STATE = FeatureRequestWorkflow.get_state


async def _start(env, activities):
    worker = Worker(
        env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS, activities=activities
    )
    return worker


@pytest.mark.asyncio
async def test_happy_path_ships():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with await _start(env, activities_with()):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            # Human plays every gate, in order.
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)

            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert result.cost_tokens > 0
    # Full control flow was exercised.
    for expected in ("pm_draft_brief", "exec_council", "consumer_research", "engineering_pod", "deploy"):
        assert expected in result.stage_log


@pytest.mark.asyncio
async def test_ci_failure_halts_before_deploy():
    # The org must not progress past code review to merge while the PR's CI is red. The pod's
    # bounded CI fix loop can't make it pass (CI always fails) -> the workflow halts at CI_FAILED
    # and never reaches the deploy gate or merges.
    from temporalio import activity

    from orchestrator.shared.types import CIResult

    @activity.defn(name="await_ci")
    async def ci_always_fail(project: str, branch: str, pr_url: str) -> CIResult:
        return CIResult(status="failed", passed=False, failing_summary="E2E: contrast 1.06:1", url=pr_url)

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with await _start(env, activities_with({"await_ci": ci_always_fail})):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            result = await handle.result()

    assert result.status == Status.CI_FAILED
    assert "engineering_pod" in result.stage_log
    assert "ci_failed" in result.stage_log
    assert "deploy" not in result.stage_log          # never merged a red PR
    assert "deploy_approval" not in result.stage_log  # halted before the deploy gate


@pytest.mark.asyncio
async def test_qa_failure_halts_before_deploy():
    # QA is a hard gate, symmetric with CI (2026-07-02): if the QA agent's final verdict on
    # the pod's output is a fail (after the bounded QA→fix loop), the workflow halts at
    # QA_FAILED and never reaches the deploy gate.
    from temporalio import activity

    from orchestrator.shared.types import QAResult

    @activity.defn(name="qa_review")
    async def qa_always_fail(project: str, story_results: list) -> QAResult:
        return QAResult(passed=False, notes="(test) diff doesn't substantiate the claimed work")

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with await _start(env, activities_with({"qa_review": qa_always_fail})):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            result = await handle.result()

    assert result.status == Status.QA_FAILED
    assert "qa_failed" in result.stage_log
    assert "deploy" not in result.stage_log           # never merged past a red QA
    assert "deploy_approval" not in result.stage_log  # halted before the deploy gate


@pytest.mark.asyncio
async def test_pr_not_opened_skips_ci_gate_and_halts():
    # Regression (2026-06-21): when open_pr fails (e.g. the diff didn't apply against the remote
    # base), there is no PR to wait on or fix. The pod must NOT call await_ci (which would poll a
    # non-existent PR until timeout) or revise_after_ci (a full, wasted coding run) — it must
    # short-circuit to CI_FAILED so the parent halts before deploy.
    from temporalio import activity

    from orchestrator.shared.types import CIResult, PRResult, StoryResult

    called: list[str] = []

    @activity.defn(name="open_pr")
    async def open_pr_fails(project, branch, story_results, review_summary=""):
        return PRResult(opened=False, branch=branch, note="no story diff applied cleanly")

    @activity.defn(name="await_ci")
    async def await_ci_spy(project: str, branch: str, pr_url: str) -> CIResult:
        called.append("await_ci")
        return CIResult(status="passed", passed=True)

    @activity.defn(name="revise_after_ci")
    async def revise_spy(plan, result, ci) -> StoryResult:
        called.append("revise_after_ci")
        return result

    overrides = {"open_pr": open_pr_fails, "await_ci": await_ci_spy, "revise_after_ci": revise_spy}
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with await _start(env, activities_with(overrides)):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            result = await handle.result()

    assert result.status == Status.CI_FAILED
    assert called == []                       # CI gate fully skipped — no wasted wait or coding revise
    assert "deploy" not in result.stage_log   # never merged


@pytest.mark.asyncio
async def test_human_veto_rejects_despite_agent_approval():
    # Governance: the human vote is decisive. Agents approve (stub), human says NO ->
    # the feature is rejected and short-circuits before implementation.
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with await _start(env, activities_with()):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[False, "tester"])
            result = await handle.result()

    assert result.status == Status.REJECTED_BY_COUNCIL
    assert "engineering_pod" not in result.stage_log


@pytest.mark.asyncio
async def test_human_overrides_agent_dissent():
    # Every agent votes NO; the human YES is decisive and the feature proceeds to ship.
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        activities = activities_with({"council_agent_vote": mock.council_vote_reject})
        async with await _start(env, activities):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert any("human override by tester -> approved" in line for line in result.stage_log)


@pytest.mark.asyncio
async def test_deploy_declined_holds():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with await _start(env, activities_with()):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, False)
            result = await handle.result()

    assert result.status == Status.HELD
    assert "deploy" not in result.stage_log  # deploy activity never ran


@pytest.mark.asyncio
async def test_prd_loop_respects_cap():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        activities = activities_with({"architect_review_prd": mock.architect_review_always_reject})
        async with await _start(env, activities):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    # v1 + one revise per failed pass = MAX_PRD_PASSES revisions.
    assert any("hit cap" in line for line in result.stage_log)
    # The workflow still ships (proceeds with best-effort PRD), proving the loop is bounded.
    assert result.status == Status.SHIPPED


@pytest.mark.asyncio
async def test_pm_revise_loops_back_then_approves():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with await _start(env, activities_with()):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])

            # First sign-off: request a revision.
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            v1 = (await handle.query(GET_STATE)).prd_version
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "revise")

            # Workflow loops back through PRD revision + research, then re-enters sign-off
            # with a bumped PRD version. Now approve.
            await wait_until(
                handle, lambda s: s.stage == "pm_signoff" and s.prd_version > v1, GET_STATE
            )
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert result.stage_log.count("consumer_research") >= 2  # ran again after the revise
