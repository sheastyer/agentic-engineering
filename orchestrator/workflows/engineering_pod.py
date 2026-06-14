"""EngineeringPodWorkflow — orchestrator-worker child (CLAUDE.md §7, §8).

The orchestrator (this workflow) fans stories out to worker activities that each run a
coding agent (stubbed in M1; Agent SDK in sandboxed worktrees in M4), then runs QA. A
single bounded QA->fix pass (MAX_QA_FIX_PASSES) re-implements any failing stories — the
cap keeps the loop from running away (§10). Deploy is NOT here: it sits behind the
parent's human approval gate (§9.2).
"""

import asyncio

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities import stubs as act
    from orchestrator.shared.config import MAX_QA_FIX_PASSES
    from orchestrator.shared.types import PodResult, StoryPlan
    from orchestrator.workflows.common import run_activity


@workflow.defn
class EngineeringPodWorkflow:
    @workflow.run
    async def run(self, plan: StoryPlan) -> PodResult:
        story_by_id = {s.id: s for s in plan.stories}

        # Fan out implementation across stories (workers).
        results = list(
            await asyncio.gather(
                *(run_activity(act.implement_story, s) for s in plan.stories)
            )
        )
        qa = await run_activity(act.qa_review, results)

        # Bounded QA -> fix loop: re-implement failing stories, then re-QA.
        fixes = 0
        while not qa.passed and fixes < MAX_QA_FIX_PASSES:
            fixes += 1
            failed_ids = [r.story_id for r in results if r.status != "done"]
            refixed = list(
                await asyncio.gather(
                    *(run_activity(act.implement_story, story_by_id[sid]) for sid in failed_ids)
                )
            )
            results = [r for r in results if r.status == "done"] + refixed
            qa = await run_activity(act.qa_review, results)

        cost = sum(r.cost_tokens for r in results) + qa.cost_tokens
        return PodResult(
            story_results=results,
            qa=qa,
            branch=f"agentic/{plan.feature_id}",
            cost_tokens=cost,
        )
