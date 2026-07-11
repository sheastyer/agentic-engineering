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
    from orchestrator.humanio.gates import row_line
    from orchestrator.shared.types import (
        FeedbackEvent,
        GateNotice,
        NoticeRow,
        ProgressNotice,
        Status,
        Story,
        StoryPlan,
        WorkflowResult,
        WorkflowState,
    )
    from orchestrator.workflows.common import NOTIFY_TIMEOUT, clip, run_activity
    from orchestrator.workflows.engineering_pod import EngineeringPodWorkflow


class _BudgetHalt(Exception):
    """Internal: a budget gate (over-budget override, or the pre-pod coding-budget gate)
    was declined or timed out. Caught in run(); the message becomes the result summary."""


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
        self._title = ""
        self._project = ""
        self._gate_context: list[str] = []
        self._thread_ts = ""    # the run's Slack thread anchor (set by the first progress post)
        self._clarification: str | None = None
        self._clarification_by = "unknown"
        self._deploy_approved: bool | None = None
        self._deploy_approver = "unknown"
        self._budget_decision: bool | None = None
        self._budget_approver = "unknown"
        # Coding-budget gate inbox: (decision, budget_usd, approver) — see feature workflow.
        self._coding_budget: tuple | None = None

    # `approver` defaults to "unknown" so the extension is additive (replay-safe for
    # histories signalled before identities existed; M5 SEC — approvals carry who approved).
    @workflow.signal
    def submit_user_clarification(self, text: str, approver: str = "unknown") -> None:
        self._clarification = text
        self._clarification_by = approver

    @workflow.signal
    def submit_deploy_approval(self, approve: bool, approver: str = "unknown") -> None:
        self._deploy_approved = approve
        self._deploy_approver = approver

    @workflow.signal
    def submit_budget_decision(self, approve: bool, approver: str = "unknown") -> None:
        self._budget_decision = approve
        self._budget_approver = approver

    @workflow.signal
    def submit_coding_budget(
        self, decision: str, budget_usd: float = 0.0, approver: str = "unknown"
    ) -> None:
        self._coding_budget = (decision, budget_usd, approver)

    @workflow.query
    def get_state(self) -> WorkflowState:
        return WorkflowState(
            stage=self._stage,
            status=self._status,
            cost_tokens=self._cost,
            cost_usd=round(self._cost_usd, 6),
            log=list(self._log),
            gate_context=list(self._gate_context),
        )

    @workflow.run
    async def run(self, event: FeedbackEvent) -> WorkflowResult:
        try:
            result = await self._execute(event)
        except _BudgetHalt as halt:
            result = self._result(
                event, str(halt) or f"Halted at budget gate (${self._cost_usd:.4f})."
            )
        # One terminal post covers every exit path (shipped/duplicate/held/over-budget/...).
        await self._notify_progress(
            "done",
            [f"status: {result.status} · total cost ${result.cost_usd:.4f}", clip(result.summary)],
        )
        return result

    async def _execute(self, event: FeedbackEvent) -> WorkflowResult:
        self._title = event.title
        self._project = event.project

        # Root of the run's Slack thread: the bug report itself, the moment work starts.
        await self._notify_progress(
            "feedback_received",
            [f"{event.kind} from {event.submitted_by}", clip(event.body)],
        )

        triage = await self._act(act.triage_feedback, event, stage="triage")
        dedupe = await self._act(act.dedupe_check, event, stage="dedupe")
        if dedupe.is_duplicate:
            self._status = Status.CLOSED_DUPLICATE
            return self._result(event, f"Duplicate of {dedupe.duplicate_of}.")
        await self._notify_progress(
            "triage",
            [
                f"priority: {triage.priority}"
                + (" · needs clarification" if triage.needs_clarification else "")
            ],
        )

        # Optional clarification gate (only if triage asked for it), 7-day timeout.
        if triage.needs_clarification:
            self._enter("await_clarification")
            # Free-text gate: the Slack card carries an answer input (see
            # humanio.gates GATE_INPUTS); the CLI / a direct signal still work too.
            await self._notify_gate(
                "clarification",
                [f"reporter: {event.submitted_by}", f"report: {clip(event.body)}"],
            )
            try:
                await workflow.wait_condition(
                    lambda: self._clarification is not None,
                    timeout=timedelta(days=CLARIFICATION_TIMEOUT_DAYS),
                )
                self._log.append(f"clarification received (by {self._clarification_by})")
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
        # Coding-budget gate (§9.4), same as the feature path: before the pod spends
        # anything real, a human funds the round. The estimate stub returns gate=False,
        # so $0 dry-runs never park here.
        estimate = await run_activity(act.estimate_coding_budget, plan)
        if estimate.gate:
            plan.coding_budget_usd = await self._coding_budget_gate(estimate)
            self._ceiling = max(self._ceiling, self._cost_usd + plan.coding_budget_usd)

        self._enter("engineering_pod")
        # Carry the run's thread anchor + title on the plan so the pod posts its coding
        # play-by-play (stories in flight, QA, each review/CI pass, PR opened) into this thread.
        plan.thread_ts = self._thread_ts
        plan.title = self._title
        pod = await workflow.execute_child_workflow(
            EngineeringPodWorkflow.run,
            plan,
            id=f"{workflow.info().workflow_id}-pod",
        )
        self._cost += pod.cost_tokens
        self._cost_usd += pod.cost_usd
        await self._check_budget()
        # Verdicts on one line — the deploy gate card that follows carries the detail.
        # QA screenshots ride this post as uploads into the thread; when QA passed but
        # nothing was captured, the note says why (honest absence beats silence).
        await self._notify_progress(
            "engineering",
            [f"PR: {pod.pr_url or pod.branch}"] + self._screenshot_lines(pod),
            rows=[
                NoticeRow("coding", pod.story_result.status, pod.story_result.tier or "")
            ]
            + self._pod_verdict_rows(pod),
            image_refs=list(pod.screenshots),
        )

        # QA gate (hard, symmetric with the feature path): halt before deploy on a red QA
        # verdict. ("Tests unavailable in sandbox" is not a fail — the profile declares that.)
        if not pod.qa.passed:
            self._status = Status.QA_FAILED
            self._enter("qa_failed")
            return self._result(
                event, f"QA failed on the pod's output; halted before deploy. {pod.qa.notes}"
            )

        # CI gate (hard, §9.2): never surface a red PR to the deploy gate, let alone merge it.
        if not pod.ci_passed:
            self._status = Status.CI_FAILED
            self._enter("ci_failed")
            return self._result(
                event, f"CI failed on the PR ({pod.pr_url}); halted before deploy. {pod.ci_notes}"
            )

        # Deploy approval gate
        self._enter("deploy_approval")
        await self._notify_gate(
            "deploy",
            [f"PR: {pod.pr_url or pod.branch}"]
            + (
                [f"screenshots: {len(pod.screenshots)} in thread (📸 engineering post)"]
                if pod.screenshots
                else []
            ),
            rows=self._pod_verdict_rows(pod, detailed=True),
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
        return self._result(event, f"Bug fix shipped ({pod.pr_url or pod.branch}).")

    # --- helpers ---------------------------------------------------------------
    async def _coding_budget_gate(self, estimate) -> float:
        """Park until a human funds the coding round (same contract as the feature
        workflow's gate): approve the estimate, set a custom budget, or reject (halt as
        HELD). Timeout -> fund the estimate, so an unanswered card doesn't strand a run
        whose spend stays bounded by the org's own sizing."""
        self._enter(f"coding_budget_gate (est ${estimate.estimate_usd:.2f})")
        self._coding_budget = None
        await self._notify_gate(
            "coding_budget",
            list(estimate.breakdown)
            + ["Fund the estimate, or enter a custom budget in USD below."],
        )
        try:
            await workflow.wait_condition(
                lambda: self._coding_budget is not None,
                timeout=timedelta(days=BUDGET_OVERRIDE_TIMEOUT_DAYS),
            )
        except asyncio.TimeoutError:
            self._log.append(
                f"coding budget gate timed out; funding the estimate (${estimate.estimate_usd:.2f})"
            )
            return estimate.estimate_usd
        decision, amount, approver = self._coding_budget
        if decision == "reject":
            self._status = Status.HELD
            self._log.append(f"coding budget declined by {approver}; halted before the pod")
            raise _BudgetHalt("Coding budget declined; halted before the engineering pod.")
        custom = decision == "custom" and amount > 0
        budget = amount if custom else estimate.estimate_usd
        self._log.append(
            f"coding budget funded: {'custom' if custom else 'estimate'} ${budget:.2f} by {approver}"
        )
        return budget

    async def _check_budget(self) -> None:
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

    async def _notify_gate(
        self, gate: str, context: list[str], rows: list["NoticeRow"] | None = None
    ) -> None:
        """Tell the human-I/O channel this workflow is parked at a gate. Advisory: a
        notification failure must never block or kill the gate — the signal path and the
        gate's timeout still work without it, so failures degrade to a log line.

        ``context`` is header lines; ``rows`` are enumerated items (verdicts) the Slack
        layer renders as scannable rows. The queryable ``gate_context`` keeps the flat
        string shape it always had."""
        rows = rows or []
        self._gate_context = list(context) + [row_line(r) for r in rows]
        notice = GateNotice(
            workflow_id=workflow.info().workflow_id,
            gate=gate,
            title=self._title,
            project=self._project,
            cost_usd=round(self._cost_usd, 4),
            context=list(context),
            rows=list(rows),
            thread_ts=self._thread_ts,
        )
        try:
            await run_activity(act.notify_gate, notice, timeout=NOTIFY_TIMEOUT)
        except Exception:
            self._log.append(f"gate notification failed ({gate}); gate still open on its timeout")

    @staticmethod
    def _pod_verdict_rows(pod, detailed: bool = False) -> list["NoticeRow"]:
        """QA / review / CI as scannable verdict rows, shared by the engineering progress
        post and the deploy gate card. ``detailed`` attaches the notes/URLs a human needs
        to approve the deploy; the progress post stays terse. Pure formatting."""
        ci_detail = ""
        if detailed:
            ci_detail = clip(pod.ci_notes) or ""
            if pod.ci_url:
                ci_detail = (ci_detail + " " if ci_detail else "") + pod.ci_url
        return [
            NoticeRow(
                "QA",
                "passed" if pod.qa.passed else "failed",
                clip(pod.qa.notes) if detailed else "",
            ),
            NoticeRow(
                "review",
                "approved" if pod.review_approved else "unresolved",
                clip(pod.review_notes) if detailed and pod.review_notes else "",
            ),
            NoticeRow("CI", "passed" if pod.ci_passed else "failed", ci_detail),
        ]

    @staticmethod
    def _screenshot_lines(pod) -> list[str]:
        """One honest line about post-QA screenshots for the engineering post: how many
        rode the thread, or why none did (only when QA passed — a failed QA never
        attempts capture). Pure formatting — deterministic."""
        if pod.screenshots:
            return [f"screenshots: {len(pod.screenshots)} in thread"]
        if pod.qa.passed and pod.screenshot_note:
            return [f"screenshots: none ({clip(pod.screenshot_note)})"]
        return []

    async def _notify_progress(
        self,
        stage: str,
        text: list[str],
        rows: list["NoticeRow"] | None = None,
        document_title: str = "",
        document_md: str = "",
        image_refs: list[str] | None = None,
    ) -> None:
        """Post a stage update into the run's Slack thread (advisory, like _notify_gate).
        The first post anchors the thread: its returned ts is stored (deterministically —
        activity results are part of history) and threaded onto every later notice.
        ``rows`` are enumerated items rendered as scannable rows below ``text``."""
        notice = ProgressNotice(
            workflow_id=workflow.info().workflow_id,
            stage=stage,
            title=self._title,
            project=self._project,
            text=list(text),
            rows=list(rows or []),
            document_title=document_title,
            document_md=document_md,
            image_refs=list(image_refs or []),
            thread_ts=self._thread_ts,
            cost_usd=round(self._cost_usd, 4),
        )
        try:
            result = await run_activity(act.notify_progress, notice, timeout=NOTIFY_TIMEOUT)
        except Exception:
            self._log.append(f"progress notification failed ({stage})")
            return
        if not self._thread_ts and result.ts:
            self._thread_ts = result.ts

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
