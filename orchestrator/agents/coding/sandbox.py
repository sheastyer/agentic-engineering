"""The sandbox seam — where a command actually executes (D9).

The whole point of M4's isolation invariant (CLAUDE.md §9.6) is that coding agents run in
a real sandbox: a git worktree alone is NOT one — it shares `.git`, the host filesystem,
env vars (incl. the API key), and the network. This module makes the execution boundary a
*pluggable interface* so the loop can be proven now on a `LocalSandbox` and hardened later
to a `ContainerSandbox` (the M4 exit gate's escape negative-test) without touching the
workspace/agent/pod code above it.
"""

import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass
class CommandResult:
    returncode: int
    output: str          # merged stdout+stderr


class Sandbox(Protocol):
    """Runs a shell command in a directory and returns its result."""

    name: str

    def run(self, command: str, cwd: str, timeout: int = 600) -> CommandResult: ...


class LocalSandbox:
    """Runs commands as local subprocesses in the workspace dir.

    ⚠️  NOT a security boundary. It shares the host filesystem, environment (including any
    API key), and network. It exists to prove the implement→test→QA loop on a trusted
    fixture. The M4 **exit gate** requires a `ContainerSandbox` (no host FS mount beyond
    the workspace, scoped network, only the secrets the story needs) plus the
    sandbox-escape negative-test before a real coding agent runs untrusted input here.
    """

    name = "local"

    def run(self, command: str, cwd: str, timeout: int = 600) -> CommandResult:
        proc = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))
