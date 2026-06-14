"""Temporal worker entrypoint (M0/M1).

Connects to the local dev server and serves every workflow + activity on the org task
queue. Run alongside `temporal server start-dev`:

    ./.venv/bin/python -m worker.main
"""

import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from orchestrator.activities.stubs import ALL_ACTIVITIES
from orchestrator.shared.config import TASK_QUEUE, TEMPORAL_NAMESPACE, TEMPORAL_TARGET
from orchestrator.workflows.bug import BugWorkflow
from orchestrator.workflows.consumer_research import ConsumerResearchWorkflow
from orchestrator.workflows.engineering_pod import EngineeringPodWorkflow
from orchestrator.workflows.feature_request import FeatureRequestWorkflow

ALL_WORKFLOWS = [
    FeatureRequestWorkflow,
    BugWorkflow,
    ConsumerResearchWorkflow,
    EngineeringPodWorkflow,
]


def build_activities() -> list:
    """Stub activities by default ($0). Env toggles swap in real runner-backed ones, one
    at a time, as they're validated in M3 — the swap is by activity name."""
    activities = list(ALL_ACTIVITIES)
    if os.environ.get("USE_AGENT_TRIAGE"):
        from orchestrator.activities.agent_backed import triage_feedback_agent

        activities = [a for a in activities if a.__name__ != "triage_feedback"]
        activities.append(triage_feedback_agent)
        logging.info("USE_AGENT_TRIAGE: real triage via MODEL_PROVIDER=%s",
                     os.environ.get("MODEL_PROVIDER", "anthropic"))
    return activities


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(TEMPORAL_TARGET, namespace=TEMPORAL_NAMESPACE)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=ALL_WORKFLOWS,
        activities=build_activities(),
    )
    logging.info("worker connected to %s; serving task queue %r", TEMPORAL_TARGET, TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
