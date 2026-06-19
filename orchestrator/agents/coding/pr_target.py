"""Where a PR actually gets opened — the pod's terminal side-effect, made pluggable.

Same "isolation/real is opt-in by config, off by default" posture as `factory.build_sandbox`:
the default `LocalPRTarget` clones, branches, applies the story diffs and commits in a
disposable temp dir but **never pushes** (a $0, side-effect-free dry run that still proves
the diff applies cleanly). `GitHubPRTarget` is the only path that touches the outside world —
it pushes the branch and runs `gh pr create` — and is selected only by `CODING_PR_TARGET=github`.

This is execution-plane code (I/O lives here), only ever imported by an activity, never by a
workflow (R3). Deploy/merge is a *separate*, human-gated step (§9.2); this only opens the PR.
"""

import os
import subprocess
import tempfile
from typing import Protocol

from orchestrator.shared.types import PRResult

_GIT_ID = '-c user.email=org@agentic.local -c user.name="agentic org"'


class PRTarget(Protocol):
    def open(
        self,
        *,
        repo_source: str,
        base_branch: str,
        branch: str,
        diffs: list[str],
        title: str,
        body: str,
    ) -> PRResult: ...


def _run(command: str, cwd: str | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run a shell command on the host (trusted org git plumbing), capturing output."""
    return subprocess.run(
        command, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout
    )


def _prepare_branch(repo_source: str, branch: str, diffs: list[str], body: str) -> tuple[str, str]:
    """Clone repo_source into a fresh temp dir, cut `branch`, apply the story diffs, commit.

    Returns (checkout_path, commit_message). Raises RuntimeError if the clone fails or no diff
    applies (so the caller can report `opened=False` instead of pushing an empty branch).
    """
    root = tempfile.mkdtemp(prefix="agentic-pr-")
    checkout = os.path.join(root, "repo")
    clone = _run(f"git clone --depth 1 {_q(repo_source)} {_q(checkout)}", cwd=root)
    if clone.returncode != 0:
        raise RuntimeError(f"clone failed: {clone.stderr.strip() or clone.stdout.strip()}")

    _run(f"git checkout -b {_q(branch)}", cwd=checkout)
    applied = 0
    for diff in diffs:
        if not diff.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
            fh.write(diff if diff.endswith("\n") else diff + "\n")
            patch_path = fh.name
        res = _run(f"git apply --3way {_q(patch_path)}", cwd=checkout)
        os.unlink(patch_path)
        if res.returncode == 0:
            applied += 1
    if applied == 0:
        raise RuntimeError("no story diff applied cleanly")

    _run(f"git add -A && git {_GIT_ID} commit -q -m {_q(_commit_message(body))}", cwd=checkout)
    return checkout, _commit_message(body)


def _commit_message(body: str) -> str:
    first = (body or "agentic change").strip().splitlines()[0] if body.strip() else "agentic change"
    return f"[agentic] {first}"[:72]


class LocalPRTarget:
    """Default: prove the PR end-to-end locally — clone, branch, apply, commit — but DO NOT
    push or call any external API. The returned url is a `file://…#branch` stand-in so the
    flow has a concrete artifact to show without any outward side effect."""

    name = "local"

    def open(
        self, *, repo_source, base_branch, branch, diffs, title, body
    ) -> PRResult:
        try:
            checkout, _ = _prepare_branch(repo_source, branch, diffs, body)
        except RuntimeError as exc:
            return PRResult(opened=False, branch=branch, note=str(exc))
        return PRResult(
            opened=True,
            url=f"file://{checkout}#{branch}",
            branch=branch,
            note="local dry-run PR (cloned, applied, committed; not pushed)",
        )


class GitHubPRTarget:
    """The real outward-facing path: push the branch to origin and `gh pr create`. Selected
    only by CODING_PR_TARGET=github, so opening a real PR is always a deliberate config choice
    (and `gh` must be authenticated on the host)."""

    name = "github"

    def open(
        self, *, repo_source, base_branch, branch, diffs, title, body
    ) -> PRResult:
        try:
            checkout, _ = _prepare_branch(repo_source, branch, diffs, body)
        except RuntimeError as exc:
            return PRResult(opened=False, branch=branch, note=str(exc))

        push = _run(f"git push -u origin {_q(branch)}", cwd=checkout)
        if push.returncode != 0:
            return PRResult(
                opened=False, branch=branch,
                note=f"push failed: {push.stderr.strip() or push.stdout.strip()}",
            )
        pr = _run(
            f"gh pr create --base {_q(base_branch)} --head {_q(branch)} "
            f"--title {_q(title)} --body {_q(body)}",
            cwd=checkout,
        )
        if pr.returncode != 0:
            return PRResult(
                opened=False, branch=branch,
                note=f"gh pr create failed: {pr.stderr.strip() or pr.stdout.strip()}",
            )
        url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else ""
        return PRResult(opened=True, url=url, branch=branch, note="opened via gh")


def build_pr_target() -> PRTarget:
    """Select the PR target (CODING_PR_TARGET env). Default `local` = no external side effect;
    `github` = real PR. Off-by-default mirrors build_sandbox / build_coding_agent."""
    choice = os.environ.get("CODING_PR_TARGET", "local").lower()
    if choice in ("github", "gh"):
        return GitHubPRTarget()
    return LocalPRTarget()


def _q(value: str) -> str:
    import shlex

    return shlex.quote(value)
