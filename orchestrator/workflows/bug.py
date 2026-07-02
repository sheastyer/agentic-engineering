"""BugWorkflow — the shorter path (CLAUDE.md §7).

triage -> dedupe -> (optional user-clarification signal w/ 7-day timeout) -> PM
prioritize -> engineering pod (child) -> gated deploy.

The fix itself rides the SAME EngineeringPodWorkflow the feature path uses — one pod,
two entry points. The bug becomes a one-story plan, so the pod's whole machinery comes
for free: real coding in a sandboxed workspace, the bounded code-review ↔ revise loop,
functional QA, open_pr, the bounded CI gate ↔ fix loop, and an idempotent merge on the
deploy gate. (The old bespoke fix/review_fix stages produced a diff that died inside the
activity — no PR, an empty deploy ref — and their verdicts were never even read.)

Same invariants as the feature workflow: deterministic orchestration only, human gates
are signals with timeouts, deploy is gated, the org never merges past a red PR.
"""

from datetime import timedelta

import asyncio

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from orchestrator.activities import stubs as act
    from orchestrator.shared.config import (
        BUDGET_OVERRIDE_TIMEOUT_DAYS,
        BUDGET_USD,
        CLARIFICATION_TIMEOUT_DAYS,
        DEPLOY_TIMEOUT_DAYS,
    )
    from orchestrator.shared.types import (
        FeedbackEvent,
        Status,
        Story,
        StoryPlan,
        WorkflowResult,
        WorkflowState,
    )
    from orchestrator.workflows.common import run_activity
    from orchestrator.workflows.engineering_pod import EngineeringPodWorkflow


class _BudgetHalt(Exception):
    """Internal: the per-workflow budget gate was declined or timed out. Caught in run()."""


@workflow.defn
class BugWorkflow:
    def __init__(self) -> None:
        self._stage = "init"
        self._status = Status.RUNNING
        self._cost = 0
        self._cost_usd = 0.0
        self._budget_overridden = False
        self._ceiling = BUDGET_USD["bug"]
        self._log: list[str] = []
        self._clarification: str | None = None
        self._deploy_approved: bool | None = None
        self._budget_decision: bool | None = None

    @workflow.signal
    def submit_user_clarification(self, text: str) -> None:
        self._clarification = text

    @workflow.signal
    def submit_deploy_approval(self, approve: bool) -> None:
        self._deploy_approved = approve

    @workflow.signal
    def submit_budget_decision(self, approve: bool) -> None:
        self._budget_decision = approve

    @workflow.query
    def get_state(self) -> WorkflowState:
        return WorkflowState(
            stage=self._stage,
            status=self._status,
            cost_tokens=self._cost,
            cost_usd=round(self._cost_usd, 6),
            log=list(self._log),
        )

    @workflow.run
    async def run(self, event: FeedbackEvent) -> WorkflowResult:
        try:
            return await self._execute(event)
        except _BudgetHalt:
            return self._result(event, f"Halted at budget gate (${self._cost_usd:.4f}).")

    async def _execute(self, event: FeedbackEvent) -> WorkflowResult:
        triage = await self._act(act.triage_feedback, event, stage="triage")
        dedupe = await self._act(act.dedupe_check, event, stage="dedupe")
        if dedupe.is_duplicate:
            self._status = Status.CLOSED_DUPLICATE
            return self._result(event, f"Duplicate of {dedupe.duplicate_of}.")

        # Optional clarification gate (only if triage asked for it), 7-day timeout.
        if triage.needs_clarification:
            self._enter("await_clarification")
            try:
                await workflow.wait_condition(
                    lambda: self._clarification is not None,
                    timeout=timedelta(days=CLARIFICATION_TIMEOUT_DAYS),
                )
            except asyncio.TimeoutError:
                self._log.append("clarification timed out; proceeding with original report")

        await self._act(act.pm_prioritize_bug, event, triage, stage="pm_prioritize")

        # Engineering pod (child) — the bug as a one-story plan. The clarification (when we
        # got one) rides along in the context so the coding agent sees the reporter's answer.
        context = event.body
        if self._clarification:
            context += f"\n\nReporter's clarification: {self._clarification}"
        plan = StoryPlan(
            feature_id=f"bugfix-{event.id}",
            stories=[
                Story(
                    id=f"bugfix-{event.id}-S1",
                    title=f"Fix this bug: {event.title}",
                    estimate=2,
                    tier="sonnet",
                )
            ],
            project=event.project,
            context=context,
        )
        self._enter("engineering_pod")
        pod = await workflow.execute_child_workflow(
            EngineeringPodWorkflow.run,
            plan,
            id=f"{workflow.info().workflow_id}-pod",
        )
        self._cost += pod.cost_tokens
        self._cost_usd += pod.cost_usd
        await self._check_budget()

        # CI gate (hard, §9.2): never surface a red PR to the deploy gate, let alone merge it.
        if not pod.ci_passed:
            self._status = Status.CI_FAILED
            self._enter("ci_failed")
            return self._result(
                event, f"CI failed on the PR ({pod.pr_url}); halted before deploy. {pod.ci_notes}"
            )

        # Deploy approval gate
        self._enter("deploy_approval")
        try:
            await workflow.wait_condition(
                lambda: self._deploy_approved is not None,
                timeout=timedelta(days=DEPLOY_TIMEOUT_DAYS),
            )
        except asyncio.TimeoutError:
            self._status = Status.ESCALATED
            return self._result(event, "Deploy approval timed out; escalated.")

        if not self._deploy_approved:
            self._status = Status.HELD
            return self._result(event, "Human held the deploy.")

        await self._act(act.deploy, event.project, pod.branch, stage="deploy")
        self._status = Status.SHIPPED
        return self._result(event, f"Bug fix shipped ({pod.pr_url or pod.branch}).")

    # --- helpers ---------------------------------------------------------------
    async def _check_budget(self) -> None:
        if self._budget_overridden or self._cost_usd <= self._ceiling:
            return
        self._enter(f"budget_gate (${self._cost_usd:.4f} > ${self._ceiling:.2f})")
        self._budget_decision = None
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
            self._log.append("budget override approved; continuing")
            return
        self._status = Status.OVER_BUDGET
        self._log.append("budget override declined; halting")
        raise _BudgetHalt()

    async def _act(self, fn, *args, stage: str, timeout=None):
        self._enter(stage)
        result = await run_activity(fn, *args, timeout=timeout)
        self._cost += getattr(result, "cost_tokens", 0)
        self._cost_usd += getattr(result, "cost_usd", 0.0)
        await self._check_budget()
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
