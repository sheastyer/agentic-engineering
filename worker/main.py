"""Temporal worker entrypoint.

Connects to the local dev server and serves every workflow + activity on the org task
queue. Run alongside `temporal server start-dev`:

    ./.venv/bin/python -m worker.main

Three switches — one per plane (CLAUDE.md §2) plus the human-I/O channel (M5):
- ORG_LIVE=1     — every reasoning persona runs live on the Vercel AI Gateway.
- USE_AGENT_CODING=1 — the engineering pod runs a real coding agent on the Claude
  subscription (CODING_AGENT/CODING_SANDBOX/CODING_PR_TARGET knobs).
- ORG_SLACK=1    — gate notifications post to Slack with approve/reject buttons
  (SLACK_BOT_TOKEN/SLACK_CHANNEL_ID; run `python -m orchestrator.humanio` for the
  inbound listener that turns clicks into gate signals).
None set = $0 stubs (the test/dev default).
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from orchestrator.activities.stubs import ALL_ACTIVITIES
from orchestrator.shared.config import TASK_QUEUE, TEMPORAL_NAMESPACE, TEMPORAL_TARGET
from orchestrator.workflows.bug import BugWorkflow
from orchestrator.workflows.consumer_research import ConsumerResearchWorkflow
from orchestrator.workflows.engineering_pod import EngineeringPodWorkflow
from orchestrator.workflows.feature_request import FeatureRequestWorkflow

ALL_WORKFLOWS = [
    FeatureRequestWorkflow,
    BugWorkflow,
    ConsumerResearchWorkflow,
    EngineeringPodWorkflow,
]


def build_activities() -> list:
    """Stub activities by default ($0). ORG_LIVE=1 swaps in every live reasoning persona;
    USE_AGENT_CODING=1 swaps in the coding plane. The swap is by activity name, so the
    workflows never change. (The per-persona USE_AGENT_* flags were M3 scaffolding for
    validating personas one at a time; every persona is validated, so they're retired.)"""
    activities = list(ALL_ACTIVITIES)

    if os.environ.get("ORG_LIVE"):
        # Fail fast at startup, not at the first activity: the reasoning plane is
        # vercel-only, so a missing gateway credential can never produce a live run.
        if not (os.environ.get("AI_GATEWAY_API_KEY") or os.environ.get("VERCEL_OIDC_TOKEN")):
            raise SystemExit(
                "ORG_LIVE=1 but AI_GATEWAY_API_KEY / VERCEL_OIDC_TOKEN is not set — the "
                "reasoning plane runs on the Vercel AI Gateway (see .env.example)."
            )
        from orchestrator.activities import agent_backed as ab

        for stub_name, live in [
            ("triage_feedback", ab.triage_feedback_agent),
            ("pm_draft_brief", ab.pm_draft_brief_agent),
            ("council_agent_vote", ab.council_agent_vote_agent),
            ("consumer_research_persona", ab.consumer_research_persona_agent),
            ("pm_write_prd", ab.pm_write_prd_agent),
            ("pm_revise_prd", ab.pm_revise_prd_agent),
            ("architect_review_prd", ab.architect_review_prd_agent),
            ("architect_plan_stories", ab.architect_plan_stories_agent),
            ("pm_prioritize_bug", ab.pm_prioritize_bug_agent),
            ("review_diff", ab.review_diff_agent),
            ("qa_review", ab.qa_review_agent),
        ]:
            activities = _replace_by_name(activities, stub_name, live)
        logging.info("ORG_LIVE: all reasoning personas live on the Vercel AI Gateway")

    if os.environ.get("USE_AGENT_CODING"):
        from orchestrator.activities.coding_backed import (
            await_ci_agent,
            deploy_agent,
            implement_stories_agent,
            open_pr_agent,
            revise_after_ci_agent,
            revise_after_review_agent,
            update_pr_agent,
        )

        activities = _replace_by_name(activities, "implement_stories", implement_stories_agent)
        activities = _replace_by_name(activities, "revise_after_review", revise_after_review_agent)
        activities = _replace_by_name(activities, "await_ci", await_ci_agent)
        activities = _replace_by_name(activities, "revise_after_ci", revise_after_ci_agent)
        activities = _replace_by_name(activities, "update_pr", update_pr_agent)
        activities = _replace_by_name(activities, "open_pr", open_pr_agent)
        activities = _replace_by_name(activities, "deploy", deploy_agent)
        logging.info(
            "USE_AGENT_CODING: real coding pod — agent=%s sandbox=%s pr_target=%s "
            "(coding draws on the Claude subscription)",
            os.environ.get("CODING_AGENT", "mock"),
            os.environ.get("CODING_SANDBOX", "local"),
            os.environ.get("CODING_PR_TARGET", "local"),
        )

    if os.environ.get("ORG_SLACK"):
        # Same fail-fast pattern as ORG_LIVE: a missing token must never produce a
        # "live" worker whose gates notify nobody.
        if not (os.environ.get("SLACK_BOT_TOKEN") and os.environ.get("SLACK_CHANNEL_ID")):
            raise SystemExit(
                "ORG_SLACK=1 but SLACK_BOT_TOKEN / SLACK_CHANNEL_ID is not set — gate "
                "notifications post to Slack (see .env.example)."
            )
        from orchestrator.humanio.notify import notify_gate_slack, notify_progress_slack

        activities = _replace_by_name(activities, "notify_gate", notify_gate_slack)
        activities = _replace_by_name(activities, "notify_progress", notify_progress_slack)
        logging.info(
            "ORG_SLACK: gate + progress notifications post to Slack channel %s; run the "
            "Socket Mode listener (python -m orchestrator.humanio) so button clicks reach "
            "the gates",
            os.environ.get("SLACK_CHANNEL_ID"),
        )
    return activities


def _replace_by_name(activities: list, stub_name: str, real_activity) -> list:
    """Swap a stub activity for its runner-backed twin (matched by the stub's registered
    activity name), keeping the rest of the list intact."""
    kept = [a for a in activities if a.__name__ != stub_name]
    return [*kept, real_activity]


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(TEMPORAL_TARGET, namespace=TEMPORAL_NAMESPACE)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=ALL_WORKFLOWS,
        activities=build_activities(),
        # The live reasoning activities are SYNC functions (blocking HTTP via the runner —
        # see agent_backed.py's module docstring); they run here, in a thread pool, so they
        # can never stall the event loop that schedules workflow tasks. Sized comfortably
        # above the widest fan-out (research 4 + council 2 + a review/QA in flight).
        activity_executor=ThreadPoolExecutor(max_workers=16),
    )
    logging.info("worker connected to %s; serving task queue %r", TEMPORAL_TARGET, TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
