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
        CODING_ACTIVITY_TIMEOUT_MAX_MINUTES,
        CODING_ACTIVITY_TIMEOUT_MINUTES,
        CODING_MAX_BUDGET_USD,
        MAX_CI_FIX_PASSES,
        MAX_QA_FIX_PASSES,
        MAX_REVIEW_PASSES,
        PREVIEW_ACTIVITY_TIMEOUT_MINUTES,
    )
    from orchestrator.shared.types import CIResult, NoticeRow, PodResult, ProgressNotice, StoryPlan
    from orchestrator.workflows.common import NOTIFY_TIMEOUT, clip, run_activity

# Coding + PR-open activities run a real agent and the target's tests in a sandbox — give
# them minutes, not the 30s reasoning default. Deterministic (a constant timedelta).
_CODING_TIMEOUT = timedelta(minutes=CODING_ACTIVITY_TIMEOUT_MINUTES)


def _coding_timeout(plan: StoryPlan) -> timedelta:
    """Start-to-close for the pod's CODING passes (implement + revises). A human-funded
    budget (plan.coding_budget_usd, set at the pre-pod coding-budget gate) buys
    proportionally more wall-clock: the flat default is sized for default-cap runs, and a
    funded multi-story run cannot finish inside it — observed live 2026-07-07, when a
    $15-funded 5-story run timed out at 20:00 and the Temporal retry discarded the whole
    paid pass. Capped so a hung session can't park the workflow for days; an unfunded plan
    (coding_budget_usd == 0) keeps the flat default, so old histories replay unchanged.
    Pure arithmetic on the plan + config constants — deterministic."""
    scale = max(1.0, plan.coding_budget_usd / CODING_MAX_BUDGET_USD)
    minutes = min(CODING_ACTIVITY_TIMEOUT_MINUTES * scale, CODING_ACTIVITY_TIMEOUT_MAX_MINUTES)
    return timedelta(minutes=minutes)
# The await-CI activity polls the PR's checks until they conclude — minutes; its start-to-close
# must exceed the internal poll timeout (CI_POLL_TIMEOUT_MINUTES). Deterministic constant.
_CI_TIMEOUT = timedelta(minutes=CI_ACTIVITY_TIMEOUT_MINUTES)

# Screenshot capture boots the target's preview stack (compose build = minutes). Constant.
_PREVIEW_TIMEOUT = timedelta(minutes=PREVIEW_ACTIVITY_TIMEOUT_MINUTES)


@workflow.defn
class EngineeringPodWorkflow:
    @workflow.run
    async def run(self, plan: StoryPlan) -> PodResult:
        # The parent carries its Slack thread anchor on the plan (like coding_budget_usd) so the
        # pod can post a play-by-play (coding in flight, QA, each review/CI pass, PR opened) into
        # the SAME thread — the coding round is the long, opaque part of a run, and this makes it
        # observable. No thread (Slack off / $0 dry-run) => _notify is a no-op, nothing changes.
        # (Kept a single `plan` arg: extra run() params break Temporal's arg-type decoding for
        # callers that pass only the plan — the payload then arrives as a raw dict.)
        self._thread_ts = plan.thread_ts
        self._title = plan.title

        # Costs accrue across the (possibly looping) stages, so accumulate as we go rather than
        # summing only the final result — a coding/review pass we later supersede still spent.
        cost = 0
        cost_usd = 0.0

        def _spend(*stage_results) -> None:
            nonlocal cost, cost_usd
            for r in stage_results:
                cost += r.cost_tokens
                cost_usd += r.cost_usd

        # Coding passes get wall-clock proportional to the human-funded budget (see
        # _coding_timeout) — a funded heavy run must not die at the default-cap timeout.
        coding_timeout = _coding_timeout(plan)

        # Announce the stories going into the shared workspace — "what's in flight". (The pod
        # implements them in one session, so this list is the finest-grained view the
        # orchestration plane has; per-story detail lands in the coding summary below.)
        await self._notify(
            "coding",
            [f"Implementing {_n(len(plan.stories), 'story', 'stories')} in one workspace…"],
            rows=[
                NoticeRow(s.id, f"{s.tier} · est {s.estimate}", clip(s.title))
                for s in plan.stories
            ],
        )

        # One pod session implements the whole ordered story plan in a single workspace
        # (orchestrating per-story subagents itself when the plan has multiple stories).
        result = await run_activity(act.implement_stories, plan, timeout=coding_timeout)
        qa = await run_activity(act.qa_review, plan.project, [result])
        _spend(result, qa)
        await self._notify(
            "qa",
            ["Initial coding pass complete."],
            rows=[
                NoticeRow("coding", result.status, clip(result.summary)),
                NoticeRow("QA", "passed" if qa.passed else "failed", clip(qa.notes)),
            ],
        )

        # Bounded QA -> fix loop: re-run the implementation once if QA failed (§10 cap).
        fixes = 0
        while not qa.passed and fixes < MAX_QA_FIX_PASSES:
            fixes += 1
            await self._notify(
                "coding", [f"QA failed — re-coding (fix pass {fixes}/{MAX_QA_FIX_PASSES})."]
            )
            result = await run_activity(act.implement_stories, plan, timeout=coding_timeout)
            qa = await run_activity(act.qa_review, plan.project, [result])
            _spend(result, qa)
            await self._notify(
                "qa",
                [f"QA re-check (fix pass {fixes})."],
                rows=[NoticeRow("QA", "passed" if qa.passed else "failed", clip(qa.notes))],
            )

        # Bounded code-review -> revise loop, BEFORE the PR opens (§10 cap). A reasoning-plane
        # reviewer critiques the diff; if it requires changes, the developer (coding pod) revises
        # against that feedback and we re-QA + re-review. Each revise pass is a full coding run on
        # the subscription, so MAX_REVIEW_PASSES is a hard cost lever. The human only ever sees a
        # PR that has already been through this loop.
        review = await run_activity(act.review_diff, plan, result)
        _spend(review)
        await self._notify(
            "code_review",
            ["Reviewer critiqued the diff."],
            rows=[self._review_row(review)],
        )
        reviews = 0
        while not review.approved and reviews < MAX_REVIEW_PASSES:
            reviews += 1
            await self._notify(
                "code_review",
                [f"Revising against review feedback (pass {reviews}/{MAX_REVIEW_PASSES})."],
            )
            result = await run_activity(
                act.revise_after_review, plan, result, review, timeout=coding_timeout
            )
            qa = await run_activity(act.qa_review, plan.project, [result])
            review = await run_activity(act.review_diff, plan, result)
            _spend(result, qa, review)
            await self._notify(
                "code_review",
                [f"Re-review (pass {reviews})."],
                rows=[
                    NoticeRow("QA", "passed" if qa.passed else "failed", clip(qa.notes)),
                    self._review_row(review),
                ],
            )

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
        await self._notify(
            "pr_opened",
            [f"PR: {pr.url}" if pr.opened else f"PR not opened — {pr.note or 'no changes'}"],
        )

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
            await self._notify("ci", ["Waiting for the PR's CI to conclude…"])
            ci = await run_activity(act.await_ci, plan.project, branch, pr.url, timeout=_CI_TIMEOUT)
            _spend(ci)
            await self._notify("ci", [], rows=[self._ci_row(ci)])
            ci_passes = 0
            while not ci.passed and ci_passes < MAX_CI_FIX_PASSES:
                ci_passes += 1
                await self._notify(
                    "ci", [f"CI red — pushing a fix (pass {ci_passes}/{MAX_CI_FIX_PASSES})."]
                )
                result = await run_activity(
                    act.revise_after_ci, plan, result, ci, timeout=coding_timeout
                )
                upd = await run_activity(act.update_pr, plan.project, branch, [result], timeout=_CODING_TIMEOUT)
                ci = await run_activity(act.await_ci, plan.project, branch, pr.url, timeout=_CI_TIMEOUT)
                _spend(result, upd, ci)
                await self._notify(
                    "ci", [f"CI re-check (fix pass {ci_passes})."], rows=[self._ci_row(ci)]
                )

        # Post-QA visual evidence: screenshot the app with the FINAL diff applied (after
        # the CI fix loop, so the shots match exactly what the deploy gate would merge).
        # Only when QA passed — the user-facing promise is "screenshots of successful QA".
        # Advisory: the activity converts every failure into captured=False + a note, so
        # a broken preview can never kill a pod that's carrying a finished diff (§10).
        screenshots: list[str] = []
        screenshot_note = ""
        if qa.passed:
            shots = await run_activity(
                act.capture_screenshots, plan.project, [result], timeout=_PREVIEW_TIMEOUT
            )
            _spend(shots)
            screenshots = list(shots.refs)
            screenshot_note = shots.note

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
            screenshots=screenshots,
            screenshot_note=screenshot_note,
            cost_tokens=cost,
            cost_usd=cost_usd,
        )

    # --- Slack play-by-play (advisory, threaded onto the parent's run thread) -----------
    async def _notify(self, stage: str, text: list[str], rows: list["NoticeRow"] | None = None) -> None:
        """Post one pod step into the run's thread. A no-op without a thread anchor (Slack
        off / dry-run). Advisory like the parent's notifier: a failed post degrades to a
        silent skip — it must never block or fail a pod that's carrying paid-for coding."""
        if not self._thread_ts:
            return
        notice = ProgressNotice(
            workflow_id=workflow.info().workflow_id,
            stage=stage,
            title=self._title,
            project="",
            text=list(text),
            rows=list(rows or []),
            thread_ts=self._thread_ts,
        )
        try:
            await run_activity(act.notify_progress, notice, timeout=NOTIFY_TIMEOUT)
        except Exception:
            pass  # visibility is advisory; never let a notification failure touch the pod

    @staticmethod
    def _review_row(review) -> "NoticeRow":
        """The reviewer's verdict as a scannable row — approved, or the changes it wants."""
        if review.approved:
            return NoticeRow("review", "approved", clip(review.notes))
        detail = clip("; ".join(review.required_changes) or review.notes)
        return NoticeRow("review", "changes requested", detail)

    @staticmethod
    def _ci_row(ci) -> "NoticeRow":
        return NoticeRow("CI", ci.status, clip(ci.failing_summary or ""))


def _n(count: int, singular: str, plural: str) -> str:
    """'1 story' / '3 stories' — pure formatting, deterministic."""
    return f"{count} {singular if count == 1 else plural}"
