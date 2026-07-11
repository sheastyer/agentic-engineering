"""HTTP intake adapter — the M5 "endpoint" intake: POST /feedback → IntakeRouter.

A thin FastAPI app in front of `orchestrator.intake.route()`: a caller (the target app's
backend, a webhook relay, a future Kafka-consumer bridge, or curl) POSTs normalized
feedback and the right workflow starts. This is client-side glue like `intake.py` itself
— no LLM call, no polling; the system still costs nothing while idle (CLAUDE.md §7).

Run: ``python -m orchestrator.intake_http`` (uvicorn on ``INTAKE_PORT``, default 8000).
Requires ``INTAKE_TOKEN`` — the endpoint is outward-facing, so it refuses to start
without a bearer token (fail-fast, same posture as the worker's ORG_LIVE/ORG_SLACK
checks). Needs the ``[intake]`` extra (fastapi + uvicorn), imported lazily where it
matters so the stub/$0 paths never depend on it.

Idempotency: callers may supply ``id`` — the workflow id is derived from it
(``feedback-<id>``), so re-delivering the same event returns 409 instead of a duplicate
run (Temporal rejects the duplicate workflow id; see `intake.route`). Omitting ``id``
generates one, i.e. every POST is a new run.
"""

import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from orchestrator.intake import route
from orchestrator.projects.loader import known_projects, load_profile
from orchestrator.shared.config import TEMPORAL_NAMESPACE, TEMPORAL_TARGET
from orchestrator.shared.types import FeedbackEvent, FeedbackKind

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


class FeedbackIn(BaseModel):
    """The wire format for one piece of feedback. Mirrors FeedbackEvent minus reporter
    plumbing; `body` is untrusted end-user text and is treated as such downstream."""

    project: str = Field(min_length=1, description="Project Profile id, e.g. meal-planner")
    kind: FeedbackKind
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=20_000)
    id: str | None = Field(
        default=None,
        max_length=120,
        description="Optional idempotency key; re-posting the same id is a 409, not a new run.",
    )
    submitted_by: str = Field(default="intake-api", max_length=120)


class FeedbackAccepted(BaseModel):
    workflow_id: str
    run_id: str
    project: str
    kind: FeedbackKind


def create_app(*, token: str, temporal_connect=None) -> FastAPI:
    """Build the app. `temporal_connect` is an async factory returning a Temporal Client —
    injectable so tests exercise the HTTP surface with a fake client and $0/no server."""

    connect = temporal_connect or (
        lambda: Client.connect(TEMPORAL_TARGET, namespace=TEMPORAL_NAMESPACE)
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Connect once at startup and fail fast — a dead Temporal target should crash the
        # container (restart policy retries), not 500 on the first real feedback.
        app.state.temporal = await connect()
        logger.info("intake connected to Temporal at %s", TEMPORAL_TARGET)
        yield

    app = FastAPI(title="agentic-org intake", lifespan=lifespan)

    def require_token(
        credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    ) -> None:
        # compare_digest: the endpoint is outward-facing and unthrottled — don't hand an
        # attacker a timing oracle on the token. Compared as bytes: str-mode compare_digest
        # raises TypeError on non-ASCII, and a hostile client can put latin-1 bytes in the
        # Authorization header — that must be a 401, not a 500.
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode("utf-8"), token.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    @app.get("/healthz")
    async def healthz() -> dict:
        # Unauthenticated on purpose: the compose healthcheck and Traefik probe hit this.
        return {"ok": True, "projects": known_projects()}

    @app.post(
        "/feedback",
        status_code=202,
        response_model=FeedbackAccepted,
        dependencies=[Depends(require_token)],
    )
    async def submit_feedback(payload: FeedbackIn, request: Request) -> FeedbackAccepted:
        try:
            load_profile(payload.project)
        except KeyError:
            raise HTTPException(
                status_code=422,
                detail=f"unknown project {payload.project!r}; known: {known_projects()}",
            )
        event = FeedbackEvent(
            id=payload.id or uuid.uuid4().hex[:12],
            kind=payload.kind,
            title=payload.title,
            body=payload.body,
            submitted_by=payload.submitted_by,
            project=payload.project,
        )
        try:
            handle = await route(request.app.state.temporal, event)
        except WorkflowAlreadyStartedError:
            raise HTTPException(
                status_code=409,
                detail=f"feedback {event.id!r} was already accepted (workflow feedback-{event.id})",
            )
        logger.info(
            "accepted %s feedback %s for %s -> workflow %s",
            event.kind, event.id, event.project, handle.id,
        )
        return FeedbackAccepted(
            workflow_id=handle.id,
            run_id=handle.result_run_id or handle.first_execution_run_id or "",
            project=event.project,
            kind=event.kind,
        )

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("INTAKE_TOKEN", "")
    if not token:
        raise SystemExit(
            "INTAKE_TOKEN is not set — the intake endpoint is outward-facing and must not "
            "run open. Set a strong shared token (see .env.example)."
        )
    import uvicorn  # lazy: [intake] extra

    uvicorn.run(
        create_app(token=token),
        host=os.environ.get("INTAKE_HOST", "0.0.0.0"),
        port=int(os.environ.get("INTAKE_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
