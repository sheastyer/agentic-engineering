"""Eval CLI.

    # $0 plumbing run (synthesizes schema-valid payloads from each case's expect):
    ./.venv/bin/python -m evals.run --persona triage --provider mock

    # live run (needs provider auth; spends a few cents):
    MODEL_PROVIDER=anthropic ./.venv/bin/python -m evals.run --persona triage --provider anthropic

Reports CON (schema conformance), deterministic field-assertion pass rate, and dollar
cost. For subjective personas (e.g. pm_write_prd), pass `--judge` to also gate on the
LLM-judge's must-have criteria (D5: judge calibrated against human labels, 0 false-pass).

    # PRD-authoring with the judge gate (CON + assertions + judge must-haves):
    set -a; . ./.env; set +a; MODEL_PROVIDER=vercel \
        ./.venv/bin/python -m evals.run --persona pm_write_prd --provider vercel --judge
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
    parser.add_argument("--max-cost", type=float, default=None,
                        help="COST band: per-case dollar ceiling. Exit non-zero if any case "
                             "exceeds it — catches a persona drifting up a tier (e.g. triage "
                             "onto Opus). Set per persona from its observed tier-appropriate cost.")
    parser.add_argument("--judge", action="store_true",
                        help="also gate on the LLM-judge's must-have criteria (subjective "
                             "personas like pm_write_prd; needs a live provider). See evals/judge.py.")
    args = parser.parse_args()

    persona = get_persona(args.persona)
    profile = load_profile(args.project)
    cases_path = Path(args.cases) if args.cases else REPO / "evals" / args.persona / "cases.jsonl"
    cases = load_cases(cases_path)

    if args.provider == "mock":
        provider = MockProvider(mock_payloads_from_cases(persona.output_model, cases))
    else:
        provider = build_provider(args.provider)

    # LLM-judge gate (D5): a per-case quality scorer that grades the produced PRD against the
    # rubric in evals/judge.py. Aggregation (must-haves -> pass) lives in the judge, not the model.
    judge_verdicts: dict = {}
    quality_scorer = None
    if args.judge:
        if args.provider == "mock":
            print("--judge needs a live provider (anthropic|vercel), not mock.")
            return 2
        from evals.judge import judge_prd

        def quality_scorer(case, payload):  # closes over provider + judge_verdicts
            content = getattr(payload, "content", None)
            if content is None:
                return None  # persona has no PRD prose to grade
            verdict = judge_prd(provider, case.input, content)
            judge_verdicts[case.id] = verdict
            return verdict.score

    report = run_eval(persona, profile, provider, cases, quality_scorer=quality_scorer)

    jcol = f" {'judge':<7}" if args.judge else ""
    print(f"\neval: {report.persona}  ·  provider: {args.provider}  ·  {report.n} cases\n")
    print(f"  {'case':<22} {'CON':<5} {'assert':<7}{jcol} {'cost($)':<9} model")
    print("  " + "-" * 60)
    for r in report.results:
        con = "ok" if r.conforms else "FAIL"
        asrt = "ok" if r.passed else ("—" if not r.conforms else "FAIL")
        jcell = ""
        if args.judge:
            v = judge_verdicts.get(r.id)
            jcell = " " + (f"{'ok' if v.passed else 'FAIL'}({v.score:.2f})" if v else "—").ljust(7)
        fails = "" if r.passed or not r.conforms else \
            "  ← " + ", ".join(k for k, ok in r.assertions.items() if not ok)
        if args.judge and (v := judge_verdicts.get(r.id)) and not v.passed:
            from evals.judge import _PRD_MUST_HAVE
            fails += "  judge: " + ", ".join(k for k, ok in v.criteria.items()
                                             if k in _PRD_MUST_HAVE and not ok)
        print(f"  {r.id:<22} {con:<5} {asrt:<7}{jcell} {r.cost_usd:<9.5f} {r.model}{fails}")

    print("  " + "-" * 60)
    # COST band: fail if any case's cost exceeds the per-case ceiling (the "drifts up a tier"
    # regression guard, §10). Reported even when it passes so the headroom is visible.
    cost_ok = True
    if args.max_cost is not None:
        over = [(r.id, r.cost_usd) for r in report.results if r.cost_usd > args.max_cost]
        cost_ok = not over
        verdict = "ok" if cost_ok else "FAIL"
        print(f"  COST band: {verdict}  (ceiling ${args.max_cost:.5f}/case; "
              f"max observed ${max((r.cost_usd for r in report.results), default=0.0):.5f})"
              + ("  ← " + ", ".join(f"{cid} ${c:.5f}" for cid, c in over) if over else ""))
    judge_ok = True
    judge_cost = 0.0
    if args.judge:
        judge_ok = bool(judge_verdicts) and all(v.passed for v in judge_verdicts.values())
        judge_cost = sum(v.cost_usd for v in judge_verdicts.values())
    print(f"  CON rate: {report.con_rate:.0%}   assertion pass: {report.assertion_pass_rate:.0%}"
          f"   total: ${report.total_cost:.4f}   mean: ${report.mean_cost:.5f}")
    if args.judge:
        passes = sum(1 for v in judge_verdicts.values() if v.passed)
        print(f"  judge: {passes}/{len(judge_verdicts)} pass must-haves   "
              f"judge cost: ${judge_cost:.4f}   combined: ${report.total_cost + judge_cost:.4f}")
    print()

    ok = (report.con_rate == 1.0 and report.assertion_pass_rate >= args.min_pass
          and judge_ok and cost_ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
