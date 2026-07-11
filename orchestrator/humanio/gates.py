"""Gate ↔ Slack mapping — the single source of truth for the human-I/O channel.

Shared by the outbound notifier (which renders each gate's buttons) and the inbound
listener (which maps a click back to a workflow signal), so the two can never drift.
Pure functions over plain data: no Slack or Temporal imports, testable for $0.
"""

import json
from dataclasses import dataclass

from orchestrator.shared.types import GateNotice, NoticeRow, ProgressNotice

# A row's short status word -> a colored emoji, so a card reads at a glance (green = go,
# red = stop, gray = inconclusive, yellow = partial). Matched on the first word of the
# status, lowercased; an unmapped status just renders without an emoji.
_STATUS_EMOJI = {
    "approve": "🟢", "approved": "🟢", "pass": "🟢", "passed": "🟢", "ok": "🟢",
    "complete": "🟢", "completed": "🟢", "done": "🟢", "shipped": "🟢", "yes": "🟢",
    "reject": "🔴", "rejected": "🔴", "fail": "🔴", "failed": "🔴", "hold": "🔴",
    "held": "🔴", "halt": "🔴", "blocked": "🔴", "error": "🔴", "no": "🔴",
    "unresolved": "⚪", "skipped": "⚪", "skip": "⚪", "pending": "⚪", "n/a": "⚪",
    "unavailable": "⚪", "none": "⚪",
    "partial": "🟡", "warn": "🟡", "warning": "🟡", "revise": "🟡", "changes": "🟡",
}


def _status_emoji(status: str) -> str:
    first = status.strip().lower().split()
    return _STATUS_EMOJI.get(first[0], "") if first else ""


def render_rows(rows: list[NoticeRow]) -> str:
    """Render enumerated notice rows as one scannable mrkdwn block: each row is a bold
    label with a status emoji, and its detail is demoted into a blockquote underneath.
    Beats a run-on paragraph — the human sees each vote / verdict / story as its own,
    clearly delimited line instead of a wall of text."""
    out: list[str] = []
    for r in rows:
        emoji = _status_emoji(r.status)
        head = " ".join(p for p in (emoji, f"*{r.label}*") if p)
        if r.status:
            head += f"  {r.status}"
        if r.detail:
            head += f"\n> {r.detail}"
        out.append(head)
    return "\n\n".join(out)


def row_line(row: NoticeRow) -> str:
    """Flatten a row to one plain string for the queryable ``gate_context`` state (and
    plain-text fallbacks) — the same ``label: status — detail`` shape the workflows used
    before rows existed, so queries/audits keep reading the way they always did."""
    parts = f"{row.label}: {row.status}" if row.status else row.label
    return f"{parts} — {row.detail}" if row.detail else parts

# gate -> [(label, decision, button style)]. A gate with no buttons is input-only
# (the clarification gate's answer is free text — see GATE_INPUTS below).
GATE_BUTTONS: dict[str, list[tuple[str, str, str]]] = {
    "council": [("Approve", "approve", "primary"), ("Reject", "reject", "danger")],
    "pm_signoff": [("Approve", "approve", "primary"), ("Request revision", "revise", "danger")],
    "deploy": [("Approve deploy", "approve", "primary"), ("Hold", "reject", "danger")],
    "budget": [("Approve override", "approve", "primary"), ("Halt", "reject", "danger")],
    "coding_budget": [("Fund estimate", "approve", "primary"), ("Halt run", "reject", "danger")],
    "clarification": [],
}

# gate -> (decision, label, placeholder) for gates that also take FREE TEXT, rendered as
# a plain_text_input under the buttons (dispatch on Enter). What the text means per gate:
#   pm_signoff    — revision feedback: typing = "request revision", and the PM agent
#                   revises the PRD against exactly these words instead of a generic nudge.
#   coding_budget — a custom dollar amount instead of the estimate.
#   clarification — the answer to the reporter question (this gate's only control).
GATE_INPUTS: dict[str, tuple[str, str, str]] = {
    "pm_signoff": (
        "revise",
        "Or request revision with feedback — press Enter",
        "e.g. drop the CSV export; clarify the empty state",
    ),
    "coding_budget": (
        "custom",
        "Or fund a custom budget (USD) — press Enter",
        "e.g. 6.50",
    ),
    "clarification": (
        "answer",
        "Answer the reporter's question — press Enter",
        "your clarification reaches the engineering pod",
    ),
}

GATE_LABELS = {
    "council": "Exec council vote",
    "pm_signoff": "PM sign-off",
    "deploy": "Deploy approval",
    "budget": "Budget override",
    "coding_budget": "Coding budget",
    "clarification": "Reporter clarification needed",
}
GATE_EMOJI = {
    "council": "🏛️",
    "pm_signoff": "✍️",
    "deploy": "🚀",
    "budget": "💸",
    "coding_budget": "💰",
    "clarification": "❓",
}

# Progress-thread stages (ProgressNotice.stage) — presentation only, so it lives here
# in the Slack layer, not in workflow code.
STAGE_LABELS = {
    "feedback_received": "New feedback",
    "brief": "PM brief",
    "council": "Council decision",
    "prd": "PRD",
    "mocks": "UX mocks",
    "research": "Consumer research",
    "stories": "Story plan",
    "engineering": "Engineering pod",
    # Fine-grained engineering-pod steps (posted by EngineeringPodWorkflow into the same
    # thread) so a coding run is a play-by-play, not one silent block until it finishes.
    "coding": "Coding",
    "qa": "QA",
    "code_review": "Code review",
    "pr_opened": "PR opened",
    "ci": "CI",
    "triage": "Triage",
    "done": "Run finished",
}
STAGE_EMOJI = {
    "feedback_received": "📥",
    "brief": "📝",
    "council": "🏛️",
    "prd": "📄",
    "mocks": "🎨",
    "research": "🔬",
    "stories": "🧱",
    "engineering": "🤖",
    "coding": "🛠️",
    "qa": "🧪",
    "code_review": "🔎",
    "pr_opened": "🔀",
    "ci": "⚙️",
    "triage": "🩺",
    "done": "🏁",
}


def _meta_footer(*parts: str) -> dict:
    """The run's metadata (project, workflow id, spend) as a context block — Slack
    renders it small and gray, so it stops competing with the content above it."""
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": " · ".join(p for p in parts if p)[:3000]}],
    }


def _append_rows_section(blocks: list[dict], rows: list[NoticeRow]) -> None:
    """Append the enumerated rows as their own mrkdwn section (kept separate from the
    header so the divider between 'what this is' and 'the items' is visual, not just
    textual). A no-op when there are no rows, so notify-only cards stay unchanged."""
    if not rows:
        return
    blocks.append(
        {"type": "section", "text": {"type": "mrkdwn", "text": render_rows(rows)[:3000]}}
    )


def render_progress_text(notice: ProgressNotice) -> str:
    """Plain one-liner for the `text` field (notification banners, screen readers) —
    the readable message is the blocks (build_progress_blocks)."""
    label = STAGE_LABELS.get(notice.stage, notice.stage)
    if not notice.thread_ts:
        return f"{label} — {notice.title} [{notice.workflow_id}]"
    return f"{label} — {notice.title}"


def build_progress_blocks(notice: ProgressNotice) -> list[dict]:
    """One section per stage: bold headline, detail lines under it. Only the thread
    root carries the run metadata (as a small context footer) — replies stay terse,
    the ids live on the root."""
    emoji = STAGE_EMOJI.get(notice.stage, "•")
    label = STAGE_LABELS.get(notice.stage, notice.stage)
    if not notice.thread_ts:
        head = f"{emoji} *{label} — {notice.title}*"
    else:
        head = f"{emoji} *{label}*"
    body = "\n".join(notice.text)
    blocks = [
        {
            "type": "section",
            # Block Kit caps a section's text at 3000 chars; detail lines are already
            # clipped workflow-side, so this only trims pathological bodies.
            "text": {"type": "mrkdwn", "text": (head + (f"\n{body}" if body else ""))[:3000]},
        }
    ]
    _append_rows_section(blocks, notice.rows)
    if not notice.thread_ts:
        blocks.append(_meta_footer(notice.project, f"`{notice.workflow_id}`"))
    return blocks


@dataclass
class GateAction:
    """A human's button click (or text-input submit), decoded from the Slack payload."""

    workflow_id: str
    gate: str
    decision: str       # "approve" | "reject" | "revise" | "custom" (text input)
    user_id: str        # Slack user id — checked against the approver allowlist
    user_name: str      # for the audit trail / message update
    text: str = ""      # what the human typed, for text-input decisions ("custom")


def escape_mrkdwn(text: str) -> str:
    """Escape Slack's mrkdwn control characters in human-typed text before the bot
    echoes it back into a message. ``<`` opens Slack's special sequences — a typed
    ``<!channel>`` would otherwise fire a mass notification when re-posted."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def parse_dollars(raw: str) -> float | None:
    """A human-typed dollar amount ("6.50", "$12") -> float, or None when it doesn't
    parse or fails the sanity bounds (0 < value <= 500 — a guard against absurd inputs;
    the coding pod should never be funded past that in one click)."""
    try:
        value = float((raw or "").strip().lstrip("$").replace(",", ""))
    except ValueError:
        return None
    if not 0 < value <= 500:
        return None
    return round(value, 2)


def signal_for(action: GateAction) -> tuple[str, list] | None:
    """Map a decoded click to ``(signal name, args)`` on the parked workflow.

    Every signal carries the approver's identity (M5 SEC). Returns None for a
    gate/decision combination no workflow understands — the listener ignores it."""
    approver = action.user_name or action.user_id
    if action.gate == "pm_signoff":
        if action.decision not in ("approve", "revise"):
            return None
        # Text typed into the card's input is the PM's revision feedback; a bare
        # button click sends none (the workflow falls back to a generic revise).
        return "submit_pm_signoff", [action.decision, approver, action.text.strip()]
    if action.gate == "clarification":
        if action.decision != "answer" or not action.text.strip():
            return None
        return "submit_user_clarification", [action.text.strip(), approver]
    if action.gate == "coding_budget":
        if action.decision == "custom":
            amount = parse_dollars(action.text)
            if amount is None:
                return None
            return "submit_coding_budget", ["custom", amount, approver]
        if action.decision not in ("approve", "reject"):
            return None
        return "submit_coding_budget", [action.decision, 0.0, approver]
    if action.decision not in ("approve", "reject"):
        return None
    approve = action.decision == "approve"
    if action.gate == "council":
        return "submit_human_vote", [approve, approver]
    if action.gate == "deploy":
        return "submit_deploy_approval", [approve, approver]
    if action.gate == "budget":
        return "submit_budget_decision", [approve, approver]
    return None


def fallback_text(notice: GateNotice) -> str:
    """Plain-text summary (Slack notification banners, screen readers)."""
    label = GATE_LABELS.get(notice.gate, notice.gate)
    return f"{label} — {notice.title} [{notice.workflow_id}]"


def build_blocks(notice: GateNotice) -> list[dict]:
    """Render a GateNotice as Block Kit: one section (bold headline + context lines),
    the gate's buttons, and the run metadata demoted to a small context footer —
    the buttons are what should stand out, not the ids.

    Each button's ``value`` carries ``{workflow_id, gate, decision}`` as JSON — the
    listener decodes exactly that to signal the right workflow."""
    label = GATE_LABELS.get(notice.gate, notice.gate)
    emoji = GATE_EMOJI.get(notice.gate, "🔔")
    head = f"{emoji} *{label} — {notice.title}*"
    body = "\n".join(notice.context)
    blocks: list[dict] = [
        {
            "type": "section",
            # Block Kit caps a section's text at 3000 chars; context lines are already
            # clipped workflow-side, so this only trims pathological titles/fan-outs.
            "text": {"type": "mrkdwn", "text": (head + (f"\n{body}" if body else ""))[:3000]},
        },
    ]
    _append_rows_section(blocks, notice.rows)
    buttons = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": label_},
            "style": style,
            "action_id": f"gate:{notice.gate}:{decision}",
            "value": json.dumps(
                {"workflow_id": notice.workflow_id, "gate": notice.gate, "decision": decision}
            ),
        }
        for label_, decision, style in GATE_BUTTONS.get(notice.gate, [])
    ]
    if buttons:
        blocks.append({"type": "actions", "block_id": f"gate:{notice.gate}", "elements": buttons})
    if notice.gate in GATE_INPUTS:
        # Free-text gates: a text input that fires a block_actions payload on Enter
        # (dispatch_action is required for inputs in messages). Buttons carry our JSON
        # envelope in ``value``; an input's value IS the typed text, so the envelope
        # rides ``block_id`` instead — the listener decodes both shapes.
        decision, input_label, placeholder = GATE_INPUTS[notice.gate]
        blocks.append(
            {
                "type": "input",
                "dispatch_action": True,
                "block_id": json.dumps(
                    {"workflow_id": notice.workflow_id, "gate": notice.gate, "decision": decision}
                ),
                "label": {"type": "plain_text", "text": input_label},
                "element": {
                    "type": "plain_text_input",
                    "action_id": f"gate:{notice.gate}:{decision}",
                    "placeholder": {"type": "plain_text", "text": placeholder},
                    "dispatch_action_config": {"trigger_actions_on": ["on_enter_pressed"]},
                    # Defensive cap: this text flows into LLM prompts (revision concerns,
                    # the bug context) — bound it at the source.
                    "max_length": 2000,
                },
            }
        )
    blocks.append(
        _meta_footer(
            notice.project, f"`{notice.workflow_id}`", f"${notice.cost_usd:.4f} spent"
        )
    )
    return blocks
