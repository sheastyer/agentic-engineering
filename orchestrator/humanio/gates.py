"""Gate ↔ Slack mapping — the single source of truth for the human-I/O channel.

Shared by the outbound notifier (which renders each gate's buttons) and the inbound
listener (which maps a click back to a workflow signal), so the two can never drift.
Pure functions over plain data: no Slack or Temporal imports, testable for $0.
"""

import json
from dataclasses import dataclass

from orchestrator.shared.types import GateNotice, ProgressNotice

# gate -> [(label, decision, button style)]. A gate with no buttons is notify-only:
# the clarification gate wants free text, which a button can't carry — the human
# answers via the CLI / a direct workflow signal (submit_user_clarification).
GATE_BUTTONS: dict[str, list[tuple[str, str, str]]] = {
    "council": [("Approve", "approve", "primary"), ("Reject", "reject", "danger")],
    "pm_signoff": [("Approve", "approve", "primary"), ("Request revision", "revise", "danger")],
    "deploy": [("Approve deploy", "approve", "primary"), ("Hold", "reject", "danger")],
    "budget": [("Approve override", "approve", "primary"), ("Halt", "reject", "danger")],
    "clarification": [],
}

GATE_LABELS = {
    "council": "Exec council vote",
    "pm_signoff": "PM sign-off",
    "deploy": "Deploy approval",
    "budget": "Budget override",
    "clarification": "Reporter clarification needed",
}
GATE_EMOJI = {
    "council": "🏛️",
    "pm_signoff": "✍️",
    "deploy": "🚀",
    "budget": "💸",
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
    if not notice.thread_ts:
        blocks.append(_meta_footer(notice.project, f"`{notice.workflow_id}`"))
    return blocks


@dataclass
class GateAction:
    """A human's button click, decoded from the Slack interaction payload."""

    workflow_id: str
    gate: str
    decision: str       # "approve" | "reject" | "revise"
    user_id: str        # Slack user id — checked against the approver allowlist
    user_name: str      # for the audit trail / message update


def signal_for(action: GateAction) -> tuple[str, list] | None:
    """Map a decoded click to ``(signal name, args)`` on the parked workflow.

    Every signal carries the approver's identity (M5 SEC). Returns None for a
    gate/decision combination no workflow understands — the listener ignores it."""
    approver = action.user_name or action.user_id
    if action.gate == "pm_signoff":
        if action.decision not in ("approve", "revise"):
            return None
        return "submit_pm_signoff", [action.decision, approver]
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
    blocks.append(
        _meta_footer(
            notice.project, f"`{notice.workflow_id}`", f"${notice.cost_usd:.4f} spent"
        )
    )
    return blocks
