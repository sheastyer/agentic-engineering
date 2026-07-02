"""Live Slack notifier — the ORG_SLACK=1 twin of the ``notify_gate`` stub.

A **sync** ``def`` on purpose: it's one blocking HTTP post, so it runs in the worker's
thread-pool ``activity_executor``, never on the event loop (see agent_backed.py's
module docstring — an async body here stalled every workflow task on the worker).

It also **never raises**: the deploy/budget gates run after the coding pass, and an
activity failure there would kill a workflow that's carrying a paid-for diff (§10).
A Slack outage degrades to ``NotifyResult(delivered=False)`` — the gate's signal path
and timeout work without the notification.
"""

import logging
import os

from temporalio import activity

from orchestrator.humanio.gates import build_blocks, fallback_text
from orchestrator.shared.types import GateNotice, NotifyResult

_log = logging.getLogger(__name__)

_client = None


def _build_client():
    """Lazy singleton WebClient — slack_sdk is only imported on the live path, so the
    stub/test worker never needs the [slack] extra installed."""
    global _client
    if _client is None:
        from slack_sdk import WebClient

        _client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    return _client


def notify_gate_with_client(notice: GateNotice, client, channel: str) -> NotifyResult:
    """Core logic with an injectable client, unit-testable with a fake for $0."""
    try:
        resp = client.chat_postMessage(
            channel=channel,
            text=fallback_text(notice),
            blocks=build_blocks(notice),
        )
        return NotifyResult(delivered=True, note=f"slack ts={resp.get('ts') or ''}")
    except Exception as exc:
        _log.warning(
            "slack notify failed for %s gate %r: %s", notice.workflow_id, notice.gate, exc
        )
        return NotifyResult(delivered=False, note=f"slack notify failed: {exc}")


@activity.defn(name="notify_gate")
def notify_gate_slack(notice: GateNotice) -> NotifyResult:
    return notify_gate_with_client(notice, _build_client(), os.environ["SLACK_CHANNEL_ID"])
