"""Reference project #1 — the meal-planner testbed (CLAUDE.md, Reference projects).

This is DATA describing a target app, not part of the org. Intake/deploy descriptors are
placeholders until M5/M4 wire the real adapters; the org reads this profile and stays
generic.
"""

from orchestrator.projects.profile import (
    Deploy,
    DeployKind,
    Intake,
    IntakeKind,
    ProjectProfile,
    Repo,
    Stack,
)

PROFILE = ProjectProfile(
    id="meal-planner",
    name="Meal Planner",
    description=(
        "A barebones agentic meal-planner: takes a household profile and, via a chat "
        "interface, surfaces recipes to plan the week."
    ),
    repo=Repo(
        git_remote="git@github.com:sheastyer/meal-planner.git",
        default_branch="main",
        local_path="~/Projects/meal-planner",
    ),
    stack=Stack(
        languages=["typescript"],
        package_manager="npm",
        test_command="npm test",
        build_command="npm run build",
        # The suite needs npm install + a browser; the offline coding sandbox can't run it,
        # so in-sandbox QA reports "unavailable" and the PR's GitHub CI is the real gate.
        sandbox_tests=False,
    ),
    # Placeholders until M5 — meal-planner uses Next.js + Drizzle/Postgres, so feedback
    # will most likely surface as a DB table the intake adapter polls.
    intake=Intake(kind=IntakeKind.DB_TABLE, descriptor="feedback"),
    # D6 (resolved 2026-06-16): deploy = open + merge PR. The engineering pod opens a PR
    # against the repo (so humans review the real diff); on the deploy-approval gate the
    # deploy activity merges it to the default branch. Both side-effects carry an
    # idempotency key (M4) so a Temporal retry can't double-open/double-merge.
    deploy=Deploy(kind=DeployKind.MERGE, descriptor="open PR in pod; merge to default branch on deploy approval"),
    conventions=[
        "TypeScript + Next.js (App Router); Drizzle ORM over Postgres.",
        "Match existing code style; keep changes minimal and focused.",
        "All changes land via PR — never push to main directly.",
        "AI SDK v6: use `maxOutputTokens` (not the v5 `maxTokens`) in generateText/streamText — "
        "`maxTokens` is a TypeScript compile error that fails CI (observed 2026-06-28).",
    ],
    secret_refs={
        # logical name -> env var name (the value lives in the secret store, never here)
        "github_token": "MEALPLANNER_GITHUB_TOKEN",
    },
)
