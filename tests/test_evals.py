"""Eval-harness plumbing tests ($0, mock provider). Validates the harness/runner/report
machinery — not model quality (that needs a live run + the D5 scoring decision)."""

from pathlib import Path

from evals.harness import (
    EvalCase,
    MockProvider,
    load_cases,
    mock_payloads_from_cases,
    run_eval,
)
from orchestrator.agents.registry import get_persona
from orchestrator.agents.registry.contracts import TriageOutput
from orchestrator.projects.loader import load_profile

REPO = Path(__file__).resolve().parent.parent
PERSONA = get_persona("triage")
PROFILE = load_profile("meal-planner")


def test_triage_cases_file_loads():
    cases = load_cases(REPO / "evals" / "triage" / "cases.jsonl")
    assert len(cases) >= 5
    assert all(c.id and c.input and c.expect for c in cases)


def test_mock_run_conforms_and_passes_assertions():
    cases = load_cases(REPO / "evals" / "triage" / "cases.jsonl")
    provider = MockProvider(mock_payloads_from_cases(TriageOutput, cases))
    report = run_eval(PERSONA, PROFILE, provider, cases)

    assert report.con_rate == 1.0          # every synthesized payload is schema-valid
    assert report.assertion_pass_rate == 1.0  # payloads built from expect, so they match
    assert report.total_cost > 0            # cost flows through (haiku tier)


def test_assertion_mismatch_is_flagged():
    case = EvalCase(id="x", input="...", expect={"kind": "feature"})
    wrong = TriageOutput(kind="bug", priority="P2", needs_clarification=False, rationale="r")
    report = run_eval(PERSONA, PROFILE, MockProvider([wrong]), [case])

    result = report.results[0]
    assert result.conforms is True            # schema-valid...
    assert result.assertions == {"kind": False}  # ...but the field assertion fails
    assert result.passed is False
    assert report.assertion_pass_rate == 0.0
