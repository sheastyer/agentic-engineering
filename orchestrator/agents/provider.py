"""The model-provider abstraction (between activity calls and the actual model backend).

The Agent Runner depends only on this interface, so the org is provider-agnostic: the
default backend is the Anthropic Messages SDK (drawing on a Claude subscription or a
direct API key), but anyone can bring their own by implementing `ModelProvider` — a
Vercel AI Gateway provider ships alongside. Scope is the reasoning/completion plane;
the M4 engineering pod's coding-agent execution gets its own abstraction (two planes,
CLAUDE.md §2).

Providers return raw token usage + the concrete model id; the runner computes dollar cost
from PRICING in one place, so cost accounting stays consistent across providers.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass
class ProviderResponse:
    payload: BaseModel | None      # validated output-contract instance, or None if unparseable
    model_id: str                  # the concrete model the provider used
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0


class ModelProvider(Protocol):
    """One structured completion. Implementations map this to their backend's API."""

    name: str

    def generate_structured(
        self,
        *,
        tier: str,                 # "haiku" | "sonnet" | "opus"
        system: str,
        messages: list[dict],
        output_model: type[BaseModel],
        effort: str,
        max_tokens: int,
    ) -> ProviderResponse: ...


def usage_int(usage: Any, *attrs: str) -> int:
    """Read the first present (possibly nested) usage attribute as an int; 0 if absent.

    `usage_int(u, "input_tokens")` or, for nested OpenAI shapes,
    `usage_int(u, "prompt_tokens_details.cached_tokens")`."""
    for attr in attrs:
        obj: Any = usage
        for part in attr.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if obj is not None:
            return int(obj)
    return 0
