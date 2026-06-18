"""The sandbox seam — where a command actually executes (D9).

The whole point of M4's isolation invariant (CLAUDE.md §9.6) is that coding agents run in
a real sandbox: a git worktree alone is NOT one — it shares `.git`, the host filesystem,
env vars (incl. the API key), and the network. This module makes the execution boundary a
*pluggable interface* so the loop can be proven now on a `LocalSandbox` and hardened later
to a `ContainerSandbox` (the M4 exit gate's escape negative-test) without touching the
workspace/agent/pod code above it.
"""

import os
import subprocess
from dataclasses import dataclass, field
from typing import Protocol

# Default container image for the execution boundary. python:3.x-slim has an interpreter
# (enough to run a Python target's test command) and nothing else — no git, no shell tools
# beyond busybox `sh`. Overridable per project via CODING_SANDBOX_IMAGE; the target's own
# stack (from its Project Profile) decides what the real image needs.
_DEFAULT_IMAGE = os.environ.get("CODING_SANDBOX_IMAGE", "python:3.14-slim")


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


@dataclass
class ContainerSandbox:
    """Runs commands inside an ephemeral Docker container — the real D9 execution boundary.

    This is what makes it safe to run repo-authored code (a target's test/build command, and
    eventually the agent's own Bash tool) on untrusted input. Each `run` is a fresh
    `docker run --rm` that mounts **only** the workspace dir at `/work` and otherwise shares
    nothing with the host. The three escape vectors the M4 exit gate names are each closed by
    construction, and asserted by `tests/test_sandbox_isolation.py`:

      • **Filesystem** — only `cwd` is bind-mounted; the rest of the host tree (other repos,
        `~/.aws`, `~/.claude`) simply isn't present in the container.
      • **Secrets/env** — the container's environment is *empty* except the keys explicitly
        passed in `env`. Host env vars (an API key in the parent process) do not cross.
      • **Network** — `--network none` by default; a story opts in only if it must.

    Plus defence-in-depth: all Linux capabilities dropped, `no-new-privileges`, and pid/mem/cpu
    caps so a runaway or fork-bomb can't take the host down.

    Note (honest scope): with the SDK coding agent, the agent process and its Bash tool run on
    the *host* today; this sandbox currently contains the **test command** Workspace runs (a
    real injection vector — repo-authored test scripts). Containing the agent process itself
    (run `claude` inside the container, or the SDK's native SandboxSettings) is the remaining
    D9 hardening, tracked in PLAN.md.
    """

    image: str = _DEFAULT_IMAGE
    allow_network: bool = False
    env: dict[str, str] = field(default_factory=dict)  # the ONLY secrets that cross the boundary
    runtime: str = "docker"
    memory: str = "1g"
    cpus: str = "2"
    name: str = "container"

    def run(self, command: str, cwd: str, timeout: int = 600) -> CommandResult:
        args = [
            self.runtime, "run", "--rm",
            "-v", f"{os.path.abspath(cwd)}:/work",
            "-w", "/work",
            "--network", "bridge" if self.allow_network else "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", "512",
            "--memory", self.memory,
            "--cpus", self.cpus,
        ]
        for key, value in self.env.items():
            args += ["-e", f"{key}={value}"]
        args += [self.image, "sh", "-lc", command]
        # No `env=` on the host call: the docker *client* inherits the parent env (it needs
        # DOCKER_HOST etc.), but the *container* only ever sees what `-e` above injects.
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return CommandResult(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def docker_available(runtime: str = "docker") -> bool:
    """True if a container runtime daemon is reachable — gates the isolation tests."""
    try:
        return subprocess.run(
            [runtime, "info"], capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
