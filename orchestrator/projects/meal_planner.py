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
    Preview,
    PreviewLogin,
    ProjectProfile,
    Repo,
    Stack,
)

# Post-QA screenshot preview (see profile.Preview). The repo's own docker-compose stack
# (db + migrate + app, health at /api/health) boots the checkout with the pod's diff
# applied — repo code stays containerized (D9). Isolation from a real local stack:
# a dedicated compose project name + APP_PORT 3411 give this run its own volumes,
# network, and port. (Caveat: the compose file pins container_name for db/app, which
# -p can't rename — don't run the preview on a host already running the real stack.)
# The .env.local the compose file requires is generated inline; TAVILY_API_KEY is a
# dummy (recipe search degrades, pages still render). The login spec signs up a
# throwaway household against the empty preview DB, so authed pages render too —
# a brand-new household lands on /onboarding, which is honest first-run UI.
_PREVIEW_ENV = "APP_PORT=3411\\nPOSTGRES_PASSWORD=preview\\nTAVILY_API_KEY=preview-dummy\\n"
_COMPOSE = "docker compose --env-file .env.local -p mealplanner-preview"

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
    preview=Preview(
        up=f"printf '{_PREVIEW_ENV}' > .env.local && {_COMPOSE} up -d --build",
        down=f"{_COMPOSE} down -v --remove-orphans",
        url="http://localhost:3411",
        ready_path="/api/health",
        ready_timeout_s=300,
        up_timeout_s=900,  # first compose build of the Next.js image takes minutes
        routes=["/login", "/onboarding", "/calendar", "/shopping"],
        # Signup against the empty preview DB sets the session cookie; the credential
        # is a disposable fixture for an ephemeral database, not a secret. Username
        # must match the app's ^[a-z0-9_]{3,30}$ (no hyphens — a hyphen 400s, observed
        # in the 2026-07-07 live validation).
        login=PreviewLogin(
            api_path="/api/auth/signup",
            json_body={"username": "org_preview", "password": "org-preview-pass"},
        ),
    ),
)
