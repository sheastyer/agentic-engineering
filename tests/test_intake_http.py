"""The HTTP intake adapter's surface, at $0 and with no Temporal server.

`create_app(temporal_connect=...)` takes an injected client factory, so these tests run
the real FastAPI stack (auth, validation, routing glue) against a fake Temporal client
that records `start_workflow` calls. Skipped wholesale if the `[intake]` extra isn't
installed — the stub/$0 environments don't carry fastapi (same posture as `[slack]`).
"""

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from orchestrator.intake_http import create_app  # noqa: E402
from orchestrator.workflows.bug import BugWorkflow  # noqa: E402
from orchestrator.workflows.feature_request import FeatureRequestWorkflow  # noqa: E402

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _FakeHandle:
    def __init__(self, workflow_id: str):
        self.id = workflow_id
        self.result_run_id = "run-1"
        self.first_execution_run_id = "run-1"


class _FakeTemporalClient:
    """Records start_workflow calls; raises on a duplicate workflow id like Temporal does
    under REJECT_DUPLICATE (id-ever, not id-while-running — which is why route() must pass
    that policy: the ALLOW_DUPLICATE default re-runs a completed id silently)."""

    def __init__(self):
        self.calls = []

    async def start_workflow(self, target, event, *, id, task_queue, id_reuse_policy=None):
        from temporalio.common import WorkflowIDReusePolicy
        from temporalio.exceptions import WorkflowAlreadyStartedError

        assert id_reuse_policy == WorkflowIDReusePolicy.REJECT_DUPLICATE
        if any(c["id"] == id for c in self.calls):
            raise WorkflowAlreadyStartedError(id, "wf", run_id="run-1")
        self.calls.append({"target": target, "event": event, "id": id, "task_queue": task_queue})
        return _FakeHandle(id)


@pytest.fixture()
def client_and_fake():
    fake = _FakeTemporalClient()

    async def connect():
        return fake

    app = create_app(token=TOKEN, temporal_connect=connect)
    with TestClient(app) as client:  # context manager runs the lifespan (connects)
        yield client, fake


def test_healthz_is_open_and_lists_projects(client_and_fake):
    client, _ = client_and_fake
    res = client.get("/healthz")
    assert res.status_code == 200
    assert "meal-planner" in res.json()["projects"]


def test_feedback_requires_bearer_token(client_and_fake):
    client, fake = client_and_fake
    payload = {"project": "meal-planner", "kind": "bug", "title": "t"}
    assert client.post("/feedback", json=payload).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.post("/feedback", json=payload, headers=bad).status_code == 401
    assert fake.calls == []


def test_bug_feedback_starts_bug_workflow(client_and_fake):
    client, fake = client_and_fake
    res = client.post(
        "/feedback",
        json={"project": "meal-planner", "kind": "bug", "title": "save button 500s",
              "body": "clicking save on the weekly plan errors", "id": "bug-42"},
        headers=AUTH,
    )
    assert res.status_code == 202
    assert res.json()["workflow_id"] == "feedback-bug-42"
    [call] = fake.calls
    assert call["target"] == BugWorkflow.run
    assert call["event"].kind.value == "bug"
    assert call["event"].project == "meal-planner"


def test_feature_feedback_starts_feature_workflow_and_generates_id(client_and_fake):
    client, fake = client_and_fake
    res = client.post(
        "/feedback",
        json={"project": "meal-planner", "kind": "feature", "title": "dark mode please"},
        headers=AUTH,
    )
    assert res.status_code == 202
    [call] = fake.calls
    assert call["target"] == FeatureRequestWorkflow.run
    assert call["id"].startswith("feedback-")
    assert len(call["event"].id) == 12  # generated idempotency key


def test_duplicate_id_is_409_not_a_second_run(client_and_fake):
    client, fake = client_and_fake
    payload = {"project": "meal-planner", "kind": "bug", "title": "t", "id": "dup-1"}
    assert client.post("/feedback", json=payload, headers=AUTH).status_code == 202
    assert client.post("/feedback", json=payload, headers=AUTH).status_code == 409
    assert len(fake.calls) == 1


def test_unknown_project_and_bad_kind_are_422(client_and_fake):
    client, fake = client_and_fake
    res = client.post(
        "/feedback",
        json={"project": "not-a-project", "kind": "bug", "title": "t"},
        headers=AUTH,
    )
    assert res.status_code == 422
    assert "known" in res.json()["detail"]
    res = client.post(
        "/feedback",
        json={"project": "meal-planner", "kind": "outage", "title": "t"},
        headers=AUTH,
    )
    assert res.status_code == 422
    assert fake.calls == []
