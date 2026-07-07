"""Post-QA screenshots: capture module, profile preview config, workflow wiring, and
the Slack thread uploads — all at $0 (no docker, no browser, no Slack, no tokens).

The contract under test, end to end:
- the Project Profile's optional ``preview`` declares how to boot the target and what
  to shoot (project knowledge, §3), and validate() rejects half-configured previews;
- ``capture_preview_screenshots`` clones + applies the pod's diff, brings the preview
  up, waits for ready, shoots, and ALWAYS tears down — and converts every failure into
  ``captured=False`` + a note instead of raising (advisory after the paid coding pass);
- the pod workflow captures only when QA passed and carries refs on PodResult; the
  parent posts them into the run's thread (engineering notice image_refs + a line, and
  a deploy-gate context line);
- the live notifier uploads each image into the thread and degrades on failure.
"""

import subprocess
import sys

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from orchestrator.activities.stubs import capture_screenshots as capture_stub
from orchestrator.agents.coding.preview import _run, capture_preview_screenshots
from orchestrator.humanio.notify import notify_progress_with_client
from orchestrator.projects.loader import load_profile
from orchestrator.projects.profile import (
    Deploy,
    DeployKind,
    Intake,
    IntakeKind,
    Preview,
    ProjectProfile,
    Repo,
    Stack,
)
from orchestrator.shared.config import TASK_QUEUE
from orchestrator.shared.types import (
    GateNotice,
    NotifyResult,
    ProgressNotice,
    QAResult,
    ScreenshotSet,
    Status,
    StoryResult,
)
from orchestrator.workflows.feature_request import FeatureRequestWorkflow
from tests.helpers import (
    ALL_WORKFLOWS,
    TEMPORAL_CLI,
    activities_with,
    feature_event,
    wait_until,
)

GET_STATE = FeatureRequestWorkflow.get_state


# --- fixtures ----------------------------------------------------------------------
def _git(args: str, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        f"git -c user.email=t@t -c user.name=t {args}",
        cwd=cwd, shell=True, capture_output=True, text=True,
    )


def _seeded_git_repo(tmp_path) -> str:
    repo = tmp_path / "target"
    repo.mkdir()
    (repo / "app.py").write_text("print('hello')\n")
    _git("init -q", str(repo))
    _git("add -A", str(repo))
    _git("commit -q -m baseline", str(repo))
    return str(repo)


def _real_diff(tmp_path, repo: str) -> str:
    """A unified diff produced the way the pod produces one (git diff in a clone), so
    `git apply --3way` in the capture's checkout is exercised for real."""
    clone = tmp_path / "diffclone"
    subprocess.run(
        f"git clone -q {repo} {clone}", shell=True, capture_output=True, text=True
    )
    (clone / "app.py").write_text("print('hello, preview')\n")
    out = _git("diff", str(clone))
    assert out.stdout.strip()
    return out.stdout


def _preview(**overrides) -> Preview:
    base = dict(
        up="true", down="true", url="http://localhost:9",
        ready_path="/health", ready_timeout_s=1, up_timeout_s=30, routes=["/", "/plan"],
    )
    base.update(overrides)
    return Preview(**base)


def _profile(repo: str, preview: Preview | None) -> ProjectProfile:
    return ProjectProfile(
        id="fixture", name="Fixture", description="seeded test target",
        repo=Repo(git_remote=repo, default_branch="main"),
        stack=Stack(languages=["python"], package_manager="pip", test_command="true"),
        intake=Intake(kind=IntakeKind.MANUAL),
        deploy=Deploy(kind=DeployKind.OPEN_PR),
        preview=preview,
    )


class _RecordingRunner:
    """Delegates git plumbing to the real runner; records + fakes the preview's own
    up/down commands so no docker ever runs."""

    def __init__(self, up_rc: int = 0):
        self.up_rc = up_rc
        self.commands: list[str] = []

    def __call__(self, command: str, cwd: str, timeout: int = 900):
        if command.startswith("git "):
            return _run(command, cwd=cwd, timeout=timeout)
        self.commands.append(command)
        rc = self.up_rc if command == "up-cmd" else 0
        return subprocess.CompletedProcess(command, rc, stdout="", stderr="boom")


def _fake_shooter(preview: Preview, out_dir: str) -> list[str]:
    import os

    refs = []
    for i, _route in enumerate(preview.routes):
        path = os.path.join(out_dir, f"shot-{i}.png")
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG fake")
        refs.append(path)
    return refs


# --- profile validation --------------------------------------------------------------
def test_preview_config_requires_up_down_url():
    with pytest.raises(ValueError, match="preview needs up, down, and url"):
        _profile("file:///x", _preview(down="")).validate()
    with pytest.raises(ValueError, match="routes must be non-empty"):
        _profile("file:///x", _preview(routes=[])).validate()
    _profile("file:///x", _preview()).validate()  # complete preview is fine
    _profile("file:///x", None).validate()        # and preview stays optional


def test_meal_planner_profile_declares_a_valid_preview():
    profile = load_profile("meal-planner")
    assert profile.preview is not None
    assert "docker compose" in profile.preview.up  # repo code stays containerized (D9)
    assert "-p mealplanner-preview" in profile.preview.up  # isolated from a real stack
    assert profile.preview.login is not None
    assert profile.preview.routes


# --- capture module ------------------------------------------------------------------
def test_capture_without_preview_config_is_an_honest_no_op(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    result = capture_preview_screenshots(_profile(repo, None), ["diff"], str(tmp_path / "out"))
    assert result.captured is False and "no preview" in result.note


def test_capture_without_a_diff_is_an_honest_no_op(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    result = capture_preview_screenshots(
        _profile(repo, _preview()), ["", "   "], str(tmp_path / "out")
    )
    assert result.captured is False and "no diff" in result.note


def test_capture_happy_path_shoots_every_route_and_tears_down(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    diff = _real_diff(tmp_path, repo)
    runner = _RecordingRunner()
    result = capture_preview_screenshots(
        _profile(repo, _preview(up="up-cmd", down="down-cmd")),
        [diff],
        str(tmp_path / "out"),
        runner=runner,
        probe=lambda url: True,
        shooter=_fake_shooter,
    )
    assert result.captured is True
    assert len(result.refs) == 2  # one per declared route
    for ref in result.refs:
        assert ref.endswith(".png")
    # up ran, and down ALWAYS runs after it (teardown in finally)
    assert runner.commands == ["up-cmd", "down-cmd"]


def test_capture_reports_up_failure_and_still_tears_down(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    diff = _real_diff(tmp_path, repo)
    runner = _RecordingRunner(up_rc=1)
    result = capture_preview_screenshots(
        _profile(repo, _preview(up="up-cmd", down="down-cmd")),
        [diff], str(tmp_path / "out"), runner=runner, probe=lambda url: True,
        shooter=_fake_shooter,
    )
    assert result.captured is False and "up failed" in result.note
    assert runner.commands == ["up-cmd", "down-cmd"]


def test_capture_reports_never_ready(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    diff = _real_diff(tmp_path, repo)
    runner = _RecordingRunner()
    result = capture_preview_screenshots(
        _profile(repo, _preview(up="up-cmd", down="down-cmd", ready_timeout_s=0)),
        [diff], str(tmp_path / "out"), runner=runner,
        probe=lambda url: False, shooter=_fake_shooter,
    )
    assert result.captured is False and "never became ready" in result.note
    assert "down-cmd" in runner.commands


def test_capture_never_raises_when_the_shooter_blows_up(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    diff = _real_diff(tmp_path, repo)
    runner = _RecordingRunner()

    def exploding_shooter(preview, out_dir):
        raise RuntimeError("browser exploded")

    result = capture_preview_screenshots(
        _profile(repo, _preview(up="up-cmd", down="down-cmd")),
        [diff], str(tmp_path / "out"), runner=runner,
        probe=lambda url: True, shooter=exploding_shooter,
    )
    assert result.captured is False and "browser exploded" in result.note
    assert "down-cmd" in runner.commands  # teardown survived the failure


def test_capture_reports_unapplicable_diff(tmp_path):
    repo = _seeded_git_repo(tmp_path)
    runner = _RecordingRunner()
    result = capture_preview_screenshots(
        _profile(repo, _preview(up="up-cmd", down="down-cmd")),
        ["not a real diff\n"], str(tmp_path / "out"), runner=runner,
        probe=lambda url: True, shooter=_fake_shooter,
    )
    assert result.captured is False and "checkout failed" in result.note
    assert runner.commands == []  # nothing to tear down: we never got to `up`


async def test_stub_capture_is_a_free_no_op():
    result = await capture_stub("meal-planner", [])
    assert result.captured is False and "(stub)" in result.note
    assert result.cost_tokens == 0 and result.cost_usd == 0.0


# --- workflow wiring -----------------------------------------------------------------
def _capture_recorder(calls: list, refs: list[str]):
    @activity.defn(name="capture_screenshots")
    async def capture(project: str, story_results: list[StoryResult]) -> ScreenshotSet:
        calls.append(project)
        return ScreenshotSet(captured=bool(refs), refs=list(refs), note="2 route(s) captured")

    return capture


def _progress_recorder(records: list[ProgressNotice]):
    @activity.defn(name="notify_progress")
    async def record(notice: ProgressNotice) -> NotifyResult:
        records.append(notice)
        return NotifyResult(delivered=True, ts="1111.2222")

    return record


def _gate_recorder(records: list[GateNotice]):
    @activity.defn(name="notify_gate")
    async def record(notice: GateNotice) -> NotifyResult:
        records.append(notice)
        return NotifyResult(delivered=True, ts="9999.0000")

    return record


@pytest.mark.asyncio
async def test_feature_run_threads_screenshots_through_slack():
    """QA passed -> the pod captures, PodResult carries refs, the engineering post
    uploads them into the thread, and the deploy gate points at them."""
    refs = ["runs/p/screenshots/wf/login.png", "runs/p/screenshots/wf/calendar.png"]
    calls: list[str] = []
    progress: list[ProgressNotice] = []
    gates: list[GateNotice] = []
    overrides = {
        "capture_screenshots": _capture_recorder(calls, refs),
        "notify_progress": _progress_recorder(progress),
        "notify_gate": _gate_recorder(gates),
    }
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(overrides),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "shea"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, args=["approve", "shea"])
            await wait_until(handle, lambda s: s.stage == "deploy_approval", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_deploy_approval, args=[True, "shea"])
            result = await handle.result()

    assert result.status == Status.SHIPPED
    assert calls == ["meal-planner"]  # captured exactly once, for the run's project
    engineering = next(n for n in progress if n.stage == "engineering")
    assert engineering.image_refs == refs
    assert any("screenshots: 2 in thread" in line for line in engineering.text)
    deploy_gate = next(g for g in gates if g.gate == "deploy")
    assert any("screenshots: 2 in thread" in line for line in deploy_gate.context)


@pytest.mark.asyncio
async def test_failed_qa_skips_capture_and_halts():
    """QA failed (after the bounded fix loop) -> no screenshot attempt, run halts at
    QA_FAILED — 'screenshots of successful QA' means exactly that."""
    calls: list[str] = []
    progress: list[ProgressNotice] = []

    @activity.defn(name="qa_review")
    async def failing_qa(project: str, story_results: list[StoryResult]) -> QAResult:
        return QAResult(passed=False, notes="red", cost_tokens=1)

    overrides = {
        "qa_review": failing_qa,
        "capture_screenshots": _capture_recorder(calls, ["x.png"]),
        "notify_progress": _progress_recorder(progress),
    }
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=TEMPORAL_CLI) as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE, workflows=ALL_WORKFLOWS,
            activities=activities_with(overrides),
        ):
            event = feature_event()
            handle = await env.client.start_workflow(
                FeatureRequestWorkflow.run, event, id=event.id, task_queue=TASK_QUEUE
            )
            await handle.signal(FeatureRequestWorkflow.submit_human_vote, args=[True, "shea"])
            await wait_until(handle, lambda s: s.stage == "pm_signoff", GET_STATE)
            await handle.signal(FeatureRequestWorkflow.submit_pm_signoff, args=["approve", "shea"])
            result = await handle.result()

    assert result.status == Status.QA_FAILED
    assert calls == []  # capture never ran
    engineering = next(n for n in progress if n.stage == "engineering")
    assert engineering.image_refs == []
    assert not any("screenshots" in line for line in engineering.text)


# --- Slack thread uploads --------------------------------------------------------------
class _FakeWebClient:
    def __init__(self, fail_upload: Exception | None = None):
        self.fail_upload = fail_upload
        self.posts: list[dict] = []
        self.uploads: list[dict] = []

    def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"ok": True, "ts": "111.222"}

    def files_upload_v2(self, **kwargs):
        if self.fail_upload:
            raise self.fail_upload
        self.uploads.append(kwargs)
        return {"ok": True}


def _notice(image_refs: list[str], thread_ts: str = "1111.2222") -> ProgressNotice:
    return ProgressNotice(
        workflow_id="feedback-123", stage="engineering", title="demo",
        project="meal-planner", text=["QA passed"], image_refs=image_refs,
        thread_ts=thread_ts,
    )


def test_progress_post_uploads_screenshots_into_the_thread(tmp_path):
    shot = tmp_path / "login.png"
    shot.write_bytes(b"\x89PNG fake")
    client = _FakeWebClient()
    result = notify_progress_with_client(_notice([str(shot)]), client, "C0TEST")
    assert result.delivered is True and result.note == ""
    (upload,) = client.uploads
    assert upload["thread_ts"] == "1111.2222"
    assert upload["file"] == str(shot) and upload["filename"] == "login.png"


def test_missing_screenshot_file_degrades_to_a_note(tmp_path):
    shot = tmp_path / "real.png"
    shot.write_bytes(b"\x89PNG fake")
    client = _FakeWebClient()
    result = notify_progress_with_client(
        _notice([str(tmp_path / "gone.png"), str(shot)]), client, "C0TEST"
    )
    assert result.delivered is True
    assert "screenshot upload failed: gone.png" in result.note
    (upload,) = client.uploads  # the existing file still made it
    assert upload["filename"] == "real.png"


def test_slack_upload_error_never_fails_the_notification(tmp_path):
    shot = tmp_path / "login.png"
    shot.write_bytes(b"\x89PNG fake")
    client = _FakeWebClient(fail_upload=RuntimeError("slack down"))
    result = notify_progress_with_client(_notice([str(shot)]), client, "C0TEST")
    assert result.delivered is True
    assert "screenshot upload failed: login.png" in result.note
