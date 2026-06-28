"""Post-run reasoning tracer — decode a finished workflow's history into a readable
trace of what each agent actually produced.

The queryable WorkflowState only carries stage names + a log; the *content* each persona
produced (the brief, the council rationales, the PRD, the research findings, the coding
diff, the PR) lives in Temporal history as activity results. This walks the parent and its
children (`-research-0`, `-pod`), decodes every ActivityTaskCompleted payload, and prints a
compact per-activity summary so you can verify the reasoning end to end.

    ./.venv/bin/python -m cli.trace feedback-demo-1234abcd
    ./.venv/bin/python -m cli.trace feedback-demo-1234abcd --save .localdata/artifacts.db
    ./.venv/bin/python -m cli.trace feedback-demo-1234abcd --project meal-planner --audit runs

With --audit DIR it also writes a committed audit folder
`DIR/<project>/<date>-<workflow-id>/` containing `report.md` (outcome, council votes,
PRD↔architect iterations, research, stories, pod, cost), `prd.md` (the PM's PRD + revision
history), `trace.json` (the full decoded trace), and `coding.diff` — the durable, reviewable
record the run-org skill commits and opens an audit PR for (see .claude/skills/run-org).

With --save it persists every decoded stage artifact (full payload) into a SQLite table
`trace_artifacts(workflow_id, scope, seq, activity, payload_json, saved_at)` so the
reasoning survives the (ephemeral) dev server and is queryable:

    sqlite3 .localdata/artifacts.db "select activity from trace_artifacts where scope='parent'"

Read-only wrt Temporal: it only fetches history, never signals or mutates anything.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import time

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
    "architect_plan_stories": [("complexity", 8), ("stories", 400)],
    "implement_stories": [("story_id", 28), ("status", 8), ("summary", 200), ("cost_usd", 10)],
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


async def _trace_one(
    client: Client, wf_id: str, label: str
) -> tuple[list[tuple[str, object]], object]:
    """Return (activity rows, workflow completed-result payload). The result is decoded
    from the same history we already fetch — no extra Temporal call — and is None if the
    workflow hasn't completed (e.g. a child still running, or not found)."""
    try:
        handle = client.get_workflow_handle(wf_id)
        history = await handle.fetch_history()
    except Exception as exc:
        print(f"\n── {label} ({wf_id}) — not found: {exc}")
        return [], None

    scheduled: dict[int, str] = {}
    rows: list[tuple[str, object]] = []
    completed: object = None
    for event in history.events:
        sched = event.activity_task_scheduled_event_attributes
        if sched.activity_type.name:
            scheduled[event.event_id] = sched.activity_type.name
        comp = event.activity_task_completed_event_attributes
        if comp.scheduled_event_id:
            name = scheduled.get(comp.scheduled_event_id, "?")
            values = _decode(comp.result)
            rows.append((name, values[0] if values else None))
        wf_done = event.workflow_execution_completed_event_attributes
        if wf_done.result and wf_done.result.payloads:
            decoded = _decode(wf_done.result)
            completed = decoded[0] if decoded else None

    print(f"\n── {label} ({wf_id}) — {len(rows)} activities ──────────────────")
    for name, value in rows:
        _print_activity(name, value)
    return rows, completed


def _persist(db_path: str, wf_id: str, sections: list[tuple[str, list]]) -> int:
    """Write decoded artifacts to SQLite — durable, queryable reasoning independent of the
    (ephemeral) dev server. Re-running for the same workflow replaces its rows (idempotent)."""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trace_artifacts ("
        "workflow_id TEXT, scope TEXT, seq INTEGER, activity TEXT, "
        "payload_json TEXT, saved_at TEXT)"
    )
    conn.execute("DELETE FROM trace_artifacts WHERE workflow_id = ?", (wf_id,))
    saved_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    n = 0
    for scope, rows in sections:
        for i, (activity, value) in enumerate(rows):
            conn.execute(
                "INSERT INTO trace_artifacts VALUES (?, ?, ?, ?, ?, ?)",
                (wf_id, scope, i, activity, json.dumps(value, default=str), saved_at),
            )
            n += 1
    conn.commit()
    conn.close()
    return n


def _rows_named(rows: list[tuple[str, object]], name: str) -> list[object]:
    return [v for n, v in rows if n == name and isinstance(v, dict)]


def _first(rows: list[tuple[str, object]], name: str) -> dict | None:
    hits = _rows_named(rows, name)
    return hits[0] if hits else None


def _g(d: object, key: str, default="—"):
    return d.get(key, default) if isinstance(d, dict) else default


def _prd_versions(parent: list[tuple[str, object]]) -> list[dict]:
    """All PRD revisions in order: the initial author pass then each revise pass."""
    out = _rows_named(parent, "pm_write_prd") + _rows_named(parent, "pm_revise_prd")
    return sorted(out, key=lambda d: d.get("version", 0))


def _build_report(project: str, wf_id: str, sections: list[tuple[str, list]], result) -> str:
    parent = dict(sections).get("parent", [])
    research = dict(sections).get("research", [])
    pod = dict(sections).get("pod", [])
    all_rows = [r for _, rows in sections for r in rows]
    L: list[str] = []
    A = L.append

    A(f"# Org run — {project}")
    A("")
    A(f"- **Workflow:** `{wf_id}`")
    A(f"- **Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Outcome — open_pr lives in the pod child, so search across all scopes.
    pr = _first(all_rows, "open_pr")
    A("")
    A("## Outcome")
    A("")
    A(f"- **Status:** {_g(result, 'status')}")
    A(f"- **Cost:** ${_g(result, 'cost_usd', 0.0)}  ({_g(result, 'cost_tokens', 0)} tokens)")
    A(f"- **Summary:** {_g(result, 'summary')}")
    if pr and pr.get("url"):
        A(f"- **Product PR:** {pr['url']} (opened={pr.get('opened')})")
    stage_log = _g(result, "stage_log", []) if isinstance(result, dict) else []
    if stage_log:
        A("- **Stage log:**")
        for i, stage in enumerate(stage_log, 1):
            A(f"  {i}. {stage}")

    # Brief
    brief = _first(parent, "pm_draft_brief")
    if brief:
        A("")
        A("## Brief")
        A("")
        A(f"- **Problem:** {_g(brief, 'problem')}")
        A(f"- **Summary:** {_g(brief, 'summary')}")
        A(f"- **UI-impacting:** {_g(brief, 'ui_impacting')}")

    # Council
    votes = _rows_named(parent, "council_agent_vote")
    if votes:
        A("")
        A("## Council votes")
        A("")
        A("| Voter | Approve | Rationale |")
        A("| --- | --- | --- |")
        for v in votes:
            rationale = str(_g(v, "rationale")).replace("\n", " ").replace("|", "\\|")
            A(f"| {_g(v, 'voter')} | {_g(v, 'approve')} | {rationale} |")

    # PRD + architect iterations
    prds = _prd_versions(parent)
    reviews = _rows_named(parent, "architect_review_prd")
    if prds or reviews:
        A("")
        A("## PRD & architect iterations")
        A("")
        for p in prds:
            A(f"- **PRD v{_g(p, 'version')}** — see `prd.md`")
        for r in sorted(reviews, key=lambda d: d.get("pass_no", 0)):
            concerns = str(_g(r, "concerns")).replace("\n", " ")
            A(f"- **Architect pass {_g(r, 'pass_no')}** — approved={_g(r, 'approved')}: {concerns}")

    # Research
    personas = _rows_named(research, "consumer_research_persona")
    synth = _first(research, "synthesize_research")
    if personas or synth:
        A("")
        A("## Consumer research")
        A("")
        if synth:
            A(f"- **Overall sentiment:** {_g(synth, 'overall_sentiment')}")
        for p in personas:
            notes = str(_g(p, "notes")).replace("\n", " ")
            A(f"- **{_g(p, 'persona')}** ({_g(p, 'sentiment')}): {notes}")

    # Story plan
    plan = _first(parent, "architect_plan_stories")
    if plan:
        A("")
        A("## Story plan")
        A("")
        A(f"- **Complexity:** {_g(plan, 'complexity')}")
        stories = plan.get("stories")
        if isinstance(stories, list):
            for s in stories:
                if isinstance(s, dict):
                    A(f"  - {s.get('id', '')} {s.get('title', s)}")
                else:
                    A(f"  - {s}")
        elif stories:
            A(f"  - {stories}")

    # Pod
    impls = _rows_named(pod, "implement_stories")
    qa = _first(pod, "qa_review")
    if impls or qa:
        A("")
        A("## Engineering pod")
        A("")
        for s in impls:
            A(
                f"- **{_g(s, 'story_id')}** — status={_g(s, 'status')}, "
                f"cost=${_g(s, 'cost_usd', 0.0)}: {_g(s, 'summary')}"
            )
        if qa:
            A(f"- **QA:** passed={_g(qa, 'passed')} — {_g(qa, 'notes')}")

    # Deploy
    deploy = _first(all_rows, "deploy")
    if deploy:
        A("")
        A("## Deploy")
        A("")
        A(f"- deployed={_g(deploy, 'deployed')}, ref={_g(deploy, 'ref')}")

    A("")
    return "\n".join(L)


def _build_prd(parent: list[tuple[str, object]]) -> str | None:
    versions = _prd_versions(parent)
    if not versions:
        return None
    latest = versions[-1]
    L = [f"# PRD (v{_g(latest, 'version')})", "", str(_g(latest, "content", ""))]
    if len(versions) > 1:
        L += ["", "---", "", "## Revision history", ""]
        for p in versions[:-1]:
            L += [f"### v{_g(p, 'version')}", "", str(_g(p, "content", "")), ""]
    return "\n".join(L)


def _write_audit(
    audit_root: str, project: str, wf_id: str, sections: list[tuple[str, list]],
    result, diffs: list[str],
) -> str:
    """Write a committed audit folder: report.md, prd.md, trace.json, (coding.diff)."""
    run_dir = os.path.join(
        audit_root, project, f"{time.strftime('%Y-%m-%d')}-{wf_id}"
    )
    os.makedirs(run_dir, exist_ok=True)

    with open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(_build_report(project, wf_id, sections, result))

    prd = _build_prd(dict(sections).get("parent", []))
    if prd:
        with open(os.path.join(run_dir, "prd.md"), "w", encoding="utf-8") as fh:
            fh.write(prd)

    trace = {
        "workflow_id": wf_id,
        "project": project,
        "result": result,
        "scopes": {scope: [{"activity": a, "payload": v} for a, v in rows]
                   for scope, rows in sections},
    }
    with open(os.path.join(run_dir, "trace.json"), "w", encoding="utf-8") as fh:
        json.dump(trace, fh, indent=2, default=str)

    if diffs:
        with open(os.path.join(run_dir, "coding.diff"), "w", encoding="utf-8") as fh:
            fh.write("\n\n".join(diffs))

    return run_dir


async def main() -> None:
    ap = argparse.ArgumentParser(description="Decode + (optionally) persist a workflow's reasoning trace.")
    ap.add_argument("workflow_id")
    ap.add_argument("--save", metavar="DB", help="persist decoded artifacts into this SQLite DB")
    ap.add_argument("--audit", metavar="DIR", help="write a committed audit folder under DIR/<project>/<date>-<id>/")
    ap.add_argument("--project", default="unknown", help="project id (for the audit path + report header)")
    args = ap.parse_args()
    wf_id = args.workflow_id
    client = await Client.connect(TEMPORAL_TARGET, namespace=TEMPORAL_NAMESPACE)

    parent_rows, parent_result = await _trace_one(client, wf_id, "PARENT (feature request)")
    research_rows, _ = await _trace_one(client, f"{wf_id}-research-0", "CHILD: consumer research")
    pod_rows, _ = await _trace_one(client, f"{wf_id}-pod", "CHILD: engineering pod")
    sections = [("parent", parent_rows), ("research", research_rows), ("pod", pod_rows)]

    # Dump the full coding diff(s) to a file — too big for the inline trace.
    diffs = []
    for name, value in pod_rows:
        if name == "implement_stories" and isinstance(value, dict) and value.get("diff"):
            diffs.append(f"# story {value.get('story_id')}\n{value['diff']}")
    if diffs:
        out = f"/tmp/steelthread-{wf_id}.diff"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n\n".join(diffs))
        print(f"\n  full coding diff written to {out}")

    if args.save:
        n = _persist(args.save, wf_id, sections)
        print(f"  persisted {n} stage artifacts to {args.save} (table trace_artifacts)")

    if args.audit:
        run_dir = _write_audit(args.audit, args.project, wf_id, sections, parent_result, diffs)
        print(f"  audit written to {run_dir}/ (report.md, prd.md, trace.json)")


if __name__ == "__main__":
    asyncio.run(main())
