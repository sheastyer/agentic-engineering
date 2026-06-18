"""Coding-agent selection, mirroring the reasoning plane's `build_provider` (CODING_AGENT
env var). Default is a **no-op mock** so a worker stays $0/safe until a real coding agent
is explicitly enabled — the same "off by default" posture as the M3 `USE_AGENT_*` flags.
Tests construct `MockCodingAgent(edits=...)` directly to exercise the loop.
"""

import os

from orchestrator.agents.coding.agent import CodingAgent
from orchestrator.agents.coding.sandbox import ContainerSandbox, LocalSandbox, Sandbox


def build_coding_agent() -> CodingAgent:
    choice = os.environ.get("CODING_AGENT", "mock").lower()
    if choice in ("claude_agent_sdk", "claude", "sdk"):
        from orchestrator.agents.coding.agents.claude_sdk import ClaudeSDKCodingAgent

        return ClaudeSDKCodingAgent()
    from orchestrator.agents.coding.agents.mock import MockCodingAgent

    return MockCodingAgent()


def build_sandbox() -> Sandbox:
    """Select the execution boundary for the *untrusted* test command (CODING_SANDBOX env).

    Default is `local` — fine for trusted fixtures and keeps a worker dependency-free — but a
    worker that runs a real coding agent on real feedback must set `CODING_SANDBOX=container`
    so repo-authored code never executes on the host (D9 / §9.6). Same "isolation is opt-in,
    but available by config not code" posture as `build_coding_agent`.
    """
    choice = os.environ.get("CODING_SANDBOX", "local").lower()
    if choice in ("container", "docker"):
        return ContainerSandbox()
    return LocalSandbox()
