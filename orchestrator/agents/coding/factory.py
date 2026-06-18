"""Coding-agent selection, mirroring the reasoning plane's `build_provider` (CODING_AGENT
env var). Default is a **no-op mock** so a worker stays $0/safe until a real coding agent
is explicitly enabled — the same "off by default" posture as the M3 `USE_AGENT_*` flags.
Tests construct `MockCodingAgent(edits=...)` directly to exercise the loop.
"""

import os

from orchestrator.agents.coding.agent import CodingAgent


def build_coding_agent() -> CodingAgent:
    choice = os.environ.get("CODING_AGENT", "mock").lower()
    if choice in ("claude_agent_sdk", "claude", "sdk"):
        from orchestrator.agents.coding.agents.claude_sdk import ClaudeSDKCodingAgent

        return ClaudeSDKCodingAgent()
    from orchestrator.agents.coding.agents.mock import MockCodingAgent

    return MockCodingAgent()
