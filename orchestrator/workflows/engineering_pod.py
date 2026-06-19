"""EngineeringPodWorkflow — orchestrator-worker child (CLAUDE.md §7, §8).

The orchestrator (this workflow) fans stories out to worker activities that each run a
coding agent (stubbed in M1; Agent SDK in sandboxed worktrees in M4), then runs QA. A
single bounded QA->fix pass (MAX_QA_FIX_PASSES) re-implements any failing stories — the
cap keeps the loop from running away (§10). Deploy is NOT here: it sits behind the
parent's human approval gate (§9.2).
"""

import asyncio
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities import stubs as act
    from orchestrator.shared.config import (
        CODING_ACTIVITY_TIMEOUT_MINUTES,
        CODING_MAX_STORIES,
        MAX_QA_FIX_PASSES,
    )
    from orchestrator.shared.types import PodResult, StoryPlan, StoryResult
    from orchestrator.workflows.common import run_activity

# Coding + PR-open activities run a real agent and the target's tests in a sandbox — give
# them minutes, not the 30s reasoning default. Deterministic (a constant timedelta).
_CODING_TIMEOUT = timedelta(minutes=CODING_ACTIVITY_TIMEOUT_MINUTES)


@workflow.defn
class EngineeringPodWorkflow:
    @workflow.run
    async def run(self, plan: StoryPlan) -> PodResult:
        # Cost cap (CLAUDE.md §10): code at most CODING_MAX_STORIES this run; the rest are
        # recorded as $0 "deferred" markers. At the default of 1 this also means a single
        # coding agent — no fan-out of parallel autonomous agents against one repo.
        to_code = plan.stories[:CODING_MAX_STORIES]
        deferred = plan.stories[CODING_MAX_STORIES:]
        story_by_id = {s.id: s for s in to_code}

        # Fan out implementation across the coded stories (workers).
        results = list(
            await asyncio.gather(
                *(
                    run_activity(act.implement_story, s, plan.project, timeout=_CODING_TIMEOUT)
                    for s in to_code
                )
            )
        )
        qa = await run_activity(act.qa_review, results)

        # Bounded QA -> fix loop: re-implement genuinely failing stories, then re-QA.
        fixes = 0
        while not qa.passed and fixes < MAX_QA_FIX_PASSES:
            fixes += 1
            failed_ids = [r.story_id for r in results if r.status == "failed"]
            if not failed_ids:
                break
            refixed = list(
                await asyncio.gather(
                    *(
                        run_activity(
                            act.implement_story, story_by_id[sid], plan.project,
                            timeout=_CODING_TIMEOUT,
                        )
                        for sid in failed_ids
                    )
                )
            )
            results = [r for r in results if r.status != "failed"] + refixed
            qa = await run_activity(act.qa_review, results)

        # Record deferred stories (cost cap) for visibility — coded nothing, cost nothing.
        results += [
            StoryResult(
                story_id=s.id, status="deferred", pr_ref="",
                summary="deferred this run (pod cost cap CODING_MAX_STORIES)",
            )
            for s in deferred
        ]

        # Open the PR from the assembled story diffs (the pod's terminal artifact). Deploy is
        # NOT here — it sits behind the parent's human approval gate (§9.2).
        branch = f"agentic/{plan.feature_id}"
        pr = await run_activity(
            act.open_pr, plan.project, branch, results, timeout=_CODING_TIMEOUT
        )

        cost = sum(r.cost_tokens for r in results) + qa.cost_tokens + pr.cost_tokens
        return PodResult(
            story_results=results,
            qa=qa,
            branch=branch,
            pr_url=pr.url,
            cost_tokens=cost,
        )
