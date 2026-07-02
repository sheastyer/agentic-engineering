"""Inbound half of the Slack human gates: button click → Temporal signal.

**Socket Mode, not an HTTP webhook** — this runs on a laptop/homelab with no public
ingress, and Socket Mode needs none. Client-side glue exactly like
``orchestrator/intake.py``: zero workflow code. Run it alongside the worker:

    set -a; . ./.env; set +a; ./.venv/bin/python -m orchestrator.humanio

Needs SLACK_BOT_TOKEN (xoxb-, scope chat:write), SLACK_APP_TOKEN (xapp-, Socket Mode)
and SLACK_APPROVER_IDS (comma-separated Slack user ids). Only allowlisted users can
move a gate (M5 SEC: the inbound path must not be able to forge a gate signal) —
anyone else's click is logged and dropped.

The payload-parsing / signal-mapping / message-resolution pieces are pure functions so
they're unit-testable with fakes; only ``main()`` touches slack_sdk (lazily — the
[slack] extra is not needed to import this module).
"""

import asyncio
import json
import logging
import os

from temporalio.client import Client

from orchestrator.humanio.gates import GateAction, signal_for
from orchestrator.shared.config import TEMPORAL_NAMESPACE, TEMPORAL_TARGET

_log = logging.getLogger(__name__)


def parse_block_action(payload: dict) -> GateAction | None:
    """Decode a Slack ``block_actions`` payload into a GateAction, or None if it isn't
    one of ours (wrong type, no actions, or a value that isn't our JSON envelope)."""
    if payload.get("type") != "block_actions":
        return None
    actions = payload.get("actions") or []
    if not actions:
        return None
    try:
        value = json.loads(actions[0].get("value") or "")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    workflow_id = value.get("workflow_id")
    gate = value.get("gate")
    decision = value.get("decision")
    if not (workflow_id and gate and decision):
        return None
    user = payload.get("user") or {}
    return GateAction(
        workflow_id=workflow_id,
        gate=gate,
        decision=decision,
        user_id=user.get("id") or "",
        user_name=user.get("username") or user.get("name") or "",
    )


def load_allowlist(raw: str | None = None) -> set[str]:
    """SLACK_APPROVER_IDS → set of Slack user ids allowed to move gates."""
    raw = os.environ.get("SLACK_APPROVER_IDS", "") if raw is None else raw
    return {part.strip() for part in raw.split(",") if part.strip()}


async def deliver(temporal: Client, action: GateAction) -> str:
    """Signal the parked workflow with the human's decision; returns the outcome line
    rendered back onto the Slack message."""
    mapped = signal_for(action)
    if mapped is None:
        return f"⚠️ unrecognized gate action {action.gate}:{action.decision} — ignored"
    name, args = mapped
    await temporal.get_workflow_handle(action.workflow_id).signal(name, args=args)
    who = action.user_name or action.user_id
    return f"✅ {action.gate}: *{action.decision}* by @{who}"


def resolved_blocks(original_blocks: list[dict], outcome: str) -> list[dict]:
    """The updated message after a decision: same content, buttons removed (no
    double-clicks), outcome appended — so the channel shows who decided what."""
    kept = [b for b in original_blocks if b.get("type") != "actions"]
    kept.append({"type": "context", "elements": [{"type": "mrkdwn", "text": outcome}]})
    return kept


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not (bot_token and app_token):
        raise SystemExit(
            "slack listener needs SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN "
            "(xapp-, Socket Mode) — see .env.example"
        )
    allowlist = load_allowlist()
    if not allowlist:
        raise SystemExit(
            "SLACK_APPROVER_IDS is empty — nobody could approve a gate. Set it to a "
            "comma-separated list of Slack user ids (see .env.example)."
        )

    # Lazy: only the live listener needs the [slack] extra.
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.web import WebClient

    temporal = await Client.connect(TEMPORAL_TARGET, namespace=TEMPORAL_NAMESPACE)
    loop = asyncio.get_running_loop()
    web = WebClient(token=bot_token)
    socket = SocketModeClient(app_token=app_token, web_client=web)

    def on_request(client: SocketModeClient, req: SocketModeRequest) -> None:
        # Runs on the Socket Mode client's thread: ack within Slack's 3s window first,
        # then hop onto the asyncio loop for the Temporal signal.
        if req.type != "interactive":
            return
        client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        payload = req.payload
        action = parse_block_action(payload)
        if action is None:
            return
        if action.user_id not in allowlist:
            _log.warning(
                "dropped gate click from non-allowlisted user %s (@%s) on %s",
                action.user_id, action.user_name, action.workflow_id,
            )
            return
        try:
            outcome = asyncio.run_coroutine_threadsafe(
                deliver(temporal, action), loop
            ).result(timeout=30)
        except Exception as exc:
            _log.error("failed to signal %s: %s", action.workflow_id, exc)
            outcome = f"⚠️ decision received but the signal failed: {exc}"
        _log.info("%s -> %s", action.workflow_id, outcome)

        container = payload.get("container") or {}
        channel, ts = container.get("channel_id"), container.get("message_ts")
        if channel and ts:
            try:
                original = (payload.get("message") or {}).get("blocks") or []
                web.chat_update(
                    channel=channel, ts=ts, text=outcome,
                    blocks=resolved_blocks(original, outcome),
                )
            except Exception as exc:  # cosmetic only — the signal already landed
                _log.warning("chat_update failed: %s", exc)

    socket.socket_mode_request_listeners.append(on_request)
    socket.connect()
    _log.info(
        "slack gate listener up (Socket Mode) — temporal %s, approvers %s",
        TEMPORAL_TARGET, sorted(allowlist),
    )
    await asyncio.Event().wait()  # serve forever; Ctrl-C to stop


if __name__ == "__main__":
    asyncio.run(main())
