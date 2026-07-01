"""Vercel AI Gateway provider — bring-your-own backend.

Talks to the gateway's OpenAI-compatible Chat Completions endpoint
(https://ai-gateway.vercel.sh/v1) via the `openai` Python SDK with a swapped base_url.
Structured outputs use the OpenAI `response_format: json_schema` shape; the gateway
returns the JSON as message content, which we validate against the persona's Pydantic
contract ourselves. Tiers map to gateway-namespaced model ids (anthropic/claude-…) — same
Claude models for now.

Auth: AI_GATEWAY_API_KEY (or VERCEL_OIDC_TOKEN). The `openai` client is injectable for $0
testing and lazily imported so the org never hard-depends on it.

Schema hardening (see issue: code_reviewer systematically failed to parse here): without
`strict: true`, OpenAI-compatible `json_schema` mode is a hint, not grammar-constrained
decoding, so a model asked for a richer contract (a list field, a conditional invariant)
can drift — prose before/after the JSON, markdown fences, or an omitted-but-required key.
`_strict_schema` makes every contract satisfy OpenAI's strict-mode rules (every property
required, no nested `additionalProperties` gaps, no bare `default`) and the request opts
into `strict: true`; `_extract_json` tolerates a fenced or prose-wrapped response as a
second line of defense. On a genuine parse miss the raw content is logged so a future
failure is diagnosable instead of a silent None.

Caveats (documented, not silently assumed): effort/adaptive-thinking are Anthropic-native
and not forwarded here; cache-token reporting via the gateway isn't relied on (treated as
0 unless present).
"""

import logging
import os
import re
from typing import Any

from pydantic import BaseModel

from orchestrator.agents.provider import ProviderResponse, usage_int
from orchestrator.shared.config import VERCEL_GATEWAY_BASE_URL, VERCEL_MODELS
from orchestrator.shared.errors import AuthError

_log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strict_schema(node: Any) -> Any:
    """Recursively rewrite a pydantic JSON schema to satisfy OpenAI strict-mode rules:
    every property is required (optional fields become nullable instead of omittable)
    and every object forbids additional properties. Strict schemas also reject a bare
    `default` keyword, so those are stripped too."""
    if isinstance(node, list):
        for item in node:
            _strict_schema(item)
        return node
    if not isinstance(node, dict):
        return node

    node.pop("default", None)
    if node.get("type") == "object" or "properties" in node:
        props = node.get("properties", {})
        node["required"] = list(props.keys())
        node["additionalProperties"] = False
        for value in props.values():
            _strict_schema(value)
    for key in ("$defs", "definitions"):
        for value in node.get(key, {}).values():
            _strict_schema(value)
    for key in ("items", "anyOf", "allOf", "oneOf"):
        if key in node:
            _strict_schema(node[key])
    return node


def _extract_json(content: str) -> str:
    """Tolerate a fenced or prose-wrapped response: unwrap a ```json fence if present,
    else take the outermost {...} substring. A best-effort second line of defense behind
    the strict schema — the runner still re-asks (bounded) if this doesn't parse."""
    content = content.strip()
    fenced = _FENCE_RE.search(content)
    if fenced:
        content = fenced.group(1).strip()
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end != -1 and end > start:
        content = content[start : end + 1]
    return content


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
        schema = _strict_schema(output_model.model_json_schema())
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": output_model.__name__,
                "schema": schema,
                "strict": True,
            },
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
            payload: BaseModel | None = output_model.model_validate_json(_extract_json(content))
        except Exception:
            payload = None  # runner re-asks (bounded)
            _log.warning(
                "vercel provider: %s produced unparseable output for %s (tier=%s); raw "
                "content: %.2000s",
                model, output_model.__name__, tier, content,
            )

        usage = getattr(resp, "usage", None)
        return ProviderResponse(
            payload=payload,
            model_id=model,
            input_tokens=usage_int(usage, "prompt_tokens", "input_tokens"),
            output_tokens=usage_int(usage, "completion_tokens", "output_tokens"),
            cache_read_tokens=usage_int(usage, "prompt_tokens_details.cached_tokens"),
        )
