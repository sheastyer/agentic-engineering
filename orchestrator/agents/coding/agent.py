"""The coding-agent interface — the execution plane's equivalent of `ModelProvider`.

One async method turns a `CodingTask` into a `CodingOutcome` by editing files inside the
prepared `Workspace`. Provider-agnostic: a `MockCodingAgent` drives $0 tests, the
`ClaudeSDKCodingAgent` runs real coding via the Claude Agent SDK. Async because the SDK's
`query` is async; the activity awaits it.
"""

from typing import Protocol

from orchestrator.agents.coding.types import CodingOutcome, CodingTask
from orchestrator.agents.coding.workspace import Workspace


class CodingAgent(Protocol):
    name: str

    async def implement(self, task: CodingTask, workspace: Workspace) -> CodingOutcome: ...
