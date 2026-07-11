"""IntakeRouter — the queue consumer that turns feedback into workflows.

This is client-side glue, NOT a workflow (CLAUDE.md §7): "the loop is not a running
session that polls an LLM." A consumer receives each normalized FeedbackEvent and calls
``client.start_workflow(...)`` — the system costs nothing while idle. In M5 a real
intake adapter (per the Project Profile) feeds this; in M1 the CLI calls it directly.
"""

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDReusePolicy

from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.types import FeedbackEvent, FeedbackKind
from orchestrator.workflows.bug import BugWorkflow
from orchestrator.workflows.feature_request import FeatureRequestWorkflow


async def route(client: Client, event: FeedbackEvent) -> WorkflowHandle:
    """Start the right workflow for a feedback event. Workflow id is the feedback id,
    so re-delivering the same event is idempotent (Temporal rejects a duplicate id).

    REJECT_DUPLICATE, not the ALLOW_DUPLICATE default: these workflows *complete* (shipped,
    rejected, held), and the default only rejects a duplicate id while the original run is
    still open — a redelivered event would silently start a second full (paid) run once the
    first one closed. The intake guarantee is per-id-ever, not per-id-while-running."""
    workflow_id = f"feedback-{event.id}"
    target = (
        FeatureRequestWorkflow.run
        if event.kind == FeedbackKind.FEATURE
        else BugWorkflow.run
    )
    return await client.start_workflow(
        target,
        event,
        id=workflow_id,
        task_queue=TASK_QUEUE,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
    )
