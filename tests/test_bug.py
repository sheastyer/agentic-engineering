"""BugWorkflow: the shorter triage -> fix -> gated-deploy path."""

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.types import Status
from orchestrator.workflows.bug import BugWorkflow
from tests import mock_activities as mock
from tests.helpers import ALL_WORKFLOWS, TEMPORAL_CLI, activities_with, bug_event, wait_until


@pytest.mark.asyncio
async def test_bug_ships_through_gated_deploy():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS, activities=activities_with()
        ):
            event = bug_event()
            handle = await env.client.start_workflow(
                BugWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await wait_until(handle, lambda s: s.stage == "deploy_approval", BugWorkflow.get_state)
            await handle.signal(BugWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    assert result.status == Status.SHIPPED
    # The fix rides the engineering pod (one pod, two entry points) and ships a real ref.
    assert "engineering_pod" in result.stage_log
    assert "pr" in result.summary or "branch" in result.summary or "agentic/" in result.summary


@pytest.mark.asyncio
async def test_bug_clarification_signal_unblocks():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        activities = activities_with({"triage_feedback": mock.triage_needs_clarification})
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS, activities=activities
        ):
            event = bug_event()
            handle = await env.client.start_workflow(
                BugWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await wait_until(
                handle, lambda s: s.stage == "await_clarification", BugWorkflow.get_state
            )
            await handle.signal(BugWorkflow.submit_user_clarification, "it repros on Safari")
            await wait_until(handle, lambda s: s.stage == "deploy_approval", BugWorkflow.get_state)
            await handle.signal(BugWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert "await_clarification" in result.stage_log


@pytest.mark.asyncio
async def test_bug_qa_failure_halts_before_deploy():
    # The bug path rides the pod, so it inherits the same hard QA gate as features.
    from temporalio import activity

    from orchestrator.shared.types import QAResult

    @activity.defn(name="qa_review")
    async def qa_always_fail(project: str, story_results: list) -> QAResult:
        return QAResult(passed=False, notes="(test) fix doesn't hold together")

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with({"qa_review": qa_always_fail}),
        ):
            event = bug_event()
            handle = await env.client.start_workflow(
                BugWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            result = await handle.result()

    assert result.status == Status.QA_FAILED
    assert "qa_failed" in result.stage_log
    assert "deploy" not in result.stage_log
    assert "deploy_approval" not in result.stage_log


@pytest.mark.asyncio
async def test_bug_duplicate_closes_early():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        activities = activities_with({"dedupe_check": mock.dedupe_is_duplicate})
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS, activities=activities
        ):
            event = bug_event()
            handle = await env.client.start_workflow(
                BugWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            result = await handle.result()

    assert result.status == Status.CLOSED_DUPLICATE
    assert "engineering_pod" not in result.stage_log
