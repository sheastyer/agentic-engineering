"""Slack human-I/O unit tests — both directions, $0, no slack_sdk required.

Outbound: GateNotice -> Block Kit blocks (buttons whose values the listener can decode);
the notifier degrades to delivered=False instead of raising (a raise after the coding
pass would discard paid-for work — CLAUDE.md §10).
Inbound: interaction payload -> GateAction -> the right workflow signal, with the
approver allowlist enforced (M5 SEC: no forged gate signals) and the message resolved.
"""

import json

import pytest

from orchestrator.humanio.gates import (
    GATE_BUTTONS,
    GateAction,
    build_blocks,
    build_progress_blocks,
    fallback_text,
    render_progress_text,
    signal_for,
)
from orchestrator.humanio.notify import notify_gate_with_client, notify_progress_with_client
from orchestrator.humanio.pdf import markdown_to_pdf
from orchestrator.humanio.slack_listener import (
    deliver,
    load_allowlist,
    parse_block_action,
    resolved_blocks,
)
from orchestrator.shared.types import GateNotice, ProgressNotice


def _notice(gate: str = "deploy") -> GateNotice:
    return GateNotice(
        workflow_id="feedback-123",
        gate=gate,
        title="Add dark mode",
        project="meal-planner",
        cost_usd=1.87,
        context=["PR: https://github.com/x/y/pull/49", "QA: passed — all green"],
    )


# --- outbound: notice -> blocks ---------------------------------------------------
def test_build_blocks_carries_context_and_decodable_buttons():
    blocks = build_blocks(_notice("deploy"))
    section, actions, footer = blocks
    assert section["type"] == "section" and "Deploy approval" in section["text"]["text"]
    assert "PR: https://github.com/x/y/pull/49" in section["text"]["text"]
    # Run metadata is demoted to the small context footer, not the message body.
    footer_text = footer["elements"][0]["text"]
    assert footer["type"] == "context"
    assert "feedback-123" in footer_text
    assert "meal-planner" in footer_text
    assert "$1.8700" in footer_text
    assert [e["text"]["text"] for e in actions["elements"]] == ["Approve deploy", "Hold"]
    # Round-trip: each button's value is exactly what the listener decodes.
    for element, (_, decision, _style) in zip(actions["elements"], GATE_BUTTONS["deploy"]):
        value = json.loads(element["value"])
        assert value == {"workflow_id": "feedback-123", "gate": "deploy", "decision": decision}


def test_clarification_gate_is_notify_only():
    blocks = build_blocks(_notice("clarification"))
    assert [b["type"] for b in blocks] == ["section", "context"]  # no buttons to forge


def test_fallback_text_names_gate_and_workflow():
    text = fallback_text(_notice())
    assert "Deploy approval" in text and "feedback-123" in text


# --- outbound: the notifier never raises ------------------------------------------
class _FakeWebClient:
    def __init__(self, fail: Exception | None = None, fail_upload: Exception | None = None):
        self.fail = fail
        self.fail_upload = fail_upload
        self.posts: list[dict] = []
        self.uploads: list[dict] = []

    def chat_postMessage(self, **kwargs):
        if self.fail:
            raise self.fail
        self.posts.append(kwargs)
        return {"ok": True, "ts": "111.222"}

    def files_upload_v2(self, **kwargs):
        if self.fail_upload:
            raise self.fail_upload
        self.uploads.append(kwargs)
        return {"ok": True}


def test_notify_posts_to_the_channel():
    client = _FakeWebClient()
    result = notify_gate_with_client(_notice(), client, "C0TEST")
    assert result.delivered is True
    (post,) = client.posts
    assert post["channel"] == "C0TEST"
    assert post["text"] and post["blocks"]


def test_notify_degrades_on_slack_error_instead_of_raising():
    result = notify_gate_with_client(_notice(), _FakeWebClient(fail=RuntimeError("boom")), "C0TEST")
    assert result.delivered is False
    assert "boom" in result.note


def test_gate_with_thread_posts_in_thread_and_broadcasts():
    client = _FakeWebClient()
    notice = _notice()
    notice.thread_ts = "1111.2222"
    notify_gate_with_client(notice, client, "C0TEST")
    (post,) = client.posts
    assert post["thread_ts"] == "1111.2222" and post["reply_broadcast"] is True


# --- outbound: the progress thread --------------------------------------------------
def _progress(stage: str = "brief", thread_ts: str = "1111.2222", **overrides) -> ProgressNotice:
    notice = ProgressNotice(
        workflow_id="feedback-123",
        stage=stage,
        title="Add dark mode",
        project="meal-planner",
        text=["summary: a dark mode toggle"],
        thread_ts=thread_ts,
    )
    for key, value in overrides.items():
        setattr(notice, key, value)
    return notice


def test_progress_root_post_anchors_the_thread():
    client = _FakeWebClient()
    result = notify_progress_with_client(_progress("feedback_received", thread_ts=""), client, "C0TEST")
    assert result.delivered is True and result.ts == "111.222"
    (post,) = client.posts
    assert post["thread_ts"] is None  # the root IS the thread
    assert "Add dark mode" in post["text"] and "feedback-123" in post["text"]
    assert post["blocks"] == build_progress_blocks(_progress("feedback_received", thread_ts=""))


def test_progress_reply_threads_onto_the_anchor():
    client = _FakeWebClient()
    notify_progress_with_client(_progress("brief"), client, "C0TEST")
    (post,) = client.posts
    assert post["thread_ts"] == "1111.2222"
    assert "PM brief" in post["text"]
    assert post["blocks"]


def test_progress_document_uploads_pdf_into_the_thread():
    client = _FakeWebClient()
    notice = _progress("prd", document_title="PRD v1 — Add dark mode",
                       document_md="# PRD\n\nA “smart” draft — with em-dashes.")
    result = notify_progress_with_client(notice, client, "C0TEST")
    assert result.delivered is True and result.note == ""
    (upload,) = client.uploads
    assert upload["thread_ts"] == "1111.2222"
    assert upload["filename"].endswith(".pdf")
    assert bytes(upload["file"][:5]) == b"%PDF-"


def test_progress_upload_failure_still_delivers_the_post():
    client = _FakeWebClient(fail_upload=RuntimeError("files:write missing"))
    notice = _progress("prd", document_title="PRD v1", document_md="# PRD")
    result = notify_progress_with_client(notice, client, "C0TEST")
    assert result.delivered is True and result.ts == "111.222"
    assert "document upload failed" in result.note


def test_progress_post_failure_degrades_without_raising():
    result = notify_progress_with_client(
        _progress(), _FakeWebClient(fail=RuntimeError("boom")), "C0TEST"
    )
    assert result.delivered is False and "boom" in result.note


def test_render_progress_text_root_vs_reply():
    # Fallback text (notification banners): one plain line, id only on the root.
    root = render_progress_text(_progress("feedback_received", thread_ts=""))
    assert root == "New feedback — Add dark mode [feedback-123]"
    reply = render_progress_text(_progress("research"))
    assert reply == "Consumer research — Add dark mode"


def test_progress_blocks_root_vs_reply():
    # Root: headline + body section, run metadata in a small context footer.
    section, footer = build_progress_blocks(_progress("feedback_received", thread_ts=""))
    assert "*New feedback — Add dark mode*" in section["text"]["text"]
    assert "summary: a dark mode toggle" in section["text"]["text"]
    assert footer["type"] == "context"
    assert "meal-planner" in footer["elements"][0]["text"]
    assert "feedback-123" in footer["elements"][0]["text"]
    # Replies stay terse: one section, no repeated ids — those live on the root.
    (section,) = build_progress_blocks(_progress("research"))
    assert "*Consumer research*" in section["text"]["text"]
    assert "feedback-123" not in section["text"]["text"]


def test_markdown_to_pdf_renders_unicode_content():
    pdf = markdown_to_pdf("# Title\n\nA “quoted” em—dash → done.\n\n- one\n- two", "Title")
    assert pdf is not None and pdf[:5] == b"%PDF-"


# --- inbound: payload -> action -> signal ------------------------------------------
def _payload(value: dict | str, user_id: str = "U0SHEA", type_: str = "block_actions") -> dict:
    return {
        "type": type_,
        "user": {"id": user_id, "username": "shea"},
        "container": {"channel_id": "C0TEST", "message_ts": "111.222"},
        "actions": [{"action_id": "gate:deploy:approve",
                     "value": value if isinstance(value, str) else json.dumps(value)}],
    }


def test_parse_block_action_decodes_our_payload():
    action = parse_block_action(
        _payload({"workflow_id": "feedback-123", "gate": "deploy", "decision": "approve"})
    )
    assert action == GateAction(
        workflow_id="feedback-123", gate="deploy", decision="approve",
        user_id="U0SHEA", user_name="shea",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "view_submission"},                       # not a block action
        {"type": "block_actions", "actions": []},          # nothing clicked
        _payload("not json"),                              # value isn't our envelope
        _payload({"workflow_id": "feedback-123"}),         # envelope missing fields
    ],
)
def test_parse_block_action_rejects_foreign_payloads(payload):
    assert parse_block_action(payload) is None


@pytest.mark.parametrize(
    ("gate", "decision", "expected"),
    [
        ("council", "approve", ("submit_human_vote", [True, "shea"])),
        ("council", "reject", ("submit_human_vote", [False, "shea"])),
        ("pm_signoff", "approve", ("submit_pm_signoff", ["approve", "shea"])),
        ("pm_signoff", "revise", ("submit_pm_signoff", ["revise", "shea"])),
        ("deploy", "approve", ("submit_deploy_approval", [True, "shea"])),
        ("deploy", "reject", ("submit_deploy_approval", [False, "shea"])),
        ("budget", "approve", ("submit_budget_decision", [True, "shea"])),
        ("budget", "reject", ("submit_budget_decision", [False, "shea"])),
        ("clarification", "approve", None),   # notify-only gate: no button signal
        ("deploy", "revise", None),           # decision that gate doesn't understand
        ("nonsense", "approve", None),
    ],
)
def test_signal_for_maps_every_gate_decision(gate, decision, expected):
    action = GateAction(
        workflow_id="feedback-123", gate=gate, decision=decision,
        user_id="U0SHEA", user_name="shea",
    )
    assert signal_for(action) == expected


def test_load_allowlist_splits_and_strips():
    assert load_allowlist("U0AAA, U0BBB ,,") == {"U0AAA", "U0BBB"}
    assert load_allowlist("") == set()


class _FakeHandle:
    def __init__(self):
        self.signals: list[tuple[str, list]] = []

    async def signal(self, name, args):
        self.signals.append((name, args))


class _FakeTemporal:
    def __init__(self):
        self.handle = _FakeHandle()
        self.requested_ids: list[str] = []

    def get_workflow_handle(self, workflow_id):
        self.requested_ids.append(workflow_id)
        return self.handle


@pytest.mark.asyncio
async def test_deliver_signals_the_parked_workflow_with_identity():
    temporal = _FakeTemporal()
    outcome = await deliver(
        temporal,
        GateAction(workflow_id="feedback-123", gate="deploy", decision="approve",
                   user_id="U0SHEA", user_name="shea"),
    )
    assert temporal.requested_ids == ["feedback-123"]
    assert temporal.handle.signals == [("submit_deploy_approval", [True, "shea"])]
    assert "approve" in outcome and "shea" in outcome


@pytest.mark.asyncio
async def test_deliver_ignores_unmappable_actions():
    temporal = _FakeTemporal()
    outcome = await deliver(
        temporal,
        GateAction(workflow_id="feedback-123", gate="nonsense", decision="approve",
                   user_id="U0SHEA", user_name="shea"),
    )
    assert temporal.handle.signals == []
    assert "ignored" in outcome


def test_resolved_blocks_strip_buttons_and_show_the_decision():
    blocks = resolved_blocks(build_blocks(_notice("deploy")), "✅ deploy: approve by @shea")
    assert all(b["type"] != "actions" for b in blocks)  # no second click possible
    assert blocks[-1]["type"] == "context"
    assert "approve by @shea" in blocks[-1]["elements"][0]["text"]
