"""Data contracts for the execution plane (one coding attempt + its QA).

Plain dataclasses, mirroring `orchestrator/shared/types.py`: the activity adapts these
into the workflow-facing `StoryResult` / `QAResult` so the orchestration plane never sees
execution-plane detail (lightweight returns, §10).
"""

from dataclasses import dataclass, field


@dataclass
class CodingTask:
    """One unit of coding work handed to a CodingAgent inside a prepared workspace."""

    instruction: str                          # what to implement/fix (untrusted text — quoted to the agent)
    test_command: str                         # the target repo's own test command (from its profile)
    conventions: list[str] = field(default_factory=list)
    tier: str = "sonnet"                      # model tier (most coding is Sonnet, §5)
    max_turns: int = 30                       # bounded agent loop (§10)
    max_budget_usd: float = 1.00              # per-attempt spend cap handed to the SDK


@dataclass
class FileEdit:
    """A deterministic find/replace edit — used by the mock agent to simulate a fix."""

    path: str
    find: str
    replace: str


@dataclass
class CodingOutcome:
    """What one coding attempt produced. `diff` is the unified diff of the workspace."""

    summary: str
    files_changed: list[str]
    diff: str
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class TestRun:
    """Raw result of running the target's test command in the workspace."""

    passed: bool
    returncode: int
    output: str


@dataclass
class QAOutcome:
    """QA verdict for a coding attempt — the target's own tests are the gate."""

    passed: bool
    notes: str
    cost_usd: float = 0.0
