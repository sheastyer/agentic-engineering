"""Vercel AI Gateway provider — bring-your-own backend.

Talks to the gateway's OpenAI-compatible Chat Completions endpoint
(https://ai-gateway.vercel.sh/v1) via the `openai` Python SDK with a swapped base_url.
Structured outputs use the OpenAI `response_format: json_schema` shape; the gateway
returns the JSON as message content, which we validate against the persona's Pydantic
contract ourselves. Tiers map to gateway-namespaced model ids (anthropic/claude-…) — same
Claude models for now.

Auth: AI_GATEWAY_API_KEY (or VERCEL_OIDC_TOKEN). The `openai` client is injectable for $0
testing and lazily imported so the org never hard-depends on it.

Caveats (documented, not silently assumed): effort/adaptive-thinking are Anthropic-native
and not forwarded here; cache-token reporting via the gateway isn't relied on (treated as
0 unless present).
"""

import json
import os
from typing import Any

from pydantic import BaseModel

from orchestrator.agents.provider import ProviderResponse, usage_int
from orchestrator.shared.config import VERCEL_GATEWAY_BASE_URL, VERCEL_MODELS
from orchestrator.shared.errors import AuthError


class VercelGatewayProvider:
    name = "vercel"

    def __init__(self, client: Any = None, model_map: dict | None = None) -> None:
        self._client = client
        self._models = model_map or VERCEL_MODELS

    def _openai(self) -> Any:
        if self._client is None:
            import openai  # lazy

            key = os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("VERCEL_OIDC_TOKEN")
            if not key:
                raise AuthError("AI_GATEWAY_API_KEY (or VERCEL_OIDC_TOKEN) is not set")
            self._client = openai.OpenAI(api_key=key, base_url=VERCEL_GATEWAY_BASE_URL)
        return self._client

    def generate_structured(
        self, *, tier, system, messages, output_model: type[BaseModel], effort, max_tokens
    ) -> ProviderResponse:
        model = self._models[tier]
        schema = output_model.model_json_schema()
        schema.setdefault("additionalProperties", False)  # OpenAI strict json_schema
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": output_model.__name__, "schema": schema},
        }

        resp = self._openai().chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, *messages],
            response_format=response_format,
            max_tokens=max_tokens,
            stream=False,
        )
        content = resp.choices[0].message.content or ""
        try:
            payload: BaseModel | None = output_model.model_validate_json(content)
        except Exception:
            payload = None  # runner re-asks (bounded)

        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            payload=payload,
            model_id=model,
            input_tokens=usage_int(usage, "prompt_tokens", "input_tokens"),
            output_tokens=usage_int(usage, "completion_tokens", "output_tokens"),
            cache_read_tokens=usage_int(usage, "prompt_tokens_details.cached_tokens"),
        )
