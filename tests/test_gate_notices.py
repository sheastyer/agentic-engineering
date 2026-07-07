"""Gate notifications + approver identity (M5 human I/O, docs/handoff-slack-gates.md).

DET tests, $0: the notify_gate stub is overridden with a recorder (same pattern as
mock_activities), so we can assert that every human gate announces itself BEFORE it
parks on its signal, with the context a human needs — and that the decision signals
record WHO decided in the stage log. A raising notifier must never block a gate
(notifications are advisory; the signal path + timeout are the gate)."""

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.types import GateNotice, NotifyResult, Status
from orchestrator.workflows.bug import BugWorkflow
from orchestrator.workflows.feature_request import FeatureRequestWorkflow
from tests import mock_activities as mock
from tests.helpers import (
    ALL_WORKFLOWS,
    TEMPORAL_CLI,
    activities_with,
    bug_event,
    feature_event,
    wait_until,
)

GET_STATE = FeatureRequestWorkflow.get_state


def _recorder(notices: list[GateNotice]):
    @activity.defn(name="notify_gate")
    async def record_notice(notice: GateNotice) -> NotifyResult:
        notices.append(notice)
        return NotifyResult(delivered=True, note="(test) recorded")

    return record_notice


@pytest.mark.asyncio
async def test_feature_gates_notify_with_context_and_record_approvers():
    notices: list[GateNotice] = []
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with({"notify_gate": _recorder(notices)}),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(
                FeatureRequestWorkflow.submit_human_vote, args=[True, "shea"]
            )
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(
                FeatureRequestWorkflow.submit_pm_signoff, args=["approve", "shea"]
            )
            state = await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            # The deploy gate surfaces what the human needs into the queryable state too.
            assert any("PR:" in line for line in state.gate_context)
            await handle.signal(
                FeatureRequestWorkflow.submit_deploy_approval, args=[True, "shea"]
            )
            result = await handle.result()

    assert result.status == Status.SHIPPED
    # Every gate on the happy path announced itself, in stage order.
    assert [n.gate for n in notices] == ["council", "pm_signoff", "deploy"]
    for n in notices:
        assert n.workflow_id == event.id
        assert n.title == event.title
        assert n.project == event.project
    council, signoff, deploy = notices
    # Council: the agents' advisory votes are enumerated rows in front of the human.
    council_votes = {r.label: r.status for r in council.rows}
    assert council_votes.get("legal") in ("approve", "reject")
    assert council_votes.get("sales") in ("approve", "reject")
    # Deploy: PR in the header, QA/review/CI verdicts as rows (PodResult -> notice).
    assert any("PR: local://pr/" in line for line in deploy.context)
    deploy_rows = {r.label: r for r in deploy.rows}
    assert deploy_rows["QA"].status == "passed"
    assert deploy_rows["review"].status == "approved"
    assert "CI" in deploy_rows
    # The queryable state keeps the flat "label: status — detail" shape it always had.
    assert any(line.startswith("QA: passed") for line in state.gate_context)
    # Approver identity lands in the stage log (M5 SEC: the audit shows who decided).
    assert any("human override by shea" in line for line in result.stage_log)
    assert any("pm sign-off: approve (by shea)" in line for line in result.stage_log)
    assert any("deploy approved by shea" in line for line in result.stage_log)


@pytest.mark.asyncio
async def test_budget_gate_notifies_and_records_decliner():
    notices: list[GateNotice] = []
    overrides = {
        "pm_draft_brief": mock.pm_draft_brief_expensive,
        "notify_gate": _recorder(notices),
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
            await wait_until(handle, lambda s: s.stage.startswith("budget_gate"), GET_STATE)
            await handle.signal(
                FeatureRequestWorkflow.submit_budget_decision, args=[False, "shea"]
            )
            result = await handle.result()

    assert result.status == Status.OVER_BUDGET
    assert [n.gate for n in notices] == ["budget"]
    assert any("ceiling" in line for line in notices[0].context)
    assert any("budget override declined by shea" in line for line in result.stage_log)


@pytest.mark.asyncio
async def test_bug_clarification_and_deploy_gates_notify():
    notices: list[GateNotice] = []
    overrides = {
        "triage_feedback": mock.triage_needs_clarification,
        "notify_gate": _recorder(notices),
    }
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(overrides),
        ):
            event = bug_event()
            handle = await env.client.start_workflow(
                BugWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await wait_until(
                handle, lambda s: s.stage == "await_clarification", BugWorkflow.get_state
            )
            await handle.signal(BugWorkflow.submit_user_clarification, "happens on save")
            await wait_until(handle, lambda s: s.stage == "deploy_approval", BugWorkflow.get_state)
            await handle.signal(BugWorkflow.submit_deploy_approval, args=[True, "shea"])
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert [n.gate for n in notices] == ["clarification", "deploy"]
    assert any("report:" in line for line in notices[0].context)
    assert any("PR: local://pr/" in line for line in notices[1].context)
    assert any("deploy approved by shea" in line for line in result.stage_log)


@pytest.mark.asyncio
async def test_notify_failure_never_blocks_a_gate():
    """Notifications are advisory: even a hard-failing notifier leaves every gate
    workable via its signal, and the run still ships (with the failure logged)."""

    @activity.defn(name="notify_gate")
    async def notify_always_fails(notice: GateNotice) -> NotifyResult:
        raise ApplicationError("(test) slack is down", type="NonRetryableAgentError")

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with({"notify_gate": notify_always_fails}),
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
    assert any("gate notification failed (council)" in line for line in result.stage_log)
    assert any("gate notification failed (deploy)" in line for line in result.stage_log)
