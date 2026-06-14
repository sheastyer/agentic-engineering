"""Anthropic provider — the default 'Claude' backend (the subscription path).

Uses the Anthropic Messages SDK with native structured outputs (`messages.parse`).
Credentials resolve via the SDK chain: ANTHROPIC_API_KEY → ANTHROPIC_AUTH_TOKEN →
`ant auth login` OAuth profile (your $20/mo subscription credit). Adaptive thinking +
effort are applied for the sonnet/opus tiers; Haiku gets neither (it would 400).

The messages client is injectable for $0 testing; `anthropic` is imported lazily so the
rest of the org never needs it.
"""

from typing import Any

from pydantic import BaseModel

from orchestrator.agents.provider import ProviderResponse, usage_int
from orchestrator.shared.config import PRICING

_REASONING_TIERS = {"sonnet", "opus"}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, messages_client: Any = None) -> None:
        self._messages = messages_client

    def _client(self) -> Any:
        if self._messages is None:
            import anthropic  # lazy: only the live path needs the SDK

            self._messages = anthropic.Anthropic().messages
        return self._messages

    def generate_structured(
        self, *, tier, system, messages, output_model: type[BaseModel], effort, max_tokens
    ) -> ProviderResponse:
        model = PRICING[tier]["model"]
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "output_format": output_model,
        }
        if tier in _REASONING_TIERS:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": effort}

        resp = self._client().parse(**kwargs)
        usage = getattr(resp, "usage", None)
        payload = getattr(resp, "parsed_output", None)
        return ProviderResponse(
            payload=payload if isinstance(payload, output_model) else None,
            model_id=model,
            input_tokens=usage_int(usage, "input_tokens"),
            output_tokens=usage_int(usage, "output_tokens"),
            cache_read_tokens=usage_int(usage, "cache_read_input_tokens"),
        )
