"""Gate ↔ Slack mapping — the single source of truth for the human-I/O channel.

Shared by the outbound notifier (which renders each gate's buttons) and the inbound
listener (which maps a click back to a workflow signal), so the two can never drift.
Pure functions over plain data: no Slack or Temporal imports, testable for $0.
"""

import json
from dataclasses import dataclass

from orchestrator.shared.types import GateNotice

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
    """Render a GateNotice as Block Kit: header, context lines, and the gate's buttons.

    Each button's ``value`` carries ``{workflow_id, gate, decision}`` as JSON — the
    listener decodes exactly that to signal the right workflow."""
    label = GATE_LABELS.get(notice.gate, notice.gate)
    lines = [
        f"*workflow:* `{notice.workflow_id}`",
        f"*project:* {notice.project}",
        f"*spend so far:* ${notice.cost_usd:.4f}",
        *notice.context,
    ]
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{label} — {notice.title}"[:150]},
        },
        {
            "type": "section",
            # Block Kit caps a section's text at 3000 chars; context lines are already
            # clipped workflow-side, so this only trims pathological titles/fan-outs.
            "text": {"type": "mrkdwn", "text": "\n".join(lines)[:3000]},
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
    return blocks
