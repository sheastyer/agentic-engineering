"""Pure pod functions an activity calls — open a workspace, code, verify with the target's
own tests, tear down. Pure (agent injected, no Temporal) so they're unit-testable at $0.

**Workspace lifecycle = one activity.** A coding attempt and its verification share one
managed workspace (the temp checkout can't — and shouldn't — survive across separate,
stateless Temporal activities). So `implement_and_verify` does both: it runs the agent and
then runs the target's test command on the *same* edits, returning the coding outcome plus
a QA verdict. The workflow's bounded QA→fix loop (§10, MAX_QA_FIX_PASSES) re-invokes this
for any story whose verdict is a fail — orchestration stays in the workflow, execution here.
"""

from orchestrator.agents.coding.agent import CodingAgent
from orchestrator.agents.coding.sandbox import Sandbox
from orchestrator.agents.coding.types import CodingOutcome, CodingTask, QAOutcome
from orchestrator.agents.coding.workspace import Workspace


def run_qa(workspace: Workspace) -> QAOutcome:
    """QA gate = the target repo's own test command. Pass == exit 0 (no false greens)."""
    run = workspace.run_tests()
    note = "all target tests passed" if run.passed else f"target tests failed (exit {run.returncode})"
    return QAOutcome(passed=run.passed, notes=note)


async def implement_and_verify(
    agent: CodingAgent,
    task: CodingTask,
    source: str,
    *,
    from_git: bool = False,
    sandbox: Sandbox | None = None,
) -> tuple[CodingOutcome, QAOutcome]:
    """One coding attempt in a fresh, disposable workspace; verified by the target's tests.

    The workspace is context-managed, so it is always torn down (§9.6 cleanup) even if the
    agent or test run raises. `sandbox` is the boundary the *target's test command* runs in
    (a `ContainerSandbox` for untrusted input — D9); it defaults to local for trusted fixtures.
    """
    with Workspace(
        source, test_command=task.test_command, from_git=from_git, sandbox=sandbox
    ) as ws:
        outcome = await agent.implement(task, ws)
        qa = run_qa(ws)
    return outcome, qa
