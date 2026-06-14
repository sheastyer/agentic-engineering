"""Provider selection. `MODEL_PROVIDER` env var picks the backend at runtime (default
anthropic). Read here — in an activity-side module, never in workflow-imported config —
so workflows stay free of env reads (R3)."""

import os

from orchestrator.agents.provider import ModelProvider
from orchestrator.agents.providers.anthropic_provider import AnthropicProvider
from orchestrator.agents.providers.vercel_provider import VercelGatewayProvider
from orchestrator.shared.config import DEFAULT_MODEL_PROVIDER

_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "vercel": VercelGatewayProvider,
}


def build_provider(name: str | None = None) -> ModelProvider:
    """Return the selected provider. Precedence: explicit arg → MODEL_PROVIDER env →
    DEFAULT_MODEL_PROVIDER. Construction is cheap and lazy — no SDK/client built until
    the first call."""
    resolved = (name or os.environ.get("MODEL_PROVIDER") or DEFAULT_MODEL_PROVIDER).lower()
    try:
        return _PROVIDERS[resolved]()
    except KeyError:
        raise ValueError(
            f"unknown MODEL_PROVIDER {resolved!r}; use one of {sorted(_PROVIDERS)}"
        ) from None
