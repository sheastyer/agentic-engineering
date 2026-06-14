"""M2 unit tests ($0): persona registry, Project Profile loader, providers, Agent Runner.

The runner is exercised against a fake ModelProvider, and each provider against a fake
backend client — no network, no tokens. Covers the M2 `CON` (output-contract) checks, the
provider abstraction, and the runner's bounded-re-ask path.
"""

from types import SimpleNamespace

import pytest

from orchestrator.agents.provider import ProviderResponse
from orchestrator.agents.providers.anthropic_provider import AnthropicProvider
from orchestrator.agents.providers.factory import build_provider
from orchestrator.agents.providers.vercel_provider import VercelGatewayProvider
from orchestrator.agents.registry import get_persona
from orchestrator.agents.registry.contracts import TriageOutput
from orchestrator.agents.runner import AgentRunner
from orchestrator.projects.loader import load_profile
from orchestrator.projects.profile import (
    Deploy,
    DeployKind,
    Intake,
    IntakeKind,
    ProjectProfile,
    Repo,
    Stack,
)
from orchestrator.shared.errors import NonRetryableAgentError

PROFILE = load_profile("meal-planner")


# --- registry ------------------------------------------------------------------
def test_registry_resolves_and_rejects_unknown():
    assert get_persona("triage").tier == "haiku"
    assert get_persona("pm_draft_brief").tier == "opus"
    with pytest.raises(KeyError):
        get_persona("nope")


# --- project profile -----------------------------------------------------------
def test_profile_loads_and_validates():
    assert PROFILE.id == "meal-planner"
    assert PROFILE.stack.languages == ["typescript"]
    assert PROFILE.intake.kind is IntakeKind.DB_TABLE


def test_profile_loader_rejects_unknown():
    with pytest.raises(KeyError):
        load_profile("does-not-exist")


def test_profile_validation_rejects_inline_secret():
    # Assemble the key marker at runtime so the literal never appears in source (the R4
    # secret scan would otherwise flag this file).
    inline_value = "sk-" + "ant-" + "deadbeefcafe1234"
    bad = ProjectProfile(
        id="x", name="X", description="d",
        repo=Repo("git@x", "main"),
        stack=Stack(["py"], "pip", "pytest"),
        intake=Intake(IntakeKind.MANUAL),
        deploy=Deploy(DeployKind.MERGE),
        secret_refs={"key": inline_value},  # a value, not an env-var name
    )
    with pytest.raises(ValueError):
        bad.validate()


# --- agent runner (provider-agnostic) -----------------------------------------
class _FakeProvider:
    name = "fake"

    def __init__(self, payload, in_tok, out_tok, model_id="claude-haiku-4-5", cache=0):
        self._resp = ProviderResponse(payload, model_id, in_tok, out_tok, cache)
        self.calls = []

    def generate_structured(self, **kwargs):
        self.calls.append(kwargs)
        return self._resp


def test_runner_returns_validated_payload_and_dollar_cost():
    parsed = TriageOutput(kind="bug", priority="P1", needs_clarification=False, rationale="ok")
    provider = _FakeProvider(parsed, 1000, 200)  # haiku $1/$5 per 1M
    result = AgentRunner(provider).run(get_persona("triage"), PROFILE, "app crashes")

    assert isinstance(result.payload, TriageOutput)
    assert result.model == "claude-haiku-4-5"
    assert result.cost_usd == pytest.approx(0.002)  # 1000×$1/1e6 + 200×$5/1e6
    # Project context injected into the system prompt; correct tier passed through.
    assert provider.calls[0]["tier"] == "haiku"
    assert "Meal Planner" in provider.calls[0]["system"]


def test_runner_bounded_reask_then_hard_fail():
    provider = _FakeProvider(payload=None, in_tok=100, out_tok=10)  # never parses
    with pytest.raises(NonRetryableAgentError):
        AgentRunner(provider).run(get_persona("triage"), PROFILE, "x")
    assert len(provider.calls) == 2  # triage max_reask=1 -> 2 attempts


# --- anthropic provider --------------------------------------------------------
def _usage(inp, out, cache=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache)


def test_anthropic_provider_sets_thinking_effort_for_reasoning_tiers_only():
    parsed = TriageOutput(kind="feature", priority="P2", needs_clarification=False, rationale="r")

    class FakeMessages:
        def __init__(self):
            self.calls = []

        def parse(self, **kw):
            self.calls.append(kw)
            return SimpleNamespace(parsed_output=parsed, usage=_usage(10, 5))

    fm = FakeMessages()
    provider = AnthropicProvider(messages_client=fm)
    msgs = [{"role": "user", "content": "x"}]

    provider.generate_structured(tier="opus", system="s", messages=msgs,
                                 output_model=TriageOutput, effort="high", max_tokens=100)
    assert fm.calls[-1]["model"] == "claude-opus-4-8"
    assert fm.calls[-1]["thinking"] == {"type": "adaptive"}
    assert fm.calls[-1]["output_config"]["effort"] == "high"

    provider.generate_structured(tier="haiku", system="s", messages=msgs,
                                 output_model=TriageOutput, effort="low", max_tokens=100)
    assert fm.calls[-1]["model"] == "claude-haiku-4-5"
    assert "thinking" not in fm.calls[-1]
    assert "output_config" not in fm.calls[-1]


# --- vercel gateway provider ---------------------------------------------------
def test_vercel_provider_builds_openai_request_and_validates_content():
    payload_json = TriageOutput(
        kind="bug", priority="P0", needs_clarification=False, rationale="r"
    ).model_dump_json()

    class FakeCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kw):
            self.calls.append(kw)
            message = SimpleNamespace(content=payload_json)
            usage = SimpleNamespace(prompt_tokens=120, completion_tokens=30)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    provider = VercelGatewayProvider(client=fake)
    resp = provider.generate_structured(
        tier="haiku", system="s", messages=[{"role": "user", "content": "x"}],
        output_model=TriageOutput, effort="low", max_tokens=100,
    )

    assert isinstance(resp.payload, TriageOutput) and resp.payload.kind == "bug"
    assert resp.model_id == "anthropic/claude-haiku-4.5"  # gateway-namespaced (dot versioning)
    assert resp.input_tokens == 120 and resp.output_tokens == 30
    call = fake.chat.completions.calls[0]
    assert call["model"] == "anthropic/claude-haiku-4.5"
    assert call["response_format"]["type"] == "json_schema"
    assert call["messages"][0]["role"] == "system"  # system folded into messages


# --- provider factory ----------------------------------------------------------
def test_factory_selects_provider_and_rejects_unknown():
    assert build_provider("anthropic").name == "anthropic"
    assert build_provider("vercel").name == "vercel"
    with pytest.raises(ValueError):
        build_provider("nope")


# --- runner-backed triage activity (M3 swap target) ----------------------------
def test_triage_activity_adapts_contract_to_workflow_type():
    from orchestrator.activities.agent_backed import triage_with_runner
    from orchestrator.shared.types import FeedbackKind, Triage

    parsed = TriageOutput(kind="feature", priority="P2", needs_clarification=False, rationale="r")
    provider = _FakeProvider(parsed, 1000, 200)  # haiku
    event = SimpleNamespace(
        id="x", kind=FeedbackKind.FEATURE, title="add button",
        body="please", submitted_by="t", project="meal-planner",
    )
    result = triage_with_runner(provider, event)

    assert isinstance(result, Triage)
    assert result.kind is FeedbackKind.FEATURE
    assert result.priority == "P2"
    assert result.cost_usd == pytest.approx(0.002)  # real dollar cost flows through
