"""Temporal worker entrypoint (M0/M1).

Connects to the local dev server and serves every workflow + activity on the org task
queue. Run alongside `temporal server start-dev`:

    ./.venv/bin/python -m worker.main
"""

import asyncio
import logging
import os

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
    """Stub activities by default ($0). Env toggles swap in real runner-backed ones, one
    at a time, as they're validated in M3 — the swap is by activity name."""
    activities = list(ALL_ACTIVITIES)
    provider = os.environ.get("MODEL_PROVIDER", "anthropic")
    if os.environ.get("USE_AGENT_BRIEF"):
        from orchestrator.activities.agent_backed import pm_draft_brief_agent

        activities = _replace_by_name(activities, "pm_draft_brief", pm_draft_brief_agent)
        logging.info("USE_AGENT_BRIEF: real PM brief authoring (Opus) via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_TRIAGE"):
        from orchestrator.activities.agent_backed import triage_feedback_agent

        activities = _replace_by_name(activities, "triage_feedback", triage_feedback_agent)
        logging.info("USE_AGENT_TRIAGE: real triage via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_COUNCIL"):
        from orchestrator.activities.agent_backed import council_agent_vote_agent

        activities = _replace_by_name(activities, "council_agent_vote", council_agent_vote_agent)
        logging.info("USE_AGENT_COUNCIL: real council votes via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_RESEARCH"):
        from orchestrator.activities.agent_backed import consumer_research_persona_agent

        activities = _replace_by_name(
            activities, "consumer_research_persona", consumer_research_persona_agent
        )
        logging.info("USE_AGENT_RESEARCH: real synthetic-user panel via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_PRD_REVISE"):
        from orchestrator.activities.agent_backed import pm_revise_prd_agent

        activities = _replace_by_name(activities, "pm_revise_prd", pm_revise_prd_agent)
        logging.info("USE_AGENT_PRD_REVISE: real PRD revision via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_PRD_AUTHOR"):
        from orchestrator.activities.agent_backed import pm_write_prd_agent

        activities = _replace_by_name(activities, "pm_write_prd", pm_write_prd_agent)
        logging.info("USE_AGENT_PRD_AUTHOR: real PRD authoring (Opus) via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_ARCH_REVIEW"):
        from orchestrator.activities.agent_backed import architect_review_prd_agent

        activities = _replace_by_name(activities, "architect_review_prd", architect_review_prd_agent)
        logging.info("USE_AGENT_ARCH_REVIEW: real architect PRD review (Opus) via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_STORY_PLAN"):
        from orchestrator.activities.agent_backed import architect_plan_stories_agent

        activities = _replace_by_name(activities, "architect_plan_stories", architect_plan_stories_agent)
        logging.info("USE_AGENT_STORY_PLAN: real architect story planning (Opus) via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_BUG_PRIORITY"):
        from orchestrator.activities.agent_backed import pm_prioritize_bug_agent

        activities = _replace_by_name(activities, "pm_prioritize_bug", pm_prioritize_bug_agent)
        logging.info("USE_AGENT_BUG_PRIORITY: real PM bug prioritization (Haiku) via MODEL_PROVIDER=%s", provider)
    if os.environ.get("USE_AGENT_REVIEW"):
        from orchestrator.activities.agent_backed import review_diff_agent

        activities = _replace_by_name(activities, "review_diff", review_diff_agent)
        logging.info(
            "USE_AGENT_REVIEW: real pre-PR code review (Sonnet, reasoning) via MODEL_PROVIDER=%s",
            provider,
        )
    if os.environ.get("USE_AGENT_CODING"):
        from orchestrator.activities.coding_backed import (
            await_ci_agent,
            deploy_agent,
            fix_bug_agent,
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
        activities = _replace_by_name(activities, "fix_bug", fix_bug_agent)
        activities = _replace_by_name(activities, "open_pr", open_pr_agent)
        activities = _replace_by_name(activities, "deploy", deploy_agent)
        logging.info(
            "USE_AGENT_CODING: real coding pod — agent=%s sandbox=%s pr_target=%s "
            "(coding draws on the Claude subscription; reasoning via MODEL_PROVIDER=%s)",
            os.environ.get("CODING_AGENT", "mock"),
            os.environ.get("CODING_SANDBOX", "local"),
            os.environ.get("CODING_PR_TARGET", "local"),
            provider,
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
    )
    logging.info("worker connected to %s; serving task queue %r", TEMPORAL_TARGET, TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
