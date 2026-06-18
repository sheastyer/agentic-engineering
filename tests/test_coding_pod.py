"""M4 execution-plane: prove the implement -> test -> QA loop at $0 (mock coding agent).

These are the slice's eval gates in unit-test form (PLAN.md M4):
- seeded-fix positive: a correct edit makes the target's OWN tests pass;
- negative QA: a no-op attempt is caught (no false-green deploys);
- the seeded bug is genuinely failing to begin with (the fixture is real);
- the managed workspace is always torn down (§9.6 cleanup).

No tokens, no auth — the MockCodingAgent applies a known edit so we exercise workspace
prep, file edits, diffing, and the real test command (run as a subprocess). The Claude
Agent SDK path (`ClaudeSDKCodingAgent`) is the same shape, live-validated separately.
"""

import os
import sys

import pytest

from orchestrator.agents.coding import Workspace, implement_and_verify, run_qa
from orchestrator.agents.coding.agents.mock import MockCodingAgent
from orchestrator.agents.coding.types import CodingTask, FileEdit

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "seeded_repo")
# Run the fixture's own suite with the SAME interpreter that's running these tests.
TEST_COMMAND = f"{sys.executable} -m pytest -q verify.py"
FIX = FileEdit(path="mathlib.py", find="return a - b", replace="return a + b")


def _task() -> CodingTask:
    return CodingTask(
        instruction="Fix mathlib.add so it returns the sum of its arguments.",
        test_command=TEST_COMMAND,
        conventions=["Keep the change minimal.", "Do not modify the tests."],
    )


async def test_seeded_fix_makes_target_tests_pass():
    agent = MockCodingAgent(edits=[FIX])
    outcome, qa = await implement_and_verify(agent, _task(), FIXTURE)

    assert qa.passed, f"QA should pass after the fix; notes={qa.notes!r}"
    assert "mathlib.py" in outcome.files_changed
    assert "return a + b" in outcome.diff and outcome.diff.strip()


async def test_noop_attempt_is_caught_by_qa():
    # An agent that changes nothing leaves the seeded bug in place: QA must NOT pass.
    agent = MockCodingAgent(edits=[])
    outcome, qa = await implement_and_verify(agent, _task(), FIXTURE)

    assert not qa.passed, "negative QA: a no-op attempt must be caught (no false green)"
    assert outcome.files_changed == []


def test_seeded_bug_actually_fails_before_any_fix():
    # The fixture is genuinely broken — guards against a vacuous positive test.
    with Workspace(FIXTURE, test_command=TEST_COMMAND) as ws:
        assert not run_qa(ws).passed


def test_diff_excludes_transient_build_artifacts():
    # Running the test command generates __pycache__/*.pyc; those must NOT pollute the diff
    # we hand up as a PR (regression from live validation, 2026-06-16).
    with Workspace(FIXTURE, test_command=TEST_COMMAND) as ws:
        ws.run_tests()
        diff = ws.diff()
    assert "__pycache__" not in diff and ".pyc" not in diff, diff


def test_workspace_is_torn_down_on_exit():
    with Workspace(FIXTURE, test_command=TEST_COMMAND) as ws:
        root = ws._root
        assert root and os.path.isdir(root)
        assert os.path.isdir(ws.path)
    assert root is not None and not os.path.exists(root), "workspace temp dir must be removed"


def test_workspace_torn_down_even_on_error():
    captured = {}
    with pytest.raises(RuntimeError):
        with Workspace(FIXTURE, test_command=TEST_COMMAND) as ws:
            captured["root"] = ws._root
            raise RuntimeError("boom")
    assert not os.path.exists(captured["root"]), "cleanup must run even when the body raises"
