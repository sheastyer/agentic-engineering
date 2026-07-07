"""Post-QA visual evidence — boot the target app with the pod's diff applied and
screenshot the profile-declared routes.

Execution-plane code (I/O everywhere), only ever imported by an activity, never by a
workflow (R3). The whole module is **advisory by construction**: every failure path —
no preview configured, the diff won't apply, the app never becomes ready, Playwright
missing — returns ``ScreenshotSet(captured=False)`` with an honest note and NEVER
raises, because this runs after the expensive coding pass and a cosmetic failure must
not kill a workflow carrying a paid-for diff (the same rule as the QA agent and the
Slack notifier, §10).

Isolation (§9.6/D9): the org runs ``preview.up`` on the *host*, so the profile's
command must itself keep repo-authored code inside a container boundary (the reference
profile uses the target's own `docker compose` stack with an isolated project name).
The capture browser (Playwright, trusted org code) runs on the host and only talks
HTTP to the containerized app.

Cost: $0 in tokens — local compute only.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request

from orchestrator.projects.profile import Preview, ProjectProfile
from orchestrator.shared.types import ScreenshotSet

_log = logging.getLogger(__name__)

_VIEWPORT = {"width": 1280, "height": 800}
_PAGE_SETTLE_MS = 1500  # after "load": let client-side rendering paint before the shot


def _run(command: str, cwd: str, timeout: int = 900) -> subprocess.CompletedProcess:
    """Host shell for the profile's up/down commands and git plumbing (same posture as
    pr_target._run — trusted org plumbing; repo code is contained by the command itself)."""
    return subprocess.run(
        command, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout
    )


def _clone_and_apply(repo_source: str, diffs: list[str], runner=_run) -> str:
    """Clone the target into a fresh temp dir and apply the pod's diffs (no commit —
    the preview builds the working tree). Returns the checkout path; raises
    RuntimeError when the clone fails or no diff applies (mirrors pr_target)."""
    root = tempfile.mkdtemp(prefix="agentic-preview-")
    checkout = os.path.join(root, "repo")
    clone = runner(f"git clone --depth 1 {_q(repo_source)} {_q(checkout)}", cwd=root)
    if clone.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError(f"clone failed: {clone.stderr.strip() or clone.stdout.strip()}")
    applied = 0
    for diff in diffs:
        if not diff.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
            fh.write(diff if diff.endswith("\n") else diff + "\n")
            patch_path = fh.name
        res = runner(f"git apply --3way {_q(patch_path)}", cwd=checkout)
        os.unlink(patch_path)
        if res.returncode == 0:
            applied += 1
    if applied == 0:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError("no story diff applied cleanly")
    return checkout


def _http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        return False


def _await_ready(url: str, timeout_s: int, probe=_http_ok, interval_s: float = 3.0) -> bool:
    """Poll the preview's ready URL until it answers (or the budget runs out)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if probe(url):
            return True
        time.sleep(interval_s)
    return False


def _route_filename(route: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", route.lower()).strip("-") or "home"
    return f"{slug}.png"


def _shoot_routes(preview: Preview, out_dir: str) -> list[str]:
    """Screenshot each declared route with headless Chromium (Playwright, sync API —
    the caller is a sync activity in the worker's thread pool; the sync API refuses to
    run on a live event loop, which is exactly why the activity is a sync ``def``).
    When a login spec exists, POST it first through the browser context's request
    client so the session cookie lands in the shared cookie jar."""
    from playwright.sync_api import sync_playwright

    refs: list[str] = []
    base = preview.url.rstrip("/")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport=_VIEWPORT)
            if preview.login is not None:
                resp = context.request.post(
                    base + preview.login.api_path, data=preview.login.json_body
                )
                if not resp.ok:
                    _log.warning(
                        "preview login POST %s returned %s; capturing unauthenticated",
                        preview.login.api_path, resp.status,
                    )
            page = context.new_page()
            for route in preview.routes:
                path = os.path.join(out_dir, _route_filename(route))
                # "load" + a short settle beats "networkidle", which can hang forever
                # on apps that keep a connection open (streaming, polling).
                page.goto(base + route, wait_until="load", timeout=30_000)
                page.wait_for_timeout(_PAGE_SETTLE_MS)
                page.screenshot(path=path, full_page=True)
                refs.append(path)
        finally:
            browser.close()
    return refs


def capture_preview_screenshots(
    profile: ProjectProfile,
    diffs: list[str],
    out_dir: str,
    *,
    runner=_run,
    probe=_http_ok,
    shooter=_shoot_routes,
) -> ScreenshotSet:
    """The whole capture: clone + apply the pod's diffs, bring the preview up, wait for
    ready, screenshot the routes, ALWAYS tear down. Pure-ish (runner/probe/shooter
    injected) so tests drive every path at $0 with no docker or browser."""
    preview = profile.preview
    if preview is None:
        return ScreenshotSet(captured=False, note="no preview configured for this project")
    diffs = [d for d in diffs if d.strip()]
    if not diffs:
        return ScreenshotSet(captured=False, note="no diff to preview")

    try:
        checkout = _clone_and_apply(profile.repo.git_remote, diffs, runner=runner)
    except RuntimeError as exc:
        return ScreenshotSet(captured=False, note=f"preview checkout failed: {exc}"[:300])
    except Exception as exc:  # noqa: BLE001 — advisory: never raise past this module
        return ScreenshotSet(captured=False, note=f"preview checkout failed: {exc}"[:300])

    try:
        up = runner(preview.up, cwd=checkout, timeout=preview.up_timeout_s)
        if up.returncode != 0:
            tail = (up.stderr or up.stdout or "").strip()[-200:]
            return ScreenshotSet(captured=False, note=f"preview up failed: {tail}")
        ready_url = preview.url.rstrip("/") + preview.ready_path
        if not _await_ready(ready_url, preview.ready_timeout_s, probe=probe):
            return ScreenshotSet(
                captured=False,
                note=f"preview never became ready at {ready_url} "
                f"within {preview.ready_timeout_s}s",
            )
        os.makedirs(out_dir, exist_ok=True)
        refs = shooter(preview, out_dir)
        return ScreenshotSet(
            captured=bool(refs),
            refs=refs,
            note=f"{len(refs)} route(s) captured" if refs else "no routes captured",
        )
    except ModuleNotFoundError as exc:
        return ScreenshotSet(
            captured=False,
            note=f"screenshot tooling unavailable ({exc}); install the [preview] extra "
            "and run `playwright install chromium`",
        )
    except Exception as exc:  # noqa: BLE001 — advisory: never raise after the coding pass
        _log.warning("preview capture failed for %s: %s", profile.id, exc)
        return ScreenshotSet(captured=False, note=f"screenshot capture failed: {exc}"[:300])
    finally:
        try:
            runner(preview.down, cwd=checkout, timeout=300)
        except Exception as exc:  # noqa: BLE001 — teardown is best-effort
            _log.warning("preview teardown failed for %s: %s", profile.id, exc)
        shutil.rmtree(os.path.dirname(checkout), ignore_errors=True)


def _q(value: str) -> str:
    import shlex

    return shlex.quote(value)
