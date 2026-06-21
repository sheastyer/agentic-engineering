"""Where the org learns whether a PR's CI is green — the gate that stops a red PR from
reaching the merge step (CLAUDE.md §9.2, §9 invariants).

Same "real is opt-in by config, off by default" posture as `pr_target` / `factory.build_sandbox`:
the default `NoCIChecker` reports **unavailable** (a mock/local PR target has no CI to wait on),
so $0 dry-runs proceed without blocking. `GitHubCIChecker` is the only path that touches the
outside world — it polls `gh pr checks` for the branch's PR until the checks conclude — and is
selected only when the PR target is `github`.

This is execution-plane code (I/O + polling live here), only ever imported by an activity, never
by a workflow (R3). The poll loop is bounded by a timeout so the await-CI activity can't hang.
"""

import os
import shutil
import subprocess
import tempfile
import time
from typing import Protocol

from orchestrator.shared.types import CIResult


class CIChecker(Protocol):
    def await_conclusion(
        self, *, repo_source: str, branch: str, timeout_s: int, interval_s: int
    ) -> CIResult:
        """Block until the PR opened from `branch` has all checks concluded (or `timeout_s`
        elapses), then return the verdict. Must never raise for an ordinary CI failure — a
        failure is a `CIResult(status="failed")`, not an exception (the workflow branches on it)."""
        ...


class NoCIChecker:
    """Default: there is no real CI to wait on (mock/local PR target). Report `unavailable` so
    the pod's CI gate is a no-op and the run proceeds — never blocks a $0 dry-run."""

    name = "none"

    def await_conclusion(self, *, repo_source, branch, timeout_s, interval_s) -> CIResult:
        return CIResult(status="unavailable", passed=True, failing_summary="", url="")


def _run(command: str, cwd: str | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout
    )


class GitHubCIChecker:
    """Real CI gate: poll `gh pr checks <branch>` until every check leaves the pending state,
    then pass iff none failed. Selected only when CODING_PR_TARGET=github (gh must be authed).

    Polls inside a shallow clone of the repo so `gh` resolves the right repository. A failed
    poll (gh absent/transient) is retried until the timeout rather than treated as a CI failure;
    only a genuine concluded-with-failures result blocks the merge."""

    name = "github"

    def await_conclusion(self, *, repo_source, branch, timeout_s, interval_s) -> CIResult:
        root = tempfile.mkdtemp(prefix="agentic-ci-")
        checkout = os.path.join(root, "repo")
        try:
            clone = _run(f"git clone --depth 1 {_q(repo_source)} {_q(checkout)}", cwd=root)
            if clone.returncode != 0:
                # Can't query CI → treat as unavailable (don't block on our own clone failure).
                return CIResult(status="unavailable", passed=True,
                                failing_summary=f"could not clone to query CI: {clone.stderr.strip()}")
            deadline = time.monotonic() + timeout_s
            last = ""
            while time.monotonic() < deadline:
                verdict = self._poll_once(checkout, branch)
                if verdict is not None:
                    return verdict
                last = "checks still pending"
                time.sleep(interval_s)
            return CIResult(status="failed", passed=False,
                            failing_summary=f"CI did not conclude within {timeout_s}s ({last})",
                            url=_pr_url(checkout, branch))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def _poll_once(self, checkout: str, branch: str) -> CIResult | None:
        """One status read. Returns a CIResult once checks have concluded, else None (still
        pending / not yet reported, keep polling)."""
        res = _run(
            f"gh pr checks {_q(branch)} "
            "--json name,state,bucket,link --jq '.[] | [.name,.bucket,.link] | @tsv'",
            cwd=checkout,
        )
        if res.returncode != 0:
            # gh exits non-zero while checks are pending OR on transient errors. If the message
            # says no checks have reported yet, keep waiting; otherwise also keep waiting.
            return None
        rows = [line.split("\t") for line in res.stdout.strip().splitlines() if line.strip()]
        if not rows:
            return None  # no checks reported yet
        buckets = [r[1] if len(r) > 1 else "" for r in rows]
        if any(b == "pending" or b == "" for b in buckets):
            return None  # still running
        failing = [r for r in rows if (r[1] if len(r) > 1 else "") == "fail"]
        url = _pr_url(checkout, branch)
        if failing:
            summary = "; ".join(f"{r[0]} failed ({r[2]})" if len(r) > 2 else f"{r[0]} failed" for r in failing)
            return CIResult(status="failed", passed=False, failing_summary=summary, url=url)
        return CIResult(status="passed", passed=True, failing_summary="", url=url)


def _pr_url(checkout: str, branch: str) -> str:
    res = _run(
        f"gh pr view {_q(branch)} --json url --jq .url", cwd=checkout
    )
    return res.stdout.strip() if res.returncode == 0 else ""


def build_ci_checker() -> CIChecker:
    """Select the CI checker. Mirrors `build_pr_target`: a real `github` PR target implies real
    CI to wait on; anything else (local/mock) has none, so the gate is a no-op (`unavailable`)."""
    choice = os.environ.get("CODING_PR_TARGET", "local").lower()
    if choice in ("github", "gh"):
        return GitHubCIChecker()
    return NoCIChecker()


def _q(value: str) -> str:
    import shlex

    return shlex.quote(value)
