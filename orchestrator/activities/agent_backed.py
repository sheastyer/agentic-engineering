"""Runner-backed activities — the M3 swap targets (one real persona at a time).

This is the bridge between the generic Agent Runner and the workflow's plain dataclasses:
the runner returns a Pydantic contract instance; the activity adapts it into the
replay-serialized workflow type (orchestrator/shared/types.py), carrying the real dollar
cost. The core logic is a plain function so it can be unit-tested with a fake client for
$0; the @activity.defn wrapper supplies the real (lazy-built) client at runtime.

NOT yet registered in the worker's ALL_ACTIVITIES — swapping it in (under the stub's
activity name) is the M3 step, done once live auth is available. Until then the stub
remains the default so M1/M2 stay green and token-free.
"""

from temporalio import activity

from orchestrator.agents.provider import ModelProvider
from orchestrator.agents.providers.factory import build_provider
from orchestrator.agents.registry import get_persona
from orchestrator.agents.registry.contracts import TriageOutput
from orchestrator.agents.runner import AgentRunner
from orchestrator.projects.loader import load_profile
from orchestrator.shared.types import FeedbackEvent, FeedbackKind, Triage


def triage_with_runner(provider: ModelProvider, event: FeedbackEvent) -> Triage:
    """Real triage via the Agent Runner. Pure (provider injected) for $0 unit testing."""
    profile = load_profile(event.project)
    persona = get_persona("triage")
    task_input = f"Title: {event.title}\n\n{event.body}"

    result = AgentRunner(provider).run(persona, profile, task_input)
    out: TriageOutput = result.payload
    return Triage(
        kind=FeedbackKind(out.kind),
        priority=out.priority,
        needs_clarification=out.needs_clarification,
        cost_tokens=result.input_tokens + result.output_tokens,
        cost_usd=result.cost_usd,
    )


@activity.defn(name="triage_feedback")
async def triage_feedback_agent(event: FeedbackEvent) -> Triage:
    """Live triage. Provider chosen by MODEL_PROVIDER env (default anthropic = your
    subscription). Registered under the stub's name so the M3 swap is a one-liner."""
    return triage_with_runner(build_provider(), event)
