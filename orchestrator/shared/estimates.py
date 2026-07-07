"""Deterministic coding-cost estimator for the pre-pod coding-budget gate.

Pure arithmetic over the story plan's tiers and the CODING_EST_* heuristics in config —
no I/O, no models. Called activity-side (both the stub and the agent-backed twin of
`estimate_coding_budget` use it), so the workflow only ever sees the resulting numbers
through an activity result and stays trivially deterministic.
"""

from collections import Counter

from orchestrator.shared.config import (
    CODING_EST_BASE_USD,
    CODING_EST_STORY_USD,
    MAX_CI_FIX_PASSES,
    MAX_REVIEW_PASSES,
)

_TIER_ORDER = {"haiku": 0, "sonnet": 1, "opus": 2}


def estimate_coding_run(stories) -> tuple[float, list[str]]:
    """Estimate ONE coding pass over `stories` (anything with a `.tier` attribute).

    Returns ``(usd, breakdown)`` — the dollar estimate and the human-readable lines for
    the gate card. An unknown/empty tier is priced as sonnet (the default implementer);
    an empty story list is priced as one sonnet-ish session (the plan still runs one
    coding pass — e.g. legacy/stub plans without stories)."""
    tiers = [
        (getattr(s, "tier", "") or "sonnet")
        if (getattr(s, "tier", "") or "sonnet") in CODING_EST_STORY_USD
        else "sonnet"
        for s in stories
    ] or ["sonnet"]
    estimate = round(CODING_EST_BASE_USD + sum(CODING_EST_STORY_USD[t] for t in tiers), 2)

    counts = Counter(tiers)
    mix = " + ".join(
        f"{counts[t]}× {t} (${CODING_EST_STORY_USD[t]:.2f})"
        for t in sorted(counts, key=lambda t: _TIER_ORDER.get(t, 1))
    )
    # Revise passes (review/CI fix loops) each re-run a full coding pass at the same cap,
    # so the funded amount can be drawn again — worst case is the product of the caps (§10).
    worst_passes = 1 + MAX_REVIEW_PASSES + MAX_CI_FIX_PASSES
    breakdown = [
        f"estimated coding cost: ${estimate:.2f}",
        f"{len(tiers)} stor{'y' if len(tiers) == 1 else 'ies'}: {mix}"
        f" + ${CODING_EST_BASE_USD:.2f} session overhead",
        f"worst case if review/CI fix loops fire ({worst_passes} coding passes):"
        f" ${estimate * worst_passes:.2f}",
    ]
    return estimate, breakdown
