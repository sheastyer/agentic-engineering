"""FeatureRequestWorkflow — the primary orchestration (CLAUDE.md §7).

Stage order:
  1. pm_draft_brief
  2. exec council: agent votes (parallel) + human vote (signal, 72h escalation timer)
     -> deterministic tally -> branch on approved?
  3. pm_write_prd -> bounded PRD <-> architect_review loop (max 3 passes)
  4. ux_generate_mocks (conditional on brief.ui_impacting)
  5. ConsumerResearchWorkflow (child, parallel fan-out)
  6. PM sign-off (signal); "revise" loops back into PRD revision (bounded)
  7. architect_plan_stories
  8. EngineeringPodWorkflow (child, orchestrator-worker)
  9. deploy approval (signal) -> deploy -> SHIPPED

Invariants honored here: this module is pure deterministic orchestration — no LLM calls,
no I/O, no clocks, no randomness (§9.1). Every human gate is a signal with a timeout
(§9.4). Every agent<->agent loop is bounded (§10). Deploy sits behind a human gate (§9.2).
"""

import asyncio
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities import stubs as act
    from orchestrator.shared.config import (
        BUDGET_OVERRIDE_TIMEOUT_DAYS,
        BUDGET_USD,
        COUNCIL_TIMEOUT_HOURS,
        DEFAULT_RESEARCH_PERSONAS,
        DEPLOY_TIMEOUT_DAYS,
        MAX_PRD_PASSES,
        MAX_SIGNOFF_REVISIONS,
        SIGNOFF_TIMEOUT_DAYS,
    )
    from orchestrator.shared.types import (
        ArchitectReview,
        CouncilResult,
        FeedbackEvent,
        GateNotice,
        ResearchRequest,
        Status,
        Vote,
        WorkflowResult,
        WorkflowState,
    )
    from orchestrator.workflows.common import NOTIFY_TIMEOUT, clip, run_activity
    from orchestrator.workflows.consumer_research import ConsumerResearchWorkflow
    from orchestrator.workflows.engineering_pod import EngineeringPodWorkflow

COUNCIL_AGENT_PERSONAS = ["legal", "sales"]


class _BudgetHalt(Exception):
    """Internal: the per-workflow budget gate was declined or timed out. Caught in run()."""


@workflow.defn
class FeatureRequestWorkflow:
    def __init__(self) -> None:
        self._stage = "init"
        self._status = Status.RUNNING
        self._cost = 0
        self._cost_usd = 0.0
        self._budget_overridden = False
        self._prd_version = 0
        self._council_approved: bool | None = None
        self._log: list[str] = []
        self._title = ""
        self._project = ""
        self._gate_context: list[str] = []

        # Human-gate inboxes (set by signals, read at the matching gate). Each decision
        # carries WHO made it (M5 SEC: approvals are recorded with the approver's
        # identity in the stage log / audit trail).
        self._human_vote: Vote | None = None
        self._pm_signoff: str | None = None      # "approve" | "revise"
        self._pm_signoff_by = "unknown"
        self._deploy_approved: bool | None = None
        self._deploy_approver = "unknown"
        self._budget_decision: bool | None = None  # budget-override gate
        self._budget_approver = "unknown"

    # --- signals (human gates) --------------------------------------------------
    # The `approver` args default to "unknown" so the extension is additive (replay-safe
    # for histories signalled before identities existed, and for callers that don't send one).
    @workflow.signal
    def submit_human_vote(self, approve: bool, voter: str = "human") -> None:
        self._human_vote = Vote(voter=voter, approve=approve, rationale="human council vote")

    @workflow.signal
    def submit_pm_signoff(self, decision: str, approver: str = "unknown") -> None:
        self._pm_signoff = decision
        self._pm_signoff_by = approver

    @workflow.signal
    def submit_deploy_approval(self, approve: bool, approver: str = "unknown") -> None:
        self._deploy_approved = approve
        self._deploy_approver = approver

    @workflow.signal
    def submit_budget_decision(self, approve: bool, approver: str = "unknown") -> None:
        self._budget_decision = approve
        self._budget_approver = approver

    # --- query ------------------------------------------------------------------
    @workflow.query
    def get_state(self) -> WorkflowState:
        return WorkflowState(
            stage=self._stage,
            status=self._status,
            cost_tokens=self._cost,
            cost_usd=round(self._cost_usd, 6),
            prd_version=self._prd_version,
            council_approved=self._council_approved,
            log=list(self._log),
            gate_context=list(self._gate_context),
        )

    # --- run --------------------------------------------------------------------
    @workflow.run
    async def run(self, event: FeedbackEvent) -> WorkflowResult:
        self._ceiling = BUDGET_USD["feature"]
        try:
            return await self._execute(event)
        except _BudgetHalt:
            return self._result(event, f"Halted at budget gate (${self._cost_usd:.4f}).")

    async def _execute(self, event: FeedbackEvent) -> WorkflowResult:
        self._title = event.title
        self._project = event.project

        # 1. PM brief
        brief = await self._act(act.pm_draft_brief, event, stage="pm_draft_brief")

        # 2. Exec council: parallel agent votes + human vote (signal w/ 72h timer)
        council = await self._run_council(brief)
        self._council_approved = council.approved
        await self._check_budget()
        if not council.approved:
            self._status = Status.REJECTED_BY_COUNCIL
            return self._result(event, "Council rejected the feature.")

        # 3. PRD + bounded PRD<->architect loop, with PM sign-off loopback (6).
        prd = await self._act(act.pm_write_prd, brief, stage="pm_write_prd")
        self._prd_version = prd.version

        signoff_revisions = 0
        while True:
            prd = await self._refine_prd(prd)

            # 4. Conditional UX mocks
            if brief.ui_impacting:
                await self._act(act.ux_generate_mocks, prd, stage="ux_generate_mocks")

            # 5. Consumer research (child, fan-out)
            self._enter("consumer_research")
            report = await workflow.execute_child_workflow(
                ConsumerResearchWorkflow.run,
                ResearchRequest(
                    feature_id=prd.feature_id,
                    prd=prd,
                    personas=list(DEFAULT_RESEARCH_PERSONAS),
                ),
                id=f"{workflow.info().workflow_id}-research-{signoff_revisions}",
            )
            self._cost += report.cost_tokens
            self._cost_usd += report.cost_usd
            await self._check_budget()

            # 6. PM sign-off gate; "revise" loops back into PRD revision (bounded)
            decision = await self._pm_signoff_gate(prd, report)
            if decision == "approve":
                break
            signoff_revisions += 1
            if signoff_revisions > MAX_SIGNOFF_REVISIONS:
                self._log.append("pm sign-off revisions exhausted; proceeding with current PRD")
                break
            prd = await self._act(
                act.pm_revise_prd,
                prd,
                ArchitectReview(approved=False, pass_no=0, concerns=["PM requested revision"]),
                stage="pm_revise_prd",
            )
            self._prd_version = prd.version

        # 7. Story planning
        plan = await self._act(act.architect_plan_stories, prd, report, stage="architect_plan_stories")

        # 8. Engineering pod (child, orchestrator-worker)
        self._enter("engineering_pod")
        pod = await workflow.execute_child_workflow(
            EngineeringPodWorkflow.run,
            plan,
            id=f"{workflow.info().workflow_id}-pod",
        )
        self._cost += pod.cost_tokens
        self._cost_usd += pod.cost_usd
        await self._check_budget()

        # 8a. QA gate (hard, symmetric with CI): the QA agent's final verdict on the pod's
        # output. The pod already ran its bounded QA→fix loop; if the verdict is still a fail,
        # halt before the deploy gate — an unattended-ish merge must not ride on a red QA.
        # ("Tests unavailable in sandbox" is NOT a fail — the profile declares that honestly.)
        if not pod.qa.passed:
            self._status = Status.QA_FAILED
            self._enter("qa_failed")
            return self._result(
                event, f"QA failed on the pod's output; halted before deploy. {pod.qa.notes}"
            )

        # 8b. CI gate: the org must not progress past code review to merge while the PR's CI is
        # red. The pod already ran a bounded CI fix loop; if CI is still failing, halt before the
        # deploy gate (a human must intervene) — never auto-merge a red PR (§9.2).
        if not pod.ci_passed:
            self._status = Status.CI_FAILED
            self._enter("ci_failed")
            return self._result(
                event, f"CI failed on the PR ({pod.pr_url}); halted before deploy. {pod.ci_notes}"
            )

        # 9. Deploy approval gate -> deploy -> SHIPPED
        self._enter("deploy_approval")
        await self._notify_gate(
            "deploy",
            [
                f"PR: {pod.pr_url or pod.branch}",
                f"QA: {'passed' if pod.qa.passed else 'failed'} — {clip(pod.qa.notes)}",
                f"review: {'approved' if pod.review_approved else 'unresolved'}"
                + (f" — {clip(pod.review_notes)}" if pod.review_notes else ""),
                f"CI: {clip(pod.ci_notes) or 'n/a'}" + (f" ({pod.ci_url})" if pod.ci_url else ""),
            ],
        )
        try:
            await workflow.wait_condition(
                lambda: self._deploy_approved is not None,
                timeout=timedelta(days=DEPLOY_TIMEOUT_DAYS),
            )
        except asyncio.TimeoutError:
            self._status = Status.ESCALATED
            return self._result(event, "Deploy approval timed out; escalated.")

        self._log.append(
            f"deploy {'approved' if self._deploy_approved else 'held'} by {self._deploy_approver}"
        )
        if not self._deploy_approved:
            self._status = Status.HELD
            return self._result(event, "Human held the deploy.")

        await self._act(act.deploy, event.project, pod.branch, stage="deploy")
        self._status = Status.SHIPPED
        return self._result(event, f"Shipped on branch {pod.branch}.")

    # --- stage helpers ----------------------------------------------------------
    async def _run_council(self, brief) -> CouncilResult:
        # Governance model: the human vote is DECISIVE (veto/override). Agent votes
        # (legal, sales) are advisory input the human weighs. Only if the human never
        # votes (72h timer fires) do we fall back to the agents' advisory majority.
        self._enter("exec_council")
        agent_votes = list(
            await asyncio.gather(
                *(self._act_raw(act.council_agent_vote, p, brief) for p in COUNCIL_AGENT_PERSONAS)
            )
        )
        await self._notify_gate(
            "council",
            [f"brief: {clip(brief.summary)}"]
            + [
                f"{v.voter}: {'approve' if v.approve else 'reject'} — {clip(v.rationale)}"
                for v in agent_votes
            ],
        )

        escalated = False
        try:
            await workflow.wait_condition(
                lambda: self._human_vote is not None,
                timeout=timedelta(hours=COUNCIL_TIMEOUT_HOURS),
            )
        except asyncio.TimeoutError:
            escalated = True
            self._log.append("council human vote timed out (72h); falling back to agent advisory majority")

        votes = list(agent_votes)
        human = self._human_vote
        if human is not None:
            votes.append(human)
            approved = human.approve  # human override is decisive
            self._log.append(
                f"council: human override by {human.voter} -> "
                f"{'approved' if approved else 'rejected'} (agents advisory)"
            )
        else:
            approvals = sum(1 for v in agent_votes if v.approve)
            approved = approvals * 2 > len(agent_votes)  # advisory majority; ties fail
            self._log.append(
                f"council: escalated, agent advisory {approvals}/{len(agent_votes)} "
                f"-> {'approved' if approved else 'rejected'}"
            )
        return CouncilResult(votes=votes, approved=approved, escalated=escalated)

    async def _refine_prd(self, prd):
        """Bounded PRD <-> architect review loop (max MAX_PRD_PASSES passes)."""
        for pass_no in range(1, MAX_PRD_PASSES + 1):
            self._enter(f"architect_review_prd[pass {pass_no}]")
            review = await self._act_raw(act.architect_review_prd, prd, pass_no)
            if review.approved:
                self._log.append(f"PRD approved by architect on pass {pass_no}")
                return prd
            prd = await self._act(act.pm_revise_prd, prd, review, stage="pm_revise_prd")
            self._prd_version = prd.version
        self._log.append(f"PRD loop hit cap ({MAX_PRD_PASSES}); proceeding with v{prd.version}")
        return prd

    async def _pm_signoff_gate(self, prd, report) -> str:
        # Consume-after-read (not reset-before-wait): a decision delivered early is still
        # honored, but each loopback iteration requires a fresh signal.
        self._enter("pm_signoff")
        await self._notify_gate(
            "pm_signoff",
            [
                f"PRD v{prd.version} ({prd.feature_id})",
                f"research: {report.overall_sentiment} across {len(report.findings)} personas",
            ],
        )
        try:
            await workflow.wait_condition(
                lambda: self._pm_signoff is not None,
                timeout=timedelta(days=SIGNOFF_TIMEOUT_DAYS),
            )
        except asyncio.TimeoutError:
            self._log.append("PM sign-off timed out; treating as revise")
            return "revise"
        decision = self._pm_signoff
        self._pm_signoff = None
        self._log.append(f"pm sign-off: {decision or 'revise'} (by {self._pm_signoff_by})")
        return decision or "revise"

    async def _check_budget(self) -> None:
        """Trip a human budget-override gate when accumulated spend crosses the ceiling
        (CLAUDE.md §10, §9.4). Approve once -> don't re-trip; decline/timeout -> halt."""
        if self._budget_overridden or self._cost_usd <= self._ceiling:
            return
        self._enter(f"budget_gate (${self._cost_usd:.4f} > ${self._ceiling:.2f})")
        self._budget_decision = None
        await self._notify_gate(
            "budget",
            [f"spent ${self._cost_usd:.4f} of the ${self._ceiling:.2f} ceiling"],
        )
        try:
            await workflow.wait_condition(
                lambda: self._budget_decision is not None,
                timeout=timedelta(days=BUDGET_OVERRIDE_TIMEOUT_DAYS),
            )
        except asyncio.TimeoutError:
            self._status = Status.OVER_BUDGET
            self._log.append("budget override timed out; halting")
            raise _BudgetHalt()
        if self._budget_decision:
            self._budget_overridden = True
            self._log.append(f"budget override approved by {self._budget_approver}; continuing")
            return
        self._status = Status.OVER_BUDGET
        self._log.append(f"budget override declined by {self._budget_approver}; halting")
        raise _BudgetHalt()

    async def _notify_gate(self, gate: str, context: list[str]) -> None:
        """Tell the human-I/O channel this workflow is parked at a gate. Advisory: a
        notification failure must never block or kill the gate — the signal path and the
        gate's timeout still work without it, so failures degrade to a log line."""
        self._gate_context = list(context)
        notice = GateNotice(
            workflow_id=workflow.info().workflow_id,
            gate=gate,
            title=self._title,
            project=self._project,
            cost_usd=round(self._cost_usd, 4),
            context=list(context),
        )
        try:
            await run_activity(act.notify_gate, notice, timeout=NOTIFY_TIMEOUT)
        except Exception:
            self._log.append(f"gate notification failed ({gate}); gate still open on its timeout")

    # --- low-level activity + bookkeeping ---------------------------------------
    async def _act(self, fn, *args, stage: str):
        self._enter(stage)
        result = await self._act_raw(fn, *args)
        await self._check_budget()
        return result

    async def _act_raw(self, fn, *args):
        result = await run_activity(fn, *args)
        self._cost += getattr(result, "cost_tokens", 0)
        self._cost_usd += getattr(result, "cost_usd", 0.0)
        return result

    def _enter(self, stage: str) -> None:
        self._stage = stage
        self._log.append(stage)

    def _result(self, event: FeedbackEvent, summary: str) -> WorkflowResult:
        self._stage = "done"
        return WorkflowResult(
            feedback_id=event.id,
            status=self._status,
            cost_tokens=self._cost,
            cost_usd=round(self._cost_usd, 6),
            summary=summary,
            stage_log=list(self._log),
        )
