"""EngineeringPodWorkflow — the coding child (CLAUDE.md §7, §8).

A feature is implemented by a **single coding agent working the architect's stories in
order, in one workspace** — so it lands as one coherent diff (no parallel agents producing
conflicting diffs against separate clones, and no partial feature from coding only the
first story). The orchestrator (this workflow) then runs QA, with one bounded QA->fix pass
(MAX_QA_FIX_PASSES), and opens the PR. Deploy is NOT here — it sits behind the parent's
human approval gate (§9.2).
"""

from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities import stubs as act
    from orchestrator.shared.config import CODING_ACTIVITY_TIMEOUT_MINUTES, MAX_QA_FIX_PASSES
    from orchestrator.shared.types import PodResult, StoryPlan
    from orchestrator.workflows.common import run_activity

# Coding + PR-open activities run a real agent and the target's tests in a sandbox — give
# them minutes, not the 30s reasoning default. Deterministic (a constant timedelta).
_CODING_TIMEOUT = timedelta(minutes=CODING_ACTIVITY_TIMEOUT_MINUTES)


@workflow.defn
class EngineeringPodWorkflow:
    @workflow.run
    async def run(self, plan: StoryPlan) -> PodResult:
        # One agent implements the whole ordered story plan in a single workspace.
        result = await run_activity(act.implement_stories, plan, timeout=_CODING_TIMEOUT)
        qa = await run_activity(act.qa_review, [result])

        # Bounded QA -> fix loop: re-run the implementation once if QA failed (§10 cap).
        fixes = 0
        while not qa.passed and fixes < MAX_QA_FIX_PASSES:
            fixes += 1
            result = await run_activity(act.implement_stories, plan, timeout=_CODING_TIMEOUT)
            qa = await run_activity(act.qa_review, [result])

        # Open the PR from the agent's diff (the pod's terminal artifact). Deploy is NOT
        # here — it sits behind the parent's human approval gate (§9.2). The branch carries a
        # per-run tag (from the deterministic workflow id) so re-runs don't collide on the
        # remote — replay-safe because workflow_id is fixed for a given execution.
        run_tag = workflow.info().workflow_id.removesuffix("-pod").split("-")[-1]
        branch = f"agentic/{plan.feature_id}-{run_tag}"
        pr = await run_activity(act.open_pr, plan.project, branch, [result], timeout=_CODING_TIMEOUT)

        cost = result.cost_tokens + qa.cost_tokens + pr.cost_tokens
        cost_usd = result.cost_usd + qa.cost_usd + pr.cost_usd
        return PodResult(
            story_results=[result],
            qa=qa,
            branch=branch,
            pr_url=pr.url,
            cost_tokens=cost,
            cost_usd=cost_usd,
        )
