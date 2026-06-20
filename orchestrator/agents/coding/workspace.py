"""The managed per-run workspace (D4) — a disposable checkout of a target repo.

Prepared on `__enter__`, torn down on `__exit__` (the §9.6 cleanup requirement), so a
coding attempt never operates against the target's real tree. Source is either a git
remote (cloned) or a local path (copied — used by the fixture and by a profile `local_path`).
A baseline git commit is made so agent edits can be read back as a unified diff (the
lightweight return the pod hands up to the orchestration plane).

I/O lives here on purpose: this is execution-plane code, only ever imported by activities,
never by a workflow (R3).
"""

import os
import shutil
import tempfile

from orchestrator.agents.coding.sandbox import LocalSandbox, Sandbox
from orchestrator.agents.coding.types import TestRun

_GIT_ID = '-c user.email=org@agentic.local -c user.name="agentic org"'

# Transient build/test artifacts a coding+test run generates that must never land in the
# diff we hand up as a PR. Added to the workspace's local git exclude (.git/info/exclude),
# which is additive and local-only — it never overrides a cloned repo's own .gitignore.
_TRANSIENT_EXCLUDES = (
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    "node_modules/",
    ".next/",
    ".agentic/",        # the container coding agent stages its prompt + a writable HOME here
)


class Workspace:
    def __init__(
        self,
        source: str,
        *,
        test_command: str,
        from_git: bool = False,
        sandbox: Sandbox | None = None,
    ) -> None:
        self.source = os.path.expanduser(source)
        self.test_command = test_command
        self.from_git = from_git
        # Two seams, by trust level:
        #  • `_host` runs the org's own git plumbing (clone, baseline, diff) on org-controlled
        #    paths — trusted, so it stays on the host (and the sandbox image needs no git).
        #  • `sandbox` runs the *target's* test command — repo-authored, untrusted, so it is
        #    the pluggable boundary (LocalSandbox to prove the loop; ContainerSandbox for D9).
        self._host: Sandbox = LocalSandbox()
        self.sandbox: Sandbox = sandbox or LocalSandbox()
        self.path: str | None = None        # the repo checkout, set on __enter__
        self._root: str | None = None        # the temp dir we own and remove

    def __enter__(self) -> "Workspace":
        self._root = tempfile.mkdtemp(prefix="agentic-ws-")
        dest = os.path.join(self._root, "repo")
        if self.from_git:
            self._host.run(f"git clone --depth 1 {_q(self.source)} {_q(dest)}", cwd=self._root)
        else:
            shutil.copytree(self.source, dest)
        self.path = dest
        self._ensure_baseline()
        self._exclude_transient_artifacts()
        return self

    def __exit__(self, *exc) -> bool:
        if self._root and os.path.isdir(self._root):
            shutil.rmtree(self._root, ignore_errors=True)
        self.path = None
        self._root = None
        return False  # never swallow exceptions

    def _ensure_baseline(self) -> None:
        """Guarantee a git HEAD to diff against — init+commit if the source isn't a repo."""
        assert self.path is not None
        if not os.path.isdir(os.path.join(self.path, ".git")):
            self._host.run(
                f"git init -q && git add -A && git {_GIT_ID} commit -q -m baseline --allow-empty",
                cwd=self.path,
            )

    def _exclude_transient_artifacts(self) -> None:
        """Keep build/test artifacts (`__pycache__`, `*.pyc`, …) out of the diff/PR."""
        assert self.path is not None
        exclude_file = os.path.join(self.path, ".git", "info", "exclude")
        os.makedirs(os.path.dirname(exclude_file), exist_ok=True)
        with open(exclude_file, "a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(_TRANSIENT_EXCLUDES) + "\n")

    def run_tests(self) -> TestRun:
        """Run the target's own test command in the workspace; pass == exit 0."""
        assert self.path is not None, "workspace not entered"
        res = self.sandbox.run(self.test_command, cwd=self.path)
        return TestRun(passed=res.returncode == 0, returncode=res.returncode, output=res.output)

    def diff(self) -> str:
        """Unified diff of all changes since the baseline (staged so new files show)."""
        assert self.path is not None, "workspace not entered"
        return self._host.run("git add -A && git diff --cached HEAD", cwd=self.path).output


def _q(path: str) -> str:
    import shlex

    return shlex.quote(path)
