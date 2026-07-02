"""Replay test — proves the orchestration is deterministic (CLAUDE.md §9.1).

Runs a full feature request to completion, then replays the parent workflow AND both
child workflows (consumer research, engineering pod). The engineering pod is where real
coding agents + worktrees arrive in M4, so replaying it is exactly where nondeterminism
would bite — replaying only the parent (the original gap) would give false confidence.
Any nondeterminism (an LLM call, I/O, a clock read, unordered iteration) sneaking into
workflow code surfaces here as a nondeterminism error.
"""

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.types import Status
from orchestrator.workflows.bug import BugWorkflow
from orchestrator.workflows.feature_request import FeatureRequestWorkflow
from tests.helpers import (
    ALL_WORKFLOWS,
    TEMPORAL_CLI,
    activities_with,
    bug_event,
    feature_event,
    wait_until,
)

GET_STATE = FeatureRequestWorkflow.get_state


@pytest.mark.asyncio
async def test_feature_request_history_replays_deterministically():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS, activities=activities_with()
        ):
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

            # Parent + deterministically-named children (happy path: no sign-off revise,
            # so the research child carries the "-0" suffix).
            child_ids = [f"{event.id}-research-0", f"{event.id}-pod"]
            histories = [await handle.fetch_history()]
            for cid in child_ids:
                histories.append(await env.client.get_workflow_handle(cid).fetch_history())

    # Replay with no server/activities — pure determinism check against workflow code.
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    for history in histories:
        await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_bug_history_replays_deterministically():
    """The bug path rides the engineering pod as a child (2026-07-02 unification), so its
    replay must cover the parent AND the pod child — same rationale as the feature test."""
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

            histories = [await handle.fetch_history()]
            histories.append(
                await env.client.get_workflow_handle(f"{event.id}-pod").fetch_history()
            )

    replayer = Replayer(workflows=ALL_WORKFLOWS)
    for history in histories:
        await replayer.replay_workflow(history)
