"""The single generic Agent Runner (CLAUDE.md §5, §6).

One runner executes any persona against any provider: render the system prompt with
injected profile context, ask the provider for a structured completion constrained to the
persona's output contract, and return the validated payload plus the real dollar cost. A
malformed response triggers a bounded re-ask; exhausting it raises NonRetryableAgentError
(a deterministic give-up, not retried). Transient backend errors propagate to the
activity's Temporal retry policy.

The runner is **provider-agnostic** — it depends only on the ModelProvider interface, so
the backend (Anthropic Messages SDK, Vercel AI Gateway, …) is a swap, not a rewrite. Cost
is computed here from token usage × tier pricing, in one place, so it's consistent across
providers (the gateway may bill with a margin — see config.PRICING).
"""

from dataclasses import dataclass

from pydantic import BaseModel

from orchestrator.agents.persona import Persona
from orchestrator.agents.provider import ModelProvider, ProviderResponse
from orchestrator.projects.profile import ProjectProfile
from orchestrator.shared.config import PRICING
from orchestrator.shared.errors import NonRetryableAgentError


@dataclass
class RunResult:
    payload: BaseModel
    cost_usd: float
    model: str
    input_tokens: int
    output_tokens: int
    reasks: int


class AgentRunner:
    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider

    def run(
        self, persona: Persona, profile: ProjectProfile, task_input: str, *, tier: str | None = None
    ) -> RunResult:
        # `tier` overrides the persona's default tier for this call only — the cost lever that
        # downgrades Opus reasoning stages to Sonnet on small features (§10). Pricing and the
        # request both use the effective tier, so cost accounting stays exact.
        effective_tier = tier or persona.tier
        system = persona.render_system(profile)
        messages: list[dict] = [{"role": "user", "content": task_input}]

        cost = 0.0
        in_tok = out_tok = 0

        for attempt in range(persona.max_reask + 1):
            resp = self._provider.generate_structured(
                tier=effective_tier,
                system=system,
                messages=messages,
                output_model=persona.output_model,
                effort=persona.effort,
                max_tokens=persona.max_tokens,
            )
            cost += _cost_usd(resp, effective_tier)
            in_tok += resp.input_tokens
            out_tok += resp.output_tokens

            if isinstance(resp.payload, persona.output_model):
                return RunResult(resp.payload, cost, resp.model_id, in_tok, out_tok, attempt)

            # Malformed/empty output: re-ask once more (bounded).
            messages = messages + [
                {"role": "assistant", "content": "(unparseable)"},
                {"role": "user", "content": "Your previous response did not match the "
                 "required schema. Respond again, strictly conforming to it."},
            ]

        raise NonRetryableAgentError(
            f"persona {persona.name!r} produced no schema-valid output after "
            f"{persona.max_reask + 1} attempts"
        )


def _cost_usd(resp: ProviderResponse, tier_name: str) -> float:
    """Dollar cost from token usage. Cache reads bill at ~0.1× input (CLAUDE.md §10)."""
    rate = PRICING[tier_name]
    return (
        resp.input_tokens * rate["input"]
        + resp.cache_read_tokens * rate["input"] * 0.1
        + resp.output_tokens * rate["output"]
    ) / 1_000_000
