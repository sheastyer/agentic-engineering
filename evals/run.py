"""Eval CLI.

    # $0 plumbing run (synthesizes schema-valid payloads from each case's expect):
    ./.venv/bin/python -m evals.run --persona triage --provider mock

    # live run (needs provider auth; spends a few cents):
    MODEL_PROVIDER=anthropic ./.venv/bin/python -m evals.run --persona triage --provider anthropic

Reports CON (schema conformance), deterministic field-assertion pass rate, and dollar
cost. Quality (LLM-judge) scoring is not wired yet — pending decision D5.
"""

import argparse
import sys
from pathlib import Path

from evals.harness import (
    MockProvider,
    load_cases,
    mock_payloads_from_cases,
    run_eval,
)
from orchestrator.agents.providers.factory import build_provider
from orchestrator.agents.registry import get_persona
from orchestrator.projects.loader import load_profile

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a persona eval set.")
    parser.add_argument("--persona", default="triage")
    parser.add_argument("--provider", default="mock", help="mock | anthropic | vercel")
    parser.add_argument("--project", default="meal-planner")
    parser.add_argument("--cases", default=None, help="path to cases.jsonl")
    parser.add_argument("--min-pass", type=float, default=1.0,
                        help="min assertion pass-rate for exit 0 (D5 will set real bars)")
    args = parser.parse_args()

    persona = get_persona(args.persona)
    profile = load_profile(args.project)
    cases_path = Path(args.cases) if args.cases else REPO / "evals" / args.persona / "cases.jsonl"
    cases = load_cases(cases_path)

    if args.provider == "mock":
        provider = MockProvider(mock_payloads_from_cases(persona.output_model, cases))
    else:
        provider = build_provider(args.provider)

    report = run_eval(persona, profile, provider, cases)

    print(f"\neval: {report.persona}  ·  provider: {args.provider}  ·  {report.n} cases\n")
    print(f"  {'case':<22} {'CON':<5} {'assert':<7} {'cost($)':<9} model")
    print("  " + "-" * 60)
    for r in report.results:
        con = "ok" if r.conforms else "FAIL"
        asrt = "ok" if r.passed else ("—" if not r.conforms else "FAIL")
        fails = "" if r.passed or not r.conforms else \
            "  ← " + ", ".join(k for k, ok in r.assertions.items() if not ok)
        print(f"  {r.id:<22} {con:<5} {asrt:<7} {r.cost_usd:<9.5f} {r.model}{fails}")

    print("  " + "-" * 60)
    print(f"  CON rate: {report.con_rate:.0%}   assertion pass: {report.assertion_pass_rate:.0%}"
          f"   total: ${report.total_cost:.4f}   mean: ${report.mean_cost:.5f}")
    print("  (quality/LLM-judge scoring pending decision D5)\n")

    ok = report.con_rate == 1.0 and report.assertion_pass_rate >= args.min_pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
