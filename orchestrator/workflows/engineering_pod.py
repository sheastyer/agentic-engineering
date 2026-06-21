"""EngineeringPodWorkflow — the coding child (CLAUDE.md §7, §8).

A feature is implemented by a **single coding agent working the architect's stories in
order, in one workspace** — so it lands as one coherent diff (no parallel agents producing
conflicting diffs against separate clones, and no partial feature from coding only the
first story). The orchestrator (this workflow) then runs QA, with one bounded QA->fix pass
(MAX_QA_FIX_PASSES), then a bounded **code-review -> revise loop** (MAX_REVIEW_PASSES): a
reasoning-plane reviewer critiques the diff and the developer (coding pod) revises against
the feedback, so the PR is opened only after it has been reviewed and iterated on. Deploy is
NOT here — it sits behind the parent's human approval gate (§9.2), which is where the
already-reviewed PR is surfaced to the human.
"""

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities import stubs as act
    from orchestrator.shared.config import (
        CODING_ACTIVITY_TIMEOUT_MINUTES,
        MAX_QA_FIX_PASSES,
        MAX_REVIEW_PASSES,
    )
    from orchestrator.shared.types import PodResult, StoryPlan
    from orchestrator.workflows.common import run_activity

# Coding + PR-open activities run a real agent and the target's tests in a sandbox — give
# them minutes, not the 30s reasoning default. Deterministic (a constant timedelta).
_CODING_TIMEOUT = timedelta(minutes=CODING_ACTIVITY_TIMEOUT_MINUTES)


@workflow.defn
class EngineeringPodWorkflow:
    @workflow.run
    async def run(self, plan: StoryPlan) -> PodResult:
        # Costs accrue across the (possibly looping) stages, so accumulate as we go rather than
        # summing only the final result — a coding/review pass we later supersede still spent.
        cost = 0
        cost_usd = 0.0

        def _spend(*stage_results) -> None:
            nonlocal cost, cost_usd
            for r in stage_results:
                cost += r.cost_tokens
                cost_usd += r.cost_usd

        # One agent implements the whole ordered story plan in a single workspace.
        result = await run_activity(act.implement_stories, plan, timeout=_CODING_TIMEOUT)
        qa = await run_activity(act.qa_review, [result])
        _spend(result, qa)

        # Bounded QA -> fix loop: re-run the implementation once if QA failed (§10 cap).
        fixes = 0
        while not qa.passed and fixes < MAX_QA_FIX_PASSES:
            fixes += 1
            result = await run_activity(act.implement_stories, plan, timeout=_CODING_TIMEOUT)
            qa = await run_activity(act.qa_review, [result])
            _spend(result, qa)

        # Bounded code-review -> revise loop, BEFORE the PR opens (§10 cap). A reasoning-plane
        # reviewer critiques the diff; if it requires changes, the developer (coding pod) revises
        # against that feedback and we re-QA + re-review. Each revise pass is a full coding run on
        # the subscription, so MAX_REVIEW_PASSES is a hard cost lever. The human only ever sees a
        # PR that has already been through this loop.
        review = await run_activity(act.review_diff, plan, result)
        _spend(review)
        reviews = 0
        while not review.approved and reviews < MAX_REVIEW_PASSES:
            reviews += 1
            result = await run_activity(
                act.revise_after_review, plan, result, review, timeout=_CODING_TIMEOUT
            )
            qa = await run_activity(act.qa_review, [result])
            review = await run_activity(act.review_diff, plan, result)
            _spend(result, qa, review)

        # Open the PR from the agent's (reviewed) diff — the pod's terminal artifact. Deploy is
        # NOT here — it sits behind the parent's human approval gate (§9.2). The branch carries a
        # per-run tag (from the deterministic workflow id) so re-runs don't collide on the
        # remote — replay-safe because workflow_id is fixed for a given execution.
        run_tag = workflow.info().workflow_id.removesuffix("-pod").split("-")[-1]
        branch = f"agentic/{plan.feature_id}-{run_tag}"
        pr = await run_activity(
            act.open_pr, plan.project, branch, [result], review.notes, timeout=_CODING_TIMEOUT
        )
        _spend(pr)

        return PodResult(
            story_result=result,
            qa=qa,
            branch=branch,
            pr_url=pr.url,
            review_approved=review.approved,
            review_notes=review.notes,
            cost_tokens=cost,
            cost_usd=cost_usd,
        )
