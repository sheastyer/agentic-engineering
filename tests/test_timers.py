"""Human-gate timeout behavior, via the time-skipping test server.

The council's 72h escalation timer is impossible to test in real wall-clock, so this
suite uses start_time_skipping(), which fast-forwards timers automatically when the
workflow is blocked.
"""

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.types import Status
from orchestrator.workflows.bug import BugWorkflow
from orchestrator.workflows.feature_request import FeatureRequestWorkflow
from tests import mock_activities as mock
from tests.helpers import ALL_WORKFLOWS, activities_with, bug_event, feature_event


@pytest.mark.asyncio
async def test_council_escalates_when_human_never_votes():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS, activities=activities_with()
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            # Deliberately do NOT submit the human council vote -> the 72h timer fires.
            # Pre-deliver the later gate signals so only the council timer drives skipping.
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)

            result = await handle.result()

    # No human vote -> escalate to the agents' advisory majority (2/2 approve), proceed
    # and ship, but the log records that the human gate escalated on the timer.
    assert result.status == Status.SHIPPED
    assert any("timed out (72h)" in line for line in result.stage_log)


@pytest.mark.asyncio
async def test_deploy_gate_escalates_on_timeout():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS, activities=activities_with()
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            # Pass the earlier gates, but NEVER approve the deploy -> 7-day timer fires.
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            result = await handle.result()

    assert result.status == Status.ESCALATED
    assert "deploy" not in result.stage_log  # deploy activity never ran


@pytest.mark.asyncio
async def test_bug_clarification_times_out_then_proceeds():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        activities = activities_with({"triage_feedback": mock.triage_needs_clarification})
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS, activities=activities
        ):
            event = bug_event()
            handle = await env.client.start_workflow(
                BugWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            # Never send a clarification -> 7-day timer fires, then it proceeds. Pre-deliver
            # the deploy approval so only the clarification timer drives time-skipping.
            await handle.signal(BugWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert any("clarification timed out" in line for line in result.stage_log)
    assert "await_clarification" in result.stage_log
