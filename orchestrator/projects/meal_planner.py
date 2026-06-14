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
    ),
    # Placeholders until M5 — meal-planner uses Next.js + Drizzle/Postgres, so feedback
    # will most likely surface as a DB table the intake adapter polls.
    intake=Intake(kind=IntakeKind.DB_TABLE, descriptor="feedback"),
    # Placeholder until M4/M6 — start by opening a PR against the repo.
    deploy=Deploy(kind=DeployKind.OPEN_PR, descriptor="default-branch PR"),
    conventions=[
        "TypeScript + Next.js (App Router); Drizzle ORM over Postgres.",
        "Match existing code style; keep changes minimal and focused.",
        "All changes land via PR — never push to main directly.",
    ],
    secret_refs={
        # logical name -> env var name (the value lives in the secret store, never here)
        "github_token": "MEALPLANNER_GITHUB_TOKEN",
    },
)
