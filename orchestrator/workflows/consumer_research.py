"""ConsumerResearchWorkflow — parallel fan-out child (CLAUDE.md §7, §8).

Runs one synthetic-user activity per demographic persona in parallel, then a single
synthesis activity. Returns a lightweight report (a summary ref + per-persona findings),
not raw transcripts — subagents persist detail to the artifact store and return
references (§10). The panel size is bounded by the caller-supplied persona list.
"""

import asyncio

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities import stubs as act
    from orchestrator.shared.types import ResearchReport, ResearchRequest
    from orchestrator.workflows.common import run_activity


@workflow.defn
class ConsumerResearchWorkflow:
    @workflow.run
    async def run(self, req: ResearchRequest) -> ResearchReport:
        # Fan out: one activity per persona, all in parallel.
        findings = await asyncio.gather(
            *(run_activity(act.consumer_research_persona, p, req.prd) for p in req.personas)
        )

        # Judge/synthesize step rolls the panel up into one report.
        report = await run_activity(act.synthesize_research, list(findings))
        report.feature_id = req.feature_id
        report.cost_tokens += sum(f.cost_tokens for f in findings)
        return report
