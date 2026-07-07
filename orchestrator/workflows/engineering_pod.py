"""EngineeringPodWorkflow — the coding child (CLAUDE.md §7, §8).

A feature is implemented by **one pod session working the architect's stories in order,
in one workspace** — so it lands as one coherent diff. For multi-story plans that session
is itself an orchestrator (execution-plane detail, see claude_sdk.py): a lead dispatches
per-story implementer subagents serially in the shared tree, each on the story's own model
tier. The single-WRITER invariant holds either way: no concurrent writers, no divergent
bases (no parallel agents producing conflicting diffs against separate clones, and no
partial feature from coding only the first story). This workflow then runs QA, with one bounded QA->fix pass
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
        CI_ACTIVITY_TIMEOUT_MINUTES,
        CODING_ACTIVITY_TIMEOUT_MINUTES,
        MAX_CI_FIX_PASSES,
        MAX_QA_FIX_PASSES,
        MAX_REVIEW_PASSES,
    )
    from orchestrator.shared.types import CIResult, PodResult, StoryPlan
    from orchestrator.workflows.common import run_activity

# Coding + PR-open activities run a real agent and the target's tests in a sandbox — give
# them minutes, not the 30s reasoning default. Deterministic (a constant timedelta).
_CODING_TIMEOUT = timedelta(minutes=CODING_ACTIVITY_TIMEOUT_MINUTES)
# The await-CI activity polls the PR's checks until they conclude — minutes; its start-to-close
# must exceed the internal poll timeout (CI_POLL_TIMEOUT_MINUTES). Deterministic constant.
_CI_TIMEOUT = timedelta(minutes=CI_ACTIVITY_TIMEOUT_MINUTES)


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

        # One pod session implements the whole ordered story plan in a single workspace
        # (orchestrating per-story subagents itself when the plan has multiple stories).
        result = await run_activity(act.implement_stories, plan, timeout=_CODING_TIMEOUT)
        qa = await run_activity(act.qa_review, plan.project, [result])
        _spend(result, qa)

        # Bounded QA -> fix loop: re-run the implementation once if QA failed (§10 cap).
        fixes = 0
        while not qa.passed and fixes < MAX_QA_FIX_PASSES:
            fixes += 1
            result = await run_activity(act.implement_stories, plan, timeout=_CODING_TIMEOUT)
            qa = await run_activity(act.qa_review, plan.project, [result])
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
            qa = await run_activity(act.qa_review, plan.project, [result])
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

        # If the PR never opened (e.g. the diff didn't apply against the remote base), there is
        # nothing to wait on or fix: await_ci would poll a non-existent PR until it times out
        # (CI_POLL_TIMEOUT_MINUTES) and the "fix" loop would burn a full coding revise per pass
        # against a branch that still can't be pushed (a real ~$2.31 + 40min waste observed
        # 2026-06-21). Short-circuit to a clear CI_FAILED so the parent halts before deploy.
        if not pr.opened:
            ci = CIResult(
                status="no_pr", passed=False,
                failing_summary=f"PR was not opened: {pr.note or 'open_pr reported opened=False'}",
            )
        else:
            # Bounded CI gate -> fix loop (§9.2, §10). Wait for the opened PR's real CI to
            # conclude; while it's red, feed the failing checks back to the developer, push the
            # fix to the SAME PR, and re-check. Capped by MAX_CI_FIX_PASSES (each pass is a full
            # coding run + a CI wait). CI "unavailable" (mock/local target) reports passed=True,
            # so $0 dry-runs skip this. If still red after the cap, ci.passed stays False and the
            # parent halts before merging (Status.CI_FAILED) — the org never merges past a red PR.
            ci = await run_activity(act.await_ci, plan.project, branch, pr.url, timeout=_CI_TIMEOUT)
            _spend(ci)
            ci_passes = 0
            while not ci.passed and ci_passes < MAX_CI_FIX_PASSES:
                ci_passes += 1
                result = await run_activity(
                    act.revise_after_ci, plan, result, ci, timeout=_CODING_TIMEOUT
                )
                upd = await run_activity(act.update_pr, plan.project, branch, [result], timeout=_CODING_TIMEOUT)
                ci = await run_activity(act.await_ci, plan.project, branch, pr.url, timeout=_CI_TIMEOUT)
                _spend(result, upd, ci)

        return PodResult(
            story_result=result,
            qa=qa,
            branch=branch,
            pr_url=pr.url,
            review_approved=review.approved,
            review_notes=review.notes,
            ci_passed=ci.passed,
            ci_url=ci.url,
            ci_notes=ci.failing_summary or ci.status,
            cost_tokens=cost,
            cost_usd=cost_usd,
        )
