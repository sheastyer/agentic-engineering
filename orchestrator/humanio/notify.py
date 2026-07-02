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
import re

from temporalio import activity

from orchestrator.humanio.gates import build_blocks, fallback_text, render_progress_text
from orchestrator.humanio.pdf import markdown_to_pdf
from orchestrator.shared.types import GateNotice, NotifyResult, ProgressNotice

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
    """Core logic with an injectable client, unit-testable with a fake for $0.

    When the run has a progress thread, the gate posts into it AND broadcasts to the
    channel (reply_broadcast) — gates need channel-level attention, thread context."""
    try:
        resp = client.chat_postMessage(
            channel=channel,
            text=fallback_text(notice),
            blocks=build_blocks(notice),
            thread_ts=notice.thread_ts or None,
            reply_broadcast=bool(notice.thread_ts),
        )
        ts = resp.get("ts") or ""
        return NotifyResult(delivered=True, note="", ts=ts)
    except Exception as exc:
        _log.warning(
            "slack notify failed for %s gate %r: %s", notice.workflow_id, notice.gate, exc
        )
        return NotifyResult(delivered=False, note=f"slack notify failed: {exc}")


def _document_filename(notice: ProgressNotice, ext: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (notice.document_title or notice.stage).lower()).strip("-")
    return f"{slug or notice.stage}-{notice.workflow_id}.{ext}"


def notify_progress_with_client(notice: ProgressNotice, client, channel: str) -> NotifyResult:
    """Post a stage update into the run's thread; upload document_md as a PDF alongside
    (falling back to the raw markdown if rendering fails). The message is the primary
    outcome: an upload failure still reports delivered=True so the workflow keeps the
    thread anchor — only a failed post is a failed notification."""
    try:
        resp = client.chat_postMessage(
            channel=channel,
            text=render_progress_text(notice),
            thread_ts=notice.thread_ts or None,
        )
        ts = resp.get("ts") or ""
    except Exception as exc:
        _log.warning(
            "slack progress post failed for %s stage %r: %s",
            notice.workflow_id, notice.stage, exc,
        )
        return NotifyResult(delivered=False, note=f"slack progress failed: {exc}")

    note = ""
    if notice.document_md:
        thread = notice.thread_ts or ts
        try:
            pdf = markdown_to_pdf(notice.document_md, notice.document_title or notice.stage)
            if pdf is not None:
                client.files_upload_v2(
                    channel=channel,
                    thread_ts=thread,
                    file=pdf,
                    filename=_document_filename(notice, "pdf"),
                    title=notice.document_title or notice.stage,
                )
            else:
                client.files_upload_v2(
                    channel=channel,
                    thread_ts=thread,
                    content=notice.document_md,
                    filename=_document_filename(notice, "md"),
                    title=notice.document_title or notice.stage,
                )
                note = "pdf render failed; uploaded markdown"
        except Exception as exc:
            _log.warning(
                "slack document upload failed for %s stage %r: %s",
                notice.workflow_id, notice.stage, exc,
            )
            note = f"posted, but document upload failed: {exc}"
    return NotifyResult(delivered=True, note=note, ts=ts)


@activity.defn(name="notify_gate")
def notify_gate_slack(notice: GateNotice) -> NotifyResult:
    return notify_gate_with_client(notice, _build_client(), os.environ["SLACK_CHANNEL_ID"])


@activity.defn(name="notify_progress")
def notify_progress_slack(notice: ProgressNotice) -> NotifyResult:
    # Sync def like notify_gate_slack: blocking HTTP belongs in the worker's thread pool.
    return notify_progress_with_client(notice, _build_client(), os.environ["SLACK_CHANNEL_ID"])
