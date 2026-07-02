"""Eval harness (M3 infra).

Runs a persona over a case set and reports the decision-free signals:
  • CON  — schema conformance: did the runner produce a contract-valid payload?
  • assertions — deterministic field checks from each case's `expect` (NOT a quality judge)
  • cost — real dollar cost per case and in aggregate

The subjective quality dimension (LLM-as-judge, pass-rate thresholds) is intentionally a
**pluggable hook** (`QualityScorer`) left unimplemented until decision D5 sets the bar and
judge approach. The harness is provider-agnostic: pass a mock provider for $0 plumbing,
or the real one (vercel) for a live run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, get_args, get_origin

from pydantic import BaseModel

from orchestrator.agents.persona import Persona
from orchestrator.agents.provider import ProviderResponse
from orchestrator.agents.runner import AgentRunner
from orchestrator.projects.profile import ProjectProfile
from orchestrator.shared.config import PRICING
from orchestrator.shared.errors import NonRetryableAgentError

# A quality scorer maps (case, payload) -> score in [0,1] or None if not applicable.
# Left unwired pending D5 (thresholds + assertions-vs-LLM-judge decision).
QualityScorer = Callable[["EvalCase", BaseModel], float | None]


@dataclass
class EvalCase:
    id: str
    input: str               # the user-content string the persona receives
    expect: dict[str, Any]   # field -> expected value, asserted deterministically


@dataclass
class CaseResult:
    id: str
    conforms: bool                       # CON: produced a schema-valid payload
    assertions: dict[str, bool]          # per-field deterministic checks
    passed: bool                         # conforms AND all assertions
    cost_usd: float
    model: str
    quality: float | None = None         # filled only if a QualityScorer is supplied
    error: str | None = None


@dataclass
class EvalReport:
    persona: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def con_rate(self) -> float:
        return _frac(r.conforms for r in self.results)

    @property
    def assertion_pass_rate(self) -> float:
        return _frac(r.passed for r in self.results)

    @property
    def total_cost(self) -> float:
        return round(sum(r.cost_usd for r in self.results), 6)

    @property
    def mean_cost(self) -> float:
        return round(self.total_cost / self.n, 6) if self.n else 0.0


def _assert_field(actual: Any, spec: Any) -> bool:
    """Evaluate one `expect` entry against the produced field value.

    A scalar spec is exact-equality (the original behavior — covers enums/bools/ints). A
    dict spec is an operator map for free-text fields where `==` doesn't fit (PRD prose,
    injection-resistance). Supported ops (string compares are case-insensitive):
      equals · contains · not_contains · contains_any · in · min_len · min_items · max_items
    `contains`/`not_contains` accept a string or a list (all/none must match resp.);
    `min_len` is string length, `min_items` is collection length.
    """
    if not isinstance(spec, dict):
        return actual == spec
    text = "" if actual is None else str(actual)
    low = text.lower()
    for op, arg in spec.items():
        if op == "equals" and actual != arg:
            return False
        if op == "contains":
            needles = arg if isinstance(arg, list) else [arg]
            if not all(str(n).lower() in low for n in needles):
                return False
        if op == "not_contains":
            needles = arg if isinstance(arg, list) else [arg]
            if any(str(n).lower() in low for n in needles):
                return False
        if op == "contains_any" and not any(str(n).lower() in low for n in arg):
            return False
        if op == "in" and actual not in arg:
            return False
        if op == "min_len" and len(text) < arg:
            return False
        if op == "min_items" and len(actual or []) < arg:
            return False
        if op == "max_items" and len(actual or []) > arg:
            return False
    return True


def load_cases(path: str | Path) -> list[EvalCase]:
    """Read a JSONL case file: one {id, input, expect} object per line."""
    cases = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        cases.append(EvalCase(id=obj["id"], input=obj["input"], expect=obj.get("expect", {})))
    return cases


def run_eval(
    persona: Persona,
    profile: ProjectProfile,
    provider: Any,
    cases: list[EvalCase],
    *,
    quality_scorer: QualityScorer | None = None,
) -> EvalReport:
    report = EvalReport(persona=persona.name)
    for case in cases:
        try:
            result = AgentRunner(provider).run(persona, profile, case.input)
        except NonRetryableAgentError as exc:
            report.results.append(
                CaseResult(case.id, conforms=False, assertions={}, passed=False,
                           cost_usd=0.0, model="", error=str(exc))
            )
            continue
        checks = {k: _assert_field(getattr(result.payload, k, None), v)
                  for k, v in case.expect.items()}
        quality = quality_scorer(case, result.payload) if quality_scorer else None
        report.results.append(
            CaseResult(
                id=case.id,
                conforms=True,
                assertions=checks,
                passed=all(checks.values()),
                cost_usd=result.cost_usd,
                model=result.model,
                quality=quality,
            )
        )
    return report


# --- mock provider (for $0 plumbing runs) -------------------------------------
class MockProvider:
    """Returns pre-built schema-valid payloads in order — exercises the harness/runner
    without a network call. Use `mock_payloads_from_cases` to synthesize them."""

    name = "mock"

    def __init__(self, payloads: list[BaseModel], tokens: tuple[int, int] = (50, 20)) -> None:
        self._payloads = payloads
        self._i = 0
        self._tokens = tokens

    def generate_structured(self, *, tier, **_) -> ProviderResponse:
        payload = self._payloads[self._i]
        self._i += 1
        return ProviderResponse(payload, PRICING[tier]["model"], self._tokens[0], self._tokens[1])


def mock_payloads_from_cases(output_model: type[BaseModel], cases: list[EvalCase]) -> list[BaseModel]:
    """Build one schema-valid payload per case from its `expect` (missing required fields
    filled with type-appropriate placeholders). Mock mode only."""
    return [_build_payload(output_model, c.expect) for c in cases]


def _build_payload(model: type[BaseModel], expect: dict[str, Any]) -> BaseModel:
    data = {}
    for name, fld in model.model_fields.items():
        if name in expect:
            data[name] = _mock_value(expect[name], fld.annotation)
        else:
            data[name] = _default_for(fld.annotation)
    return model(**data)


def _mock_value(spec: Any, annotation: Any) -> Any:
    """Synthesize a field value that satisfies a mock case's `expect`. Scalars are used
    directly; operator dicts get a best-effort satisfying value (so $0 plumbing runs stay
    green for free-text personas too)."""
    if not isinstance(spec, dict):
        return spec
    if "equals" in spec:
        return spec["equals"]
    if "in" in spec:
        return spec["in"][0]
    if "min_items" in spec or "max_items" in spec:
        # Generate min_items (or one) elements — within any max_items ceiling for sane specs.
        return [_default_item(annotation) for _ in range(spec.get("min_items", 1))]
    needles = []
    for op in ("contains", "contains_any"):
        if op in spec:
            arg = spec[op]
            needles += arg if isinstance(arg, list) else [arg]
    if needles or "min_len" in spec:
        base = " ".join(str(n) for n in needles)
        pad = spec.get("min_len", 0) - len(base)
        return base + (" " + "x" * pad if pad > 0 else "")  # satisfy contains_* AND min_len
    return _default_for(annotation)


def _default_item(annotation: Any) -> Any:
    """A single mock element for a collection field. If the collection holds a nested model
    (e.g. list[PlannedStory]), synthesize a schema-valid sub-instance; else a placeholder."""
    args = get_args(annotation)
    inner = args[0] if args else str
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return _build_payload(inner, {})
    return _default_for(inner)


def _default_for(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Literal:
        return get_args(annotation)[0]
    if origin in (list, tuple, set):
        return []
    if origin is dict:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _build_payload(annotation, {})  # nested model -> recurse
    if annotation is bool:
        return False
    if annotation is int:
        return 1  # 1 (not 0) satisfies common ge=1 / positive-int constraints (e.g. estimate)
    if annotation is float:
        return 0.0
    return "(mock)"


def _frac(bools) -> float:
    items = list(bools)
    return round(sum(1 for b in items if b) / len(items), 4) if items else 0.0
