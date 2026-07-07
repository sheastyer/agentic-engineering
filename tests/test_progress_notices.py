"""Per-run Slack progress thread (M5 human I/O): every stage posts into one thread.

DET tests, $0: notify_progress/notify_gate stubs are overridden with recorders. The
contract under test: the run's FIRST progress post is the thread root (no thread_ts);
the workflow stores the ts it returns and threads every later progress post AND gate
notice onto it; artifact stages (PRD, research) carry their documents; and a failing
progress notifier never blocks the run (advisory, like gates)."""

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.types import GateNotice, NotifyResult, ProgressNotice, Status
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
ROOT_TS = "1111.2222"


def _progress_recorder(records: list[ProgressNotice]):
    @activity.defn(name="notify_progress")
    async def record(notice: ProgressNotice) -> NotifyResult:
        records.append(notice)
        return NotifyResult(delivered=True, ts=ROOT_TS)

    return record


def _gate_recorder(records: list[GateNotice]):
    @activity.defn(name="notify_gate")
    async def record(notice: GateNotice) -> NotifyResult:
        records.append(notice)
        return NotifyResult(delivered=True, ts="9999.0000")

    return record


@pytest.mark.asyncio
async def test_feature_run_posts_every_stage_into_one_thread():
    progress: list[ProgressNotice] = []
    gates: list[GateNotice] = []
    overrides = {
        "notify_progress": _progress_recorder(progress),
        "notify_gate": _gate_recorder(gates),
    }
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(overrides),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "shea"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, args=["approve", "shea"])
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, args=[True, "shea"])
            result = await handle.result()

    assert result.status == Status.SHIPPED
    stages = [n.stage for n in progress]
    assert stages == [
        "feedback_received", "brief", "council", "prd", "mocks",
        "research", "stories", "engineering", "done",
    ]
    # Thread anchoring: the root posts with no thread; everything after threads onto
    # the ts the root's activity returned — gates included.
    assert progress[0].thread_ts == ""
    assert all(n.thread_ts == ROOT_TS for n in progress[1:])
    assert [g.gate for g in gates] == ["council", "pm_signoff", "deploy"]
    assert all(g.thread_ts == ROOT_TS for g in gates)
    # Artifact stages carry their documents (PRD full text; research synthesis).
    by_stage = {n.stage: n for n in progress}
    assert by_stage["prd"].document_md and "PRD v1" in by_stage["prd"].document_md
    assert "PRD v1" in by_stage["prd"].document_title
    assert by_stage["research"].document_md.startswith("# Consumer research")
    assert "budget-conscious" in by_stage["research"].document_md
    # The terminal post reports the outcome.
    assert any("status: shipped" in line for line in by_stage["done"].text)
    # Non-artifact stages have readable context. The brief's fields and the council's
    # per-voter tally are now enumerated rows (scannable), the rest stays header text.
    assert any(r.label == "summary" and r.detail for r in by_stage["brief"].rows)
    assert any("outcome: approved" in line for line in by_stage["council"].text)
    assert {r.label for r in by_stage["council"].rows} >= {"legal", "sales"}
    assert any("PR: local://pr/" in line for line in by_stage["engineering"].text)
    assert any(r.label == "CI" for r in by_stage["engineering"].rows)


@pytest.mark.asyncio
async def test_bug_run_posts_progress_thread():
    progress: list[ProgressNotice] = []
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with({"notify_progress": _progress_recorder(progress)}),
        ):
            event = bug_event()
            handle = await env.client.start_workflow(
                BugWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await wait_until(handle, lambda s: s.stage == "deploy_approval", BugWorkflow.get_state)
            await handle.signal(BugWorkflow.submit_deploy_approval, args=[True, "shea"])
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert [n.stage for n in progress] == ["feedback_received", "triage", "engineering", "done"]
    assert progress[0].thread_ts == ""
    assert all(n.thread_ts == ROOT_TS for n in progress[1:])


@pytest.mark.asyncio
async def test_progress_failure_never_blocks_the_run():
    @activity.defn(name="notify_progress")
    async def progress_always_fails(notice: ProgressNotice) -> NotifyResult:
        raise ApplicationError("(test) slack is down", type="NonRetryableAgentError")

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with({"notify_progress": progress_always_fails}),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "shea"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, args=["approve", "shea"])
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, args=[True, "shea"])
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert any("progress notification failed (feedback_received)" in line for line in result.stage_log)
