"""Temporal wire-format round-trips for the shared dataclasses.

Regression for a live failure (2026-07-02): temporalio 1.28's JSON converter mis-decodes
a `(str, Enum)` type hint as a LIST OF CHARACTERS (kind: "bug" -> ['b','u','g']) on
Python 3.14 — `pm_prioritize_bug` then crashed on `triage.kind.value` and the whole
BugWorkflow failed. `enum.StrEnum` round-trips correctly, so the shared enums use it;
this test pins that behavior against future enum/type edits AND temporalio upgrades.
"""

import pytest
from temporalio.converter import DataConverter

from orchestrator.shared.types import FeedbackEvent, FeedbackKind, Triage

_CONV = DataConverter.default


async def _roundtrip(obj, cls):
    payloads = await _CONV.encode([obj])
    [back] = await _CONV.decode(payloads, [cls])
    return back


@pytest.mark.asyncio
async def test_triage_enum_survives_the_activity_boundary():
    t = Triage(kind=FeedbackKind.BUG, priority="P1", needs_clarification=False)
    back = await _roundtrip(t, Triage)
    assert back.kind == FeedbackKind.BUG
    assert isinstance(back.kind, FeedbackKind)      # NOT ['b','u','g']
    assert back.kind.value == "bug"                 # the exact expression that crashed live


@pytest.mark.asyncio
async def test_feedback_event_enum_survives_the_activity_boundary():
    e = FeedbackEvent(
        id="x", kind=FeedbackKind.FEATURE, title="t", body="b",
        submitted_by="s", project="meal-planner",
    )
    back = await _roundtrip(e, FeedbackEvent)
    assert back.kind == FeedbackKind.FEATURE
    assert isinstance(back.kind, FeedbackKind)
