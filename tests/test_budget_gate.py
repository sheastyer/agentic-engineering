"""Budget-gate behavior (CLAUDE.md §10, §9.4, decision D7).

A workflow that crosses its dollar ceiling trips a human `budget_override` gate. Approve
once -> continue (doesn't re-trip); decline -> OVER_BUDGET; timeout -> OVER_BUDGET. The
expensive-brief mock returns $5 on the first stage, over the $3 feature ceiling.
"""

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.types import Status
from orchestrator.workflows.feature_request import FeatureRequestWorkflow
from tests import mock_activities as mock
from tests.helpers import ALL_WORKFLOWS, TEMPORAL_CLI, activities_with, feature_event, wait_until

GET_STATE = FeatureRequestWorkflow.get_state
EXPENSIVE = {"pm_draft_brief": mock.pm_draft_brief_expensive}


def _at_budget_gate(state):
    return state.stage.startswith("budget_gate")


@pytest.mark.asyncio
async def test_budget_gate_trips_and_override_continues():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(EXPENSIVE),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            state = await wait_until(handle, _at_budget_gate, GET_STATE)
            assert state.cost_usd == pytest.approx(5.0)  # over the $3 ceiling

            # Approve the override -> the run continues through the remaining gates and ships.
            await handle.signal(FeatureRequestWorkflow.submit_budget_decision, True)
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert any("budget override approved" in line for line in result.stage_log)


@pytest.mark.asyncio
async def test_budget_gate_decline_halts():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(EXPENSIVE),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await wait_until(handle, _at_budget_gate, GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_budget_decision, False)
            result = await handle.result()

    assert result.status == Status.OVER_BUDGET
    assert "exec_council" not in result.stage_log  # halted before any further work


@pytest.mark.asyncio
async def test_budget_gate_timeout_halts():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(EXPENSIVE),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            # Never send a budget decision -> the 7-day override timer fires.
            result = await handle.result()

    assert result.status == Status.OVER_BUDGET
    assert any("budget override timed out" in line for line in result.stage_log)
