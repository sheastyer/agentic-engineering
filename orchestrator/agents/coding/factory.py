"""Coding-agent selection, mirroring the reasoning plane's `build_provider` (CODING_AGENT
env var). Default is a **no-op mock** so a worker stays $0/safe until a real coding agent
is explicitly enabled — the same "off by default" posture as the M3 `USE_AGENT_*` flags.
Tests construct `MockCodingAgent(edits=...)` directly to exercise the loop.
"""

import os

from orchestrator.agents.coding.agent import CodingAgent
from orchestrator.agents.coding.sandbox import ContainerSandbox, LocalSandbox, Sandbox
from orchestrator.shared.config import CODING_AGENT_IMAGE_DEFAULT


def build_coding_agent() -> CodingAgent:
    choice = os.environ.get("CODING_AGENT", "mock").lower()
    if choice in ("claude_container", "container_claude"):
        from orchestrator.agents.coding.agents.claude_container import ContainerClaudeCodingAgent

        return ContainerClaudeCodingAgent(**_container_agent_config())
    if choice in ("claude_agent_sdk", "claude", "sdk"):
        from orchestrator.agents.coding.agents.claude_sdk import ClaudeSDKCodingAgent

        return ClaudeSDKCodingAgent()
    from orchestrator.agents.coding.agents.mock import MockCodingAgent

    return MockCodingAgent()


def _container_agent_config() -> dict:
    """Build the container coding agent's config from env (activity-side, never workflow-side).

    The agent runs `claude` inside a container, so it needs an image that has the `claude` CLI +
    the target's runtime, and a way for that CLI to authenticate. Credentials cross the boundary
    *only* via the explicit channels below — `CODING_AGENT_CRED_ENV` (comma-separated names of env
    vars present on the worker to forward, e.g. `ANTHROPIC_API_KEY`) and `CODING_AGENT_CRED_MOUNT`
    (a `host:container[:ro]` mount, e.g. a subscription token file) — nothing else from the host
    env or filesystem is visible inside the container.
    """
    image = os.environ.get("CODING_AGENT_IMAGE", CODING_AGENT_IMAGE_DEFAULT)
    cred_env = {
        name: os.environ[name]
        for name in _split_csv(os.environ.get("CODING_AGENT_CRED_ENV", ""))
        if name in os.environ
    }
    mount = os.environ.get("CODING_AGENT_CRED_MOUNT", "")
    cred_mounts = (_parse_mount(mount),) if mount else ()
    return {"image": image, "cred_env": cred_env, "cred_mounts": cred_mounts}


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_mount(spec: str) -> tuple[str, str, bool]:
    """`host:container[:ro]` → (host, container, read_only). Defaults to read-only (creds)."""
    parts = spec.split(":")
    host, container = parts[0], parts[1]
    read_only = len(parts) < 3 or parts[2].lower() == "ro"
    return host, container, read_only


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
