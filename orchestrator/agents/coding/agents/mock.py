"""A deterministic, $0 coding agent — the execution-plane equivalent of the mock provider.

It applies a fixed list of find/replace edits to the workspace and reports the resulting
diff. It is NOT a smart agent: it proves the loop (workspace prep → edits → QA runs the
target's tests → pass/fail) without spending tokens or needing live auth. Construct it
with the edits a test wants applied; construct it with `edits=[]` for a no-op agent (the
negative-QA case — a coding attempt that changes nothing and must be caught by QA).
"""

import os

from orchestrator.agents.coding.types import CodingOutcome, CodingTask, FileEdit
from orchestrator.agents.coding.workspace import Workspace


class MockCodingAgent:
    name = "mock"

    def __init__(self, edits: list[FileEdit] | None = None, cost_usd: float = 0.0) -> None:
        self._edits = edits or []
        self._cost_usd = cost_usd

    async def implement(self, task: CodingTask, workspace: Workspace) -> CodingOutcome:
        assert workspace.path is not None, "workspace not entered"
        changed: list[str] = []
        for edit in self._edits:
            target = os.path.join(workspace.path, edit.path)
            with open(target, encoding="utf-8") as fh:
                text = fh.read()
            if edit.find in text:
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write(text.replace(edit.find, edit.replace))
                changed.append(edit.path)
        return CodingOutcome(
            summary=f"(mock) applied {len(changed)} of {len(self._edits)} edit(s)",
            files_changed=changed,
            diff=workspace.diff(),
            cost_usd=self._cost_usd,
        )
