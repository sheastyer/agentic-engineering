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
import os
import uuid

from temporalio.client import Client, WorkflowHandle

from orchestrator.intake import route
from orchestrator.shared.config import TEMPORAL_NAMESPACE, TEMPORAL_TARGET
from orchestrator.shared.types import FeedbackEvent, FeedbackKind
from orchestrator.workflows.bug import BugWorkflow
from orchestrator.workflows.feature_request import FeatureRequestWorkflow


async def drive_feature(handle: WorkflowHandle, auto: bool = True) -> None:
    """Gate-reactive driver: stream new log lines as the workflow advances and play the
    human at every gate that appears (council, budget override, PM sign-off, deploy). Each
    gate is signalled once; for the happy steel-thread path that's all it takes.

    ``auto=False`` (the default when ORG_SLACK is set) watches without signalling — a
    real human approves via the Slack buttons, and auto-signalling would race them."""
    q = FeatureRequestWorkflow.get_state
    done = {"council": False, "budget": False, "coding_budget": False, "signoff": False, "deploy": False}
    seen = 0
    misses = 0
    while True:
        try:
            state = await handle.query(q)
            misses = 0
        except Exception:
            # A query can transiently fail (task expiry races on the dev server). Don't
            # treat that as terminal — keep polling. Only give up after many in a row
            # (the workflow is genuinely gone), letting main() surface the real result.
            misses += 1
            if misses > 50:
                break
            await asyncio.sleep(0.6)
            continue
        for line in state.log[seen:]:
            print(f"   · {line}")
        seen = len(state.log)
        stage = state.stage

        if not auto:
            pass  # watch only — the human decides in Slack
        elif stage == "exec_council" and not done["council"]:
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "cli-human"])
            print("  ✓ council: human voted APPROVE")
            done["council"] = True
        elif stage.startswith("budget_gate") and not done["budget"]:
            await handle.signal(
                FeatureRequestWorkflow.submit_budget_decision, args=[True, "cli-human"]
            )
            print(f"  ✓ budget override: APPROVE  ({stage})")
            done["budget"] = True
        elif stage.startswith("coding_budget_gate") and not done["coding_budget"]:
            await handle.signal(
                FeatureRequestWorkflow.submit_coding_budget, args=["approve", 0.0, "cli-human"]
            )
            print(f"  ✓ coding budget: FUND ESTIMATE  ({stage})")
            done["coding_budget"] = True
        elif stage == "pm_signoff" and not done["signoff"]:
            await handle.signal(
                FeatureRequestWorkflow.submit_pm_signoff, args=["approve", "cli-human"]
            )
            print("  ✓ PM sign-off: APPROVE")
            done["signoff"] = True
        elif stage == "deploy_approval" and not done["deploy"]:
            await handle.signal(
                FeatureRequestWorkflow.submit_deploy_approval, args=[True, "cli-human"]
            )
            print("  ✓ deploy approval: APPROVE")
            done["deploy"] = True

        if stage == "done":
            break
        await asyncio.sleep(1.0)


async def drive_bug(handle: WorkflowHandle, auto: bool = True) -> None:
    q = BugWorkflow.get_state
    done = {"deploy": False, "budget": False, "coding_budget": False}
    seen = 0
    misses = 0
    while True:
        try:
            state = await handle.query(q)
            misses = 0
        except Exception:
            # Same transient-query resilience as the feature driver: only give up after
            # many consecutive misses (the workflow is genuinely gone).
            misses += 1
            if misses > 50:
                break
            await asyncio.sleep(0.6)
            continue
        for line in state.log[seen:]:
            print(f"   · {line}")
        seen = len(state.log)
        if not auto:
            pass  # watch only — the human decides in Slack
        elif state.stage == "deploy_approval" and not done["deploy"]:
            await handle.signal(BugWorkflow.submit_deploy_approval, args=[True, "cli-human"])
            print("  ✓ deploy approval: APPROVE")
            done["deploy"] = True
        elif state.stage.startswith("budget_gate") and not done["budget"]:
            # The bug path runs the real coding pod now, so the budget gate can trip
            # mid-run; the demo driver plays the human and approves the override.
            await handle.signal(BugWorkflow.submit_budget_decision, args=[True, "cli-human"])
            print(f"  ✓ budget override: APPROVE  ({state.stage})")
            done["budget"] = True
        elif state.stage.startswith("coding_budget_gate") and not done["coding_budget"]:
            await handle.signal(
                BugWorkflow.submit_coding_budget, args=["approve", 0.0, "cli-human"]
            )
            print(f"  ✓ coding budget: FUND ESTIMATE  ({state.stage})")
            done["coding_budget"] = True
        if state.stage == "done":
            break
        await asyncio.sleep(1.0)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Drive a demo workflow.")
    parser.add_argument("--project", default="meal-planner", help="Project Profile id")
    parser.add_argument("--bug", action="store_true", help="run a bug demo instead of a feature")
    parser.add_argument("--title", default=None)
    parser.add_argument(
        "--auto-gates",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="play the human at every gate (default: on, unless ORG_SLACK is set — "
        "then real humans approve via the Slack buttons and this driver only watches)",
    )
    args = parser.parse_args()
    auto = args.auto_gates if args.auto_gates is not None else not os.environ.get("ORG_SLACK")

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
    if not auto:
        print("  (watching only — approve the gates via the Slack buttons)")

    if args.bug:
        await drive_bug(handle, auto=auto)
    else:
        await drive_feature(handle, auto=auto)

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
