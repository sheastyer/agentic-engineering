"""Test helpers: worker wiring, activity-override merging, and a query poller."""

import asyncio
import uuid

from orchestrator.activities import stubs
from orchestrator.shared.types import FeedbackEvent, FeedbackKind
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

# Path to the temporal CLI we installed, so start_local reuses it instead of downloading.
TEMPORAL_CLI = "/Users/sheastyer/.temporalio/bin/temporal"


def activities_with(overrides: dict | None = None) -> list:
    """Full activity list, with named stubs replaced by overrides (keyed by stub name)."""
    base = {a.__name__: a for a in stubs.ALL_ACTIVITIES}
    if overrides:
        base.update(overrides)
    return list(base.values())


def feature_event(title: str = "demo feature") -> FeedbackEvent:
    return FeedbackEvent(
        id=f"feat-{uuid.uuid4().hex[:8]}",
        kind=FeedbackKind.FEATURE,
        title=title,
        body="(test) body",
        submitted_by="test",
        project="meal-planner",
    )


def bug_event(title: str = "demo bug") -> FeedbackEvent:
    return FeedbackEvent(
        id=f"bug-{uuid.uuid4().hex[:8]}",
        kind=FeedbackKind.BUG,
        title=title,
        body="(test) body",
        submitted_by="test",
        project="meal-planner",
    )


async def wait_until(handle, predicate, query, timeout: float = 10.0):
    """Poll a workflow query until predicate(state) is true. Returns the state."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        state = await handle.query(query)
        if predicate(state):
            return state
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for predicate; last state: {state}")
