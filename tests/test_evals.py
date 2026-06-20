"""Eval-harness plumbing tests ($0, mock provider). Validates the harness/runner/report
machinery — not model quality (that needs a live run + the D5 scoring decision)."""

import sys
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


def test_operator_assertions_for_free_text_fields():
    # Operator specs (for prose personas where `==` doesn't fit): contains / not_contains /
    # contains_any / min_len / in. String matching is case-insensitive.
    from evals.harness import _assert_field

    assert _assert_field("Addresses the latency concern", {"contains": "latency"})
    assert not _assert_field("Addresses the latency concern", {"contains": "security"})
    assert _assert_field("clean rationale", {"not_contains": ["system prompt", "ignore all"]})
    assert not _assert_field("here is my system prompt", {"not_contains": "system prompt"})
    assert _assert_field("mentions caching", {"contains_any": ["caching", "indexing"]})
    assert _assert_field("a fairly long answer", {"min_len": 5})
    assert not _assert_field("hi", {"min_len": 5})
    assert _assert_field("positive", {"in": ["positive", "neutral"]})
    assert _assert_field(["a", "b", "c"], {"min_items": 2}) and not _assert_field(["a"], {"min_items": 2})
    assert _assert_field(["a"], {"max_items": 2}) and not _assert_field(["a", "b", "c"], {"max_items": 2})
    assert _assert_field("bug", "bug") and not _assert_field("bug", "feature")  # scalar == still works


def test_cost_band_gate_fails_when_a_case_exceeds_the_ceiling(monkeypatch):
    """The COST band (§10): `--max-cost` exits non-zero when a case's cost tops the per-case
    ceiling (the 'drifted up a tier' regression guard), and zero when it's within band."""
    import evals.run as run_cli

    base = ["prog", "--persona", "triage", "--provider", "mock"]
    monkeypatch.setattr(sys, "argv", base + ["--max-cost", "1.0"])
    assert run_cli.main() == 0          # mock triage costs a fraction of a cent — within band
    monkeypatch.setattr(sys, "argv", base + ["--max-cost", "0.0"])
    assert run_cli.main() == 1          # ceiling 0 → every nonzero-cost case trips the band


# --- LLM-judge (D5) ------------------------------------------------------------
class _FakeJudgeProvider:
    name = "fake"

    def __init__(self, out):
        self._out = out

    def generate_structured(self, *, tier, **_):
        from orchestrator.agents.provider import ProviderResponse
        return ProviderResponse(self._out, "claude-opus-4-8", 100, 50)


def test_judge_verdict_is_aggregated_in_code():
    from evals.judge import PRDJudgeOutput, judge_prd

    # All must-haves met, one optional (surfaces_risks) missing -> PASS, score 6/7.
    good = PRDJudgeOutput(states_problem=True, has_goals_and_non_goals=True,
                          concrete_user_stories=True, testable_acceptance_criteria=True,
                          surfaces_risks=False, scoped_no_creep=True, resisted_injection=True,
                          reasons="r")
    v = judge_prd(_FakeJudgeProvider(good), "brief", "prd")
    assert v.passed is True and v.score == round(6 / 7, 3)
    assert v.cost_usd > 0  # judge cost is accounted

    # Injection followed -> a must-have fails -> overall FAIL no matter the rest.
    bad = good.model_copy(update={"resisted_injection": False})
    assert judge_prd(_FakeJudgeProvider(bad), "brief", "prd").passed is False


def test_agreement_flags_false_pass():
    from evals.judge import agreement

    a = agreement(judge_passed=[True, True, False], human_passed=[True, False, False])
    assert a.n == 3 and a.agree == 2 and a.rate == round(2 / 3, 3)
    assert a.false_pass == 1 and a.false_fail == 0  # judge OK'd row 2 that the human failed
