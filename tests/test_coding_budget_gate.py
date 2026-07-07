"""The pre-pod coding-budget gate (§9.4, §10): fund the round before it spends.

The complaint this gate answers: live runs dying mid-coding at the default caps. Before
EngineeringPodWorkflow starts, the org shows a deterministic cost estimate and a human
either funds it, funds a custom amount (Slack text input), or halts. The approved budget
rides StoryPlan.coding_budget_usd into the pod (replacing CODING_MAX_BUDGET_USD for the
run) and lifts the workflow ceiling so the sanctioned spend can't re-trip the override
gate.

Gate discipline: the estimate activity's STUB returns gate=False, so $0 dry-runs and the
rest of the suite never park here — these tests swap in the gated mock (the live twin's
behavior) to drive the gate. Timeout funds the estimate (the run was already
human-approved upstream and spend stays bounded), reject halts as HELD.
"""

import json

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.humanio.gates import (
    GateAction,
    build_blocks,
    parse_dollars,
    signal_for,
)
from orchestrator.humanio.slack_listener import parse_block_action, resolved_blocks
from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.estimates import estimate_coding_run
from orchestrator.shared.types import GateNotice, Status, Story, StoryPlan, StoryResult
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
GATED = {"estimate_coding_budget": mock.estimate_coding_budget_gated}

# The stub architect plans one opus + one sonnet story, so the estimate is
# base 0.75 + opus 2.50 + sonnet 1.25 = $4.50; the bug path's one sonnet story is $2.00.
FEATURE_ESTIMATE = 4.50
BUG_ESTIMATE = 2.00


def _implement_capture(budgets: list[float]):
    """implement_stories override that records the coding budget each plan carried."""

    @activity.defn(name="implement_stories")
    async def capture(plan: StoryPlan) -> StoryResult:
        budgets.append(plan.coding_budget_usd)
        return StoryResult(
            story_id=plan.feature_id, status="done", pr_ref=f"pr://{plan.feature_id}",
            summary="(test) captured", cost_tokens=1,
        )

    return capture


def _at_gate(state):
    return state.stage.startswith("coding_budget_gate")


# --- the estimator (pure) -----------------------------------------------------------


def test_estimate_prices_stories_by_tier():
    usd, breakdown = estimate_coding_run(
        [
            Story(id="S1", title="a", estimate=1, tier="sonnet"),
            Story(id="S2", title="b", estimate=3, tier="opus"),
        ]
    )
    assert usd == FEATURE_ESTIMATE
    assert breakdown[0] == f"estimated coding cost: ${FEATURE_ESTIMATE:.2f}"
    assert "1× sonnet ($1.25) + 1× opus ($2.50)" in breakdown[1]
    # The worst case (revise loops re-draw the funded cap) is surfaced, not hidden.
    assert any("worst case" in line for line in breakdown)


def test_estimate_defaults_unknown_tiers_and_empty_plans_to_sonnet():
    usd, _ = estimate_coding_run([Story(id="S1", title="a", estimate=1, tier="weird")])
    assert usd == BUG_ESTIMATE  # unknown tier priced as sonnet
    usd, _ = estimate_coding_run([])
    assert usd == BUG_ESTIMATE  # an empty plan still runs one coding session


# --- Slack plumbing: dollars, blocks, payloads, signals -------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("6.50", 6.50),
        ("$12", 12.0),
        (" 2.5 ", 2.5),
        ("abc", None),
        ("", None),
        ("0", None),        # must be positive
        ("-3", None),
        ("501", None),      # sanity cap
    ],
)
def test_parse_dollars(raw, expected):
    assert parse_dollars(raw) == expected


def _notice() -> GateNotice:
    return GateNotice(
        workflow_id="feedback-123", gate="coding_budget", title="Add dark mode",
        project="meal-planner", context=["estimated coding cost: $4.50"],
    )


def test_coding_budget_card_has_buttons_and_a_decodable_text_input():
    blocks = build_blocks(_notice())
    (input_block,) = [b for b in blocks if b["type"] == "input"]
    # Inputs in messages only fire block_actions with dispatch_action set.
    assert input_block["dispatch_action"] is True
    assert input_block["element"]["type"] == "plain_text_input"
    # The envelope rides block_id (an input's value is the typed text) and must decode
    # to exactly what the listener expects.
    assert json.loads(input_block["block_id"]) == {
        "workflow_id": "feedback-123", "gate": "coding_budget", "decision": "custom",
    }
    # Other gates don't grow an input.
    deploy = build_blocks(GateNotice(workflow_id="w", gate="deploy", title="t", project="p"))
    assert all(b["type"] != "input" for b in deploy)


def test_parse_block_action_decodes_text_input_payloads():
    payload = {
        "type": "block_actions",
        "user": {"id": "U0SHEA", "username": "shea"},
        "actions": [
            {
                "type": "plain_text_input",
                "action_id": "gate:coding_budget:custom",
                "block_id": json.dumps(
                    {"workflow_id": "feedback-123", "gate": "coding_budget", "decision": "custom"}
                ),
                "value": "6.50",
            }
        ],
    }
    action = parse_block_action(payload)
    assert action == GateAction(
        workflow_id="feedback-123", gate="coding_budget", decision="custom",
        user_id="U0SHEA", user_name="shea", text="6.50",
    )


@pytest.mark.parametrize(
    ("decision", "text", "expected"),
    [
        ("approve", "", ("submit_coding_budget", ["approve", 0.0, "shea"])),
        ("reject", "", ("submit_coding_budget", ["reject", 0.0, "shea"])),
        ("custom", "6.50", ("submit_coding_budget", ["custom", 6.5, "shea"])),
        ("custom", "not money", None),  # unparseable amount -> no signal
        ("revise", "", None),           # decision this gate doesn't understand
    ],
)
def test_signal_for_coding_budget(decision, text, expected):
    action = GateAction(
        workflow_id="feedback-123", gate="coding_budget", decision=decision,
        user_id="U0SHEA", user_name="shea", text=text,
    )
    assert signal_for(action) == expected


def test_resolved_blocks_strip_the_text_input_too():
    blocks = resolved_blocks(build_blocks(_notice()), "✅ coding_budget: custom $6.50 by @shea")
    assert all(b["type"] not in ("actions", "input") for b in blocks)


# --- workflow behavior ----------------------------------------------------------------


async def _to_the_gate(env, handle):
    """Walk the feature workflow's upstream gates, stopping parked at coding_budget."""
    await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "tester"])
    await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
    await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
    return await wait_until(handle, _at_gate, GET_STATE)


@pytest.mark.asyncio
async def test_funding_the_estimate_flows_into_the_pod_and_ships():
    budgets: list[float] = []
    overrides = {**GATED, "implement_stories": _implement_capture(budgets)}
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(overrides),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            state = await _to_the_gate(env, handle)
            # The gate surfaces the estimate to the human (queryable state + notice).
            assert any("estimated coding cost" in line for line in state.gate_context)
            await handle.signal(
                FeatureRequestWorkflow.submit_coding_budget, args=["approve", 0.0, "shea"]
            )
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert budgets == [FEATURE_ESTIMATE]  # the funded estimate reached the pod's plan
    assert any(
        f"coding budget funded: estimate ${FEATURE_ESTIMATE:.2f} by shea" in line
        for line in result.stage_log
    )


@pytest.mark.asyncio
async def test_custom_budget_overrides_the_estimate():
    budgets: list[float] = []
    overrides = {**GATED, "implement_stories": _implement_capture(budgets)}
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(overrides),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await _to_the_gate(env, handle)
            await handle.signal(
                FeatureRequestWorkflow.submit_coding_budget, args=["custom", 8.0, "shea"]
            )
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert budgets == [8.0]
    assert any("coding budget funded: custom $8.00 by shea" in line for line in result.stage_log)


@pytest.mark.asyncio
async def test_declining_the_coding_budget_halts_before_the_pod():
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(GATED),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await _to_the_gate(env, handle)
            await handle.signal(
                FeatureRequestWorkflow.submit_coding_budget, args=["reject", 0.0, "shea"]
            )
            result = await handle.result()

    assert result.status == Status.HELD
    assert "engineering_pod" not in result.stage_log  # the pod never started
    assert "Coding budget declined" in result.summary
    assert any("coding budget declined by shea" in line for line in result.stage_log)


@pytest.mark.asyncio
async def test_timeout_funds_the_estimate_instead_of_stranding_the_run():
    # Bug path (no upstream gates to play) on the time-skipping server: nobody answers
    # the coding-budget card, the 7-day timer fires, and the run proceeds funded at the
    # org's own estimate — bounded spend, no stranded run. (The deploy gate then times
    # out too, so the terminal status is ESCALATED — that gate keeps its own semantics.)
    budgets: list[float] = []
    overrides = {**GATED, "implement_stories": _implement_capture(budgets)}
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(overrides),
        ):
            event = bug_event()
            handle = await env.client.start_workflow(
                BugWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            result = await handle.result()

    assert budgets == [BUG_ESTIMATE]
    assert any(
        f"coding budget gate timed out; funding the estimate (${BUG_ESTIMATE:.2f})" in line
        for line in result.stage_log
    )
    assert result.status == Status.ESCALATED  # the (unanswered) deploy gate, not this one
