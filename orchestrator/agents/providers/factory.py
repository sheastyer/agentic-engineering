"""Provider selection — the reasoning plane is **Vercel AI Gateway only** (decided
2026-07-02: one provider per plane; the coding plane draws on the Claude subscription via
the Agent SDK, see agents/coding). Kept as a factory so evals/tests can name a provider
explicitly ("vercel", or their own injected fake elsewhere); anything unknown fails loudly
rather than silently falling back."""

from orchestrator.agents.provider import ModelProvider
from orchestrator.agents.providers.vercel_provider import VercelGatewayProvider

_PROVIDERS = {
    "vercel": VercelGatewayProvider,
}


def build_provider(name: str | None = None) -> ModelProvider:
    """Return the selected provider (default: vercel — the only reasoning backend).
    Construction is cheap and lazy — no SDK/client built until the first call."""
    resolved = (name or "vercel").lower()
    try:
        return _PROVIDERS[resolved]()
    except KeyError:
        raise ValueError(
            f"unknown provider {resolved!r}; the reasoning plane is vercel-only "
            f"(use one of {sorted(_PROVIDERS)})"
        ) from None
