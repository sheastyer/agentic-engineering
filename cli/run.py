"""CLI driver (M1): start a workflow and walk it through the human gates.

Requires a running dev server (`temporal server start-dev`) and worker
(`python -m worker.main`). Then:

    ./.venv/bin/python -m cli.run            # feature-request demo
    ./.venv/bin/python -m cli.run --bug      # bug demo

It plays the human at each gate, polling the workflow's queryable state to show
progression. With stub activities this proves the control flow for zero LLM tokens; set
USE_AGENT_TRIAGE=1 on the worker to exercise the real triage agent.
"""

import argparse
import asyncio
import uuid

from temporalio.client import Client, WorkflowHandle

from orchestrator.intake import route
from orchestrator.shared.config import TEMPORAL_NAMESPACE, TEMPORAL_TARGET
from orchestrator.shared.types import FeedbackEvent, FeedbackKind
from orchestrator.workflows.bug import BugWorkflow
from orchestrator.workflows.feature_request import FeatureRequestWorkflow


async def wait_for_stage(handle: WorkflowHandle, query, stage: str, timeout: float = 15.0) -> bool:
    """Poll the workflow's get_state query until it reports the given stage."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        state = await handle.query(query)
        if state.stage == stage:
            return True
        await asyncio.sleep(0.25)
    return False


async def drive_feature(handle: WorkflowHandle) -> None:
    q = FeatureRequestWorkflow.get_state
    await wait_for_stage(handle, q, "exec_council")
    await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "cli-human"])
    print("  ✓ council: human voted APPROVE")
    await wait_for_stage(handle, q, "pm_signoff")
    await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, "approve")
    print("  ✓ PM sign-off: APPROVE")
    await wait_for_stage(handle, q, "deploy_approval")
    await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, True)
    print("  ✓ deploy approval: APPROVE")


async def drive_bug(handle: WorkflowHandle) -> None:
    await wait_for_stage(handle, BugWorkflow.get_state, "deploy_approval")
    await handle.signal(BugWorkflow.submit_deploy_approval, True)
    print("  ✓ deploy approval: APPROVE")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Drive a demo workflow.")
    parser.add_argument("--project", default="meal-planner", help="Project Profile id")
    parser.add_argument("--bug", action="store_true", help="run a bug demo instead of a feature")
    parser.add_argument("--title", default=None)
    args = parser.parse_args()

    client = await Client.connect(TEMPORAL_TARGET, namespace=TEMPORAL_NAMESPACE)
    kind = FeedbackKind.BUG if args.bug else FeedbackKind.FEATURE
    default_title = (
        "Saving a weekly plan throws an error"
        if args.bug
        else "Add a 'surprise me' weekly menu button"
    )
    event = FeedbackEvent(
        id=f"demo-{uuid.uuid4().hex[:8]}",
        kind=kind,
        title=args.title or default_title,
        body="Reported via the CLI demo driver.",
        submitted_by="cli",
        project=args.project,
    )

    handle = await route(client, event)
    print(f"▶ started {handle.id} ({kind.value} for {args.project!r})")

    if args.bug:
        await drive_bug(handle)
    else:
        await drive_feature(handle)

    result = await handle.result()
    print("\n── result ─────────────────────────────────────────────")
    spend = "stubs — no real LLM spend" if result.cost_usd == 0 else "real LLM spend"
    print(f" status      : {result.status}")
    print(f" cost        : ${result.cost_usd:.4f}  ({result.cost_tokens} tokens — {spend})")
    print(f" summary     : {result.summary}")
    print(" stage log   :")
    for i, stage in enumerate(result.stage_log, 1):
        print(f"   {i:>2}. {stage}")


if __name__ == "__main__":
    asyncio.run(main())
