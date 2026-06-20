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


def container_run_args(
    *,
    runtime: str,
    image: str,
    cwd: str,
    command: str,
    allow_network: bool,
    env: dict[str, str],
    memory: str = "1g",
    cpus: str = "2",
    user: str | None = None,
    mounts: tuple[tuple[str, str, bool], ...] = (),
    pids_limit: int = 512,
) -> list[str]:
    """The single source of truth for the D9 execution boundary (used by `ContainerSandbox`
    *and* the container coding agent, so both get the same guarantees the escape-tests assert).

    Mounts **only** `cwd` at `/work` (+ any explicit `mounts`), gives the container an **empty
    environment** except `env`, blocks the network unless `allow_network`, drops all caps, and
    sets `no-new-privileges` + pid/mem/cpu caps. `user` (uid:gid) keeps container-written files
    owned by the host user so the diff/cleanup stay clean; `mounts` are `(host, container, ro)`.
    """
    args = [
        runtime, "run", "--rm",
        "-v", f"{os.path.abspath(cwd)}:/work",
        "-w", "/work",
        "--network", "bridge" if allow_network else "none",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(pids_limit),
        "--memory", memory,
        "--cpus", cpus,
    ]
    if user:
        args += ["--user", user]
    for host_path, container_path, read_only in mounts:
        args += ["-v", f"{os.path.abspath(host_path)}:{container_path}{':ro' if read_only else ''}"]
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args += [image, "sh", "-lc", command]
    return args


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

    This boundary contains the **test command** Workspace runs (a real injection vector —
    repo-authored test scripts). The *agent process itself* is contained by the same boundary
    when `CODING_AGENT=claude_container` (see `agents/claude_container.py`), which runs `claude`
    inside a container built from `container_run_args` rather than on the host (Option A of the
    D9 agent-process hardening). The remaining tightening is an egress allow-list for the agent's
    container (it needs the model API, so it runs with the network on today) — tracked in PLAN.md.
    """

    image: str = _DEFAULT_IMAGE
    allow_network: bool = False
    env: dict[str, str] = field(default_factory=dict)  # the ONLY secrets that cross the boundary
    runtime: str = "docker"
    memory: str = "1g"
    cpus: str = "2"
    name: str = "container"

    def run(self, command: str, cwd: str, timeout: int = 600) -> CommandResult:
        args = container_run_args(
            runtime=self.runtime, image=self.image, cwd=cwd, command=command,
            allow_network=self.allow_network, env=self.env, memory=self.memory, cpus=self.cpus,
        )
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
