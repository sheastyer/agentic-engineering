"""Post-run reasoning tracer — decode a finished workflow's history into a readable
trace of what each agent actually produced.

The queryable WorkflowState only carries stage names + a log; the *content* each persona
produced (the brief, the council rationales, the PRD, the research findings, the coding
diff, the PR) lives in Temporal history as activity results. This walks the parent and its
children (`-research-0`, `-pod`), decodes every ActivityTaskCompleted payload, and prints a
compact per-activity summary so you can verify the reasoning end to end.

    ./.venv/bin/python -m cli.trace feedback-demo-1234abcd

Read-only: it only fetches history, never signals or mutates anything.
"""

import asyncio
import sys

from temporalio.client import Client
from temporalio.converter import default as _default_converter

from orchestrator.shared.config import TEMPORAL_NAMESPACE, TEMPORAL_TARGET

_PC = _default_converter().payload_converter


def _decode(payloads) -> list:
    try:
        return list(_PC.from_payloads(payloads.payloads))
    except Exception as exc:  # pragma: no cover - diagnostic path
        return [f"<undecodable: {exc}>"]


def _short(value, width: int = 240) -> str:
    text = str(value).replace("\n", " ⏎ ")
    return text if len(text) <= width else text[:width] + " …"


# Per-activity pretty printer: (field, width). Falls back to the whole dict.
_VIEWS = {
    "pm_draft_brief": [("summary", 200), ("problem", 200), ("ui_impacting", 10)],
    "council_agent_vote": [("voter", 12), ("approve", 6), ("rationale", 240)],
    "pm_write_prd": [("version", 4), ("content", 320)],
    "architect_review_prd": [("approved", 6), ("pass_no", 3), ("concerns", 240)],
    "pm_revise_prd": [("version", 4), ("content", 240)],
    "consumer_research_persona": [("persona", 28), ("sentiment", 10), ("notes", 220)],
    "synthesize_research": [("overall_sentiment", 10), ("summary_ref", 60)],
    "architect_plan_stories": [("stories", 400)],
    "implement_story": [("story_id", 28), ("status", 8), ("summary", 200), ("cost_usd", 10)],
    "qa_review": [("passed", 6), ("notes", 200)],
    "open_pr": [("opened", 6), ("url", 200), ("note", 120)],
    "deploy": [("deployed", 6), ("ref", 120)],
}


def _print_activity(name: str, value) -> None:
    if isinstance(value, dict) and name in _VIEWS:
        bits = []
        for field, width in _VIEWS[name]:
            if field in value:
                bits.append(f"{field}={_short(value[field], width)}")
        print(f"  ▸ {name}: " + " | ".join(bits))
    else:
        print(f"  ▸ {name}: {_short(value, 320)}")


async def _trace_one(client: Client, wf_id: str, label: str) -> list[tuple[str, object]]:
    try:
        handle = client.get_workflow_handle(wf_id)
        history = await handle.fetch_history()
    except Exception as exc:
        print(f"\n── {label} ({wf_id}) — not found: {exc}")
        return []

    scheduled: dict[int, str] = {}
    rows: list[tuple[str, object]] = []
    for event in history.events:
        sched = event.activity_task_scheduled_event_attributes
        if sched.activity_type.name:
            scheduled[event.event_id] = sched.activity_type.name
        comp = event.activity_task_completed_event_attributes
        if comp.scheduled_event_id:
            name = scheduled.get(comp.scheduled_event_id, "?")
            values = _decode(comp.result)
            rows.append((name, values[0] if values else None))

    print(f"\n── {label} ({wf_id}) — {len(rows)} activities ──────────────────")
    for name, value in rows:
        _print_activity(name, value)
    return rows


async def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m cli.trace <workflow-id>")
        raise SystemExit(2)
    wf_id = sys.argv[1]
    client = await Client.connect(TEMPORAL_TARGET, namespace=TEMPORAL_NAMESPACE)

    await _trace_one(client, wf_id, "PARENT (feature request)")
    await _trace_one(client, f"{wf_id}-research-0", "CHILD: consumer research")
    pod_rows = await _trace_one(client, f"{wf_id}-pod", "CHILD: engineering pod")

    # Dump the full coding diff(s) to a file — too big for the inline trace.
    diffs = []
    for name, value in pod_rows:
        if name == "implement_story" and isinstance(value, dict) and value.get("diff"):
            diffs.append(f"# story {value.get('story_id')}\n{value['diff']}")
    if diffs:
        out = f"/tmp/steelthread-{wf_id}.diff"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n\n".join(diffs))
        print(f"\n  full coding diff written to {out}")


if __name__ == "__main__":
    asyncio.run(main())
