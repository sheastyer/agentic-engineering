"""The execution plane (CLAUDE.md §2) — coding agents that read/edit code and run tests.

Separate runtime from the reasoning plane (`orchestrator/agents/runner.py` +
`ModelProvider`): short-lived but intense, file/shell/test-tool driven, run inside a
**managed per-run workspace** (D4) behind a pluggable **sandbox** seam (D9). The org
orchestrates this from Temporal *activities* — never from workflow code (§9.1).

Public surface:
- `CodingTask` / `CodingOutcome` — the in/out contract for one coding attempt.
- `CodingAgent` — the provider-agnostic agent interface (mock for $0 tests; Claude
  Agent SDK for real coding).
- `Workspace` — prepares a repo checkout, runs the target's own test command, cleans up.
- `implement_in_workspace` / `run_qa` — the pure pod functions an activity calls.
"""

from orchestrator.agents.coding.agent import CodingAgent
from orchestrator.agents.coding.pod import implement_and_verify, run_qa
from orchestrator.agents.coding.types import (
    CodingOutcome,
    CodingTask,
    FileEdit,
    QAOutcome,
    TestRun,
)
from orchestrator.agents.coding.workspace import Workspace

__all__ = [
    "CodingAgent",
    "CodingOutcome",
    "CodingTask",
    "FileEdit",
    "QAOutcome",
    "TestRun",
    "Workspace",
    "implement_and_verify",
    "run_qa",
]
