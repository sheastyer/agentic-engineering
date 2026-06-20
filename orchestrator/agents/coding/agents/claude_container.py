"""Container-isolated coding agent — Option A of the D9 agent-process hardening.

The SDK agent (`claude_sdk.py`) spawns `claude` on the **host**: `cwd` scopes where it starts,
but its Bash tool can read the host filesystem, the worker's env/secrets, and the network. This
agent instead runs the `claude` CLI **inside an ephemeral container** built from the same
`container_run_args` boundary as `ContainerSandbox` — the workspace is bind-mounted at `/work`,
the container env is empty except the one credential the agent needs, all caps are dropped, and
files are written as the host user. The agent's Bash/Edit tools then execute against the
container, not the host. So both injection vectors — the target's test command *and* the agent
process — now share one audited boundary (the escape negative-tests cover the flags).

Honest residual: the agent must reach the model API, so its container runs with the network
**on** (`bridge`). Tightening that to an egress allow-list for just the API host is the remaining
hardening (tracked in PLAN.md M4) — but FS and secret isolation already hold.

Headless contract: the prompt is fed on **STDIN** (so untrusted task text never reaches argv),
and `claude -p --output-format json` returns one JSON result carrying `total_cost_usd` / `usage`
/ `result`. Like the SDK agent, a non-zero exit (budget/turn cap, transient error) is a **soft
stop**: whatever the agent edited is captured as the diff, never discarded.

Live-validation note: the exact CLI flags and the subscription-credential-in-container path are
confirmed on a real run (see PLAN.md M4); the mechanism + boundary are unit-tested at $0 here via
an injected command runner (no docker, no auth, no tokens).
"""

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Callable

from orchestrator.agents.coding.agents.claude_sdk import _changed_paths, _prompt
from orchestrator.agents.coding.sandbox import container_run_args
from orchestrator.agents.coding.types import CodingOutcome, CodingTask
from orchestrator.agents.coding.workspace import Workspace
from orchestrator.shared.config import PRICING

# Same toolset the SDK agent allows — read/edit code and run the test command, no network tools.
_ALLOWED_TOOLS = ("Read", "Edit", "Write", "Bash", "Glob", "Grep")
# Staged inside the workspace (bind-mounted into the container), excluded from the diff by
# Workspace._TRANSIENT_EXCLUDES so neither the prompt nor the agent's HOME pollutes the PR.
_AGENT_DIR = ".agentic"
_PROMPT_REL = f"{_AGENT_DIR}/prompt.txt"
_HOME_REL = f"{_AGENT_DIR}/home"


@dataclass
class _RunResult:
    returncode: int
    stdout: str
    stderr: str


def _default_runner(args: list[str], timeout: int) -> _RunResult:
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return _RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")


def _current_user() -> str | None:
    """uid:gid of the worker process, so container-written files stay host-owned (clean diff +
    cleanup). `None` on platforms without POSIX uids (the run just omits `--user`)."""
    getuid, getgid = getattr(os, "getuid", None), getattr(os, "getgid", None)
    return f"{getuid()}:{getgid()}" if getuid and getgid else None


class ContainerClaudeCodingAgent:
    name = "claude_container"

    def __init__(
        self,
        *,
        image: str,
        cred_env: dict[str, str] | None = None,
        cred_mounts: tuple[tuple[str, str, bool], ...] = (),
        allow_network: bool = True,
        runtime: str = "docker",
        memory: str = "2g",
        cpus: str = "2",
        user: str | None = None,
        permission_mode: str | None = None,
        timeout: int = 1200,
        run_command: Callable[[list[str], int], _RunResult] | None = None,
    ) -> None:
        self.image = image
        self.cred_env = dict(cred_env or {})          # the ONLY secrets crossing into the container
        self.cred_mounts = tuple(cred_mounts)
        self.allow_network = allow_network
        self.runtime = runtime
        self.memory = memory
        self.cpus = cpus
        self.user = user if user is not None else _current_user()
        # bypassPermissions is the right non-interactive mode *because* a real boundary contains
        # the agent here (the container), not the prompt.
        self.permission_mode = permission_mode or os.environ.get("CODING_PERMISSION_MODE", "bypassPermissions")
        self.timeout = timeout
        self._run = run_command or _default_runner

    async def implement(self, task: CodingTask, workspace: Workspace) -> CodingOutcome:
        assert workspace.path is not None, "workspace not entered"
        # Stage the prompt + a writable HOME inside the workspace (bind-mounted at /work). Writing
        # the prompt to a file and feeding it on stdin keeps untrusted task text out of argv.
        os.makedirs(os.path.join(workspace.path, _HOME_REL), exist_ok=True)
        with open(os.path.join(workspace.path, _PROMPT_REL), "w", encoding="utf-8") as fh:
            fh.write(_prompt(task))

        env = {"HOME": f"/work/{_HOME_REL}", **self.cred_env}
        args = container_run_args(
            runtime=self.runtime, image=self.image, cwd=workspace.path,
            command=self._claude_command(task), allow_network=self.allow_network,
            env=env, memory=self.memory, cpus=self.cpus, user=self.user, mounts=self.cred_mounts,
        )

        stopped = ""
        summary, cost_usd, in_tok, out_tok = "", 0.0, 0, 0
        try:
            summary, cost_usd, in_tok, out_tok, stopped = _parse_result(self._run(args, self.timeout))
        except Exception as exc:  # noqa: BLE001 — soft stop: keep whatever the agent already edited
            stopped = f" [agent stopped early: {str(exc)[:120]}]"

        diff = workspace.diff()
        return CodingOutcome(
            summary=(summary + stopped) or "(agent produced no summary)",
            files_changed=_changed_paths(diff),
            diff=diff,
            cost_usd=cost_usd,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )

    def _claude_command(self, task: CodingTask) -> str:
        """The in-container `claude` invocation. Trusted values only (model id, turn/permission
        caps); the untrusted prompt arrives on stdin via `< /work/.agentic/prompt.txt`."""
        model = PRICING[task.tier]["model"]
        return (
            "claude -p --output-format json "
            f"--model {shlex.quote(model)} "
            f"--max-turns {int(task.max_turns)} "
            f"--permission-mode {shlex.quote(self.permission_mode)} "
            f"--allowedTools {shlex.quote(' '.join(_ALLOWED_TOOLS))} "
            f"< {shlex.quote('/work/' + _PROMPT_REL)}"
        )


def _parse_result(res: _RunResult) -> tuple[str, float, int, int, str]:
    """Parse `claude -p --output-format json` output → (summary, cost_usd, in_tok, out_tok, stopped).

    The result is a single JSON object on stdout; we read the last non-empty line defensively in
    case a banner precedes it. A missing/!json payload or an `is_error`/non-zero exit is a soft
    stop — the caller still captures the diff the agent produced."""
    lines = [ln for ln in (res.stdout or "").splitlines() if ln.strip()]
    try:
        data = json.loads(lines[-1]) if lines else {}
    except ValueError:
        data = {}
    if not data:
        note = (res.stderr or res.stdout or "").strip()[:120]
        return "(no JSON result from agent)", 0.0, 0, 0, (f" [agent stopped early: {note}]" if note else "")

    usage = data.get("usage") or {}
    summary = str(data.get("result", "")).strip()[:500]
    cost_usd = float(data.get("total_cost_usd") or 0.0)
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    stopped = ""
    if data.get("is_error") or res.returncode != 0:
        stopped = " [agent reported error / non-zero exit; partial diff captured]"
    return summary, cost_usd, in_tok, out_tok, stopped
