"""M4 D9 — the sandbox-escape negative tests (CLAUDE.md §9.6, PLAN.md M4 exit gate).

The exit gate requires more than "target main is untouched" — it requires that an agent
which *attempts* to escape is **prevented**. These tests drive `ContainerSandbox` with
hostile commands and assert each of the three named vectors fails:

  • leave the workspace (read a host file outside the mount),
  • read a host secret / env var,
  • reach a disallowed network host.

A positive control proves the boundary isn't just "everything fails," and a `LocalSandbox`
contrast proves *why* the container is needed (the stand-in leaks all three — by design).
Finally, an end-to-end test runs the seeded fix and verifies it with the target's test
command **inside the container**, proving the real loop works through the boundary.

Docker-gated: skipped (not failed) where no container runtime is reachable, so CI without
Docker stays green; the gate is exercised wherever a daemon exists. Uses the cached
`python:3.14-slim` image, so no network pull is needed.
"""

import os
import tempfile

import pytest

from orchestrator.agents.coding.sandbox import ContainerSandbox, LocalSandbox, docker_available

requires_docker = pytest.mark.skipif(
    not docker_available(), reason="no container runtime (docker daemon) available"
)

# A python program (run from inside the workspace mount) that tries to open a TCP connection
# to a public IP — no DNS, so it's a pure network-reachability probe. Written into the mount
# as a file so we sidestep all shell-quoting of a python one-liner.
_NETCHECK = (
    "import socket\n"
    "s = socket.socket(); s.settimeout(4)\n"
    "try:\n"
    "    s.connect(('1.1.1.1', 53)); print('CONNECTED')\n"
    "except Exception as e:\n"
    "    print('BLOCKED:', type(e).__name__)\n"
)


def _workspace() -> tuple[str, str, str]:
    """A temp root with a mounted `workspace/` and a sibling host file OUTSIDE the mount."""
    root = tempfile.mkdtemp(prefix="sbx-iso-")
    ws = os.path.join(root, "workspace")
    os.makedirs(ws)
    with open(os.path.join(ws, "inside.txt"), "w") as fh:
        fh.write("in-workspace")
    host_secret = os.path.join(root, "host_only_secret.txt")
    with open(host_secret, "w") as fh:
        fh.write("TOP-SECRET-HOST-FILE")
    return root, ws, host_secret


@requires_docker
def test_workspace_is_reachable_positive_control():
    # The boundary must let legitimate work through, or "everything is blocked" would pass
    # the negative tests vacuously.
    _root, ws, _secret = _workspace()
    res = ContainerSandbox().run("cat inside.txt", cwd=ws)
    assert res.returncode == 0 and "in-workspace" in res.output


@requires_docker
def test_host_filesystem_outside_workspace_is_not_mounted():
    _root, ws, host_secret = _workspace()
    # The host's absolute path to the sibling secret does not exist inside the container.
    res = ContainerSandbox().run(f"cat {host_secret}", cwd=ws)
    assert res.returncode != 0, "reading a host file outside the mount must fail"
    assert "TOP-SECRET-HOST-FILE" not in res.output
    # And the mount really is scoped to just the workspace.
    listing = ContainerSandbox().run("ls -A /work", cwd=ws)
    assert "inside.txt" in listing.output and "host_only_secret.txt" not in listing.output


@requires_docker
def test_host_env_secrets_do_not_cross_the_boundary(monkeypatch):
    monkeypatch.setenv("AGENTIC_FAKE_SECRET", "leakme-please")
    _root, ws, _secret = _workspace()
    # env={} → nothing crosses. (sentinel echoed so an *empty* var and a *missing* var look
    # the same: both must yield no leak.)
    res = ContainerSandbox(env={}).run(
        'printf "value=[%s]" "$AGENTIC_FAKE_SECRET"', cwd=ws
    )
    assert "leakme-please" not in res.output
    assert "value=[]" in res.output


@requires_docker
def test_only_explicitly_passed_secrets_are_present():
    # The allow-list path: a secret the story *does* need is injected, and nothing else.
    _root, ws, _secret = _workspace()
    res = ContainerSandbox(env={"STORY_TOKEN": "scoped-ok"}).run(
        'printf "[%s]" "$STORY_TOKEN"', cwd=ws
    )
    assert "[scoped-ok]" in res.output


@requires_docker
def test_network_is_disabled_by_default():
    _root, ws, _secret = _workspace()
    with open(os.path.join(ws, "netcheck.py"), "w") as fh:
        fh.write(_NETCHECK)
    res = ContainerSandbox().run("python3 netcheck.py", cwd=ws)
    assert "CONNECTED" not in res.output, "default sandbox must have no network egress"
    assert "BLOCKED" in res.output


def test_local_sandbox_is_not_a_boundary_contrast():
    # Documents *why* ContainerSandbox exists: the stand-in leaks the host file the container
    # blocks. No docker needed — this is the threat the boundary closes.
    _root, ws, host_secret = _workspace()
    res = LocalSandbox().run(f"cat {host_secret}", cwd=ws)
    assert res.returncode == 0 and "TOP-SECRET-HOST-FILE" in res.output


@requires_docker
async def test_seeded_fix_verified_inside_the_container():
    # The real loop through the boundary: edit applied in the workspace, then the target's own
    # test command runs INSIDE the container and gates the result — and the host source repo
    # is never touched.
    from orchestrator.agents.coding import implement_and_verify
    from orchestrator.agents.coding.agents.mock import MockCodingAgent
    from orchestrator.agents.coding.types import CodingTask, FileEdit

    fixture = os.path.join(os.path.dirname(__file__), "fixtures", "seeded_repo")
    # python-only test command (the slim image has no pytest): exit 0 iff the bug is fixed.
    task = CodingTask(
        instruction="Fix mathlib.add so it returns the sum.",
        test_command="python3 -c \"import mathlib,sys; sys.exit(0 if mathlib.add(2,3)==5 else 1)\"",
    )
    agent = MockCodingAgent(edits=[FileEdit("mathlib.py", "return a - b", "return a + b")])

    outcome, qa = await implement_and_verify(agent, task, fixture, sandbox=ContainerSandbox())

    assert qa.passed, f"fix must verify via the container test command; notes={qa.notes!r}"
    assert "mathlib.py" in outcome.files_changed
    # Host source repo is pristine — the pod only ever touched its disposable workspace.
    with open(os.path.join(fixture, "mathlib.py")) as fh:
        assert "return a - b" in fh.read(), "the target source must be untouched"
