"""Reference project #2 — the gardening-assistant testbed (CLAUDE.md, Reference projects).

Like meal_planner.py, this is DATA describing a target app, not part of the org. The org
reads this profile and stays generic; adding this project was a new profile + one loader
line, no org code changed (§3).

Notable target-specifics that shape the fields below:
  * Auth is **Clerk** (Google OAuth + email/password), not a homegrown signup endpoint — so
    there's no throwaway-signup path to authenticate a preview browser, and the app's own
    docker-compose ships only db/minio/mailpit (no containerized `app` service). A
    §9.6-compliant post-QA screenshot preview would need a repo-provided app container +
    real Clerk test creds, neither of which exists yet — so `preview` is omitted (QA + the
    PR's CI are the quality gate). Wire a Preview later if the repo adds an app container.
  * Prod deploys via **Coolify push-to-deploy on `main`** (Nixpacks + managed Postgres), so
    deploy.kind=MERGE: a merge to `main` *is* the production deploy — behind the workflow's
    human deploy gate (§9.2), never unattended.
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
    id="gardening-assistant",
    name="Gardening & Lawn Care Assistant",
    description=(
        "A proactive, agent-native PWA that keeps a household's garden and lawn healthy: "
        "it watches local weather, computes a soil-water balance to schedule/adjust "
        "watering, tracks fertilizer/pruning/harvest windows, diagnoses plant photos, and "
        "sends plain-language reminders (with the why). A cron-driven care engine generates "
        "the schedule; users also chat with the assistant and log what they did."
    ),
    repo=Repo(
        git_remote="git@github.com:sheastyer/gardening-assistant.git",
        default_branch="main",
        local_path="~/Projects/gardening-assistant",
    ),
    stack=Stack(
        languages=["typescript"],
        package_manager="npm",
        test_command="npm test",       # vitest run (unit); integration/e2e self-skip without a DB
        build_command="npm run build",  # next build
        # Same posture as meal-planner: the suite needs `npm ci` + Postgres (and Playwright a
        # browser), none of which the offline coding sandbox provides — so in-sandbox QA
        # reports "unavailable" and the PR's GitHub CI (lint + typecheck + vitest) is the gate.
        sandbox_tests=False,
    ),
    # Intake adapter is not wired yet (M5). For these runs feedback enters via the cli.run
    # driver, so MANUAL is the honest kind; descriptor records the eventual source.
    intake=Intake(kind=IntakeKind.MANUAL, descriptor="cli.run driver (M5 intake adapter TBD)"),
    # deploy = merge the pod's PR to `main`, which Coolify auto-deploys to prod. Always behind
    # the workflow's human deploy-approval gate (§9.2); the merge carries an idempotency key so
    # a Temporal retry can't double-merge.
    deploy=Deploy(
        kind=DeployKind.MERGE,
        descriptor="open PR in pod; merge to main on deploy approval (triggers Coolify prod deploy)",
    ),
    conventions=[
        "TypeScript + Next.js (App Router, Next 16); Drizzle ORM over Postgres (pgvector).",
        "Auth is Clerk — server code reads garden_group_id / clerk_user_id from session claims.",
        "LLM access ONLY via the Vercel AI Gateway through the Vercel AI SDK (`ai` package); "
        "never import a provider SDK in app code (vision included via the gateway).",
        "Match existing code style; keep changes minimal and focused.",
        "All changes land via PR — never push to main directly.",
        "CI must stay green: `npm run lint`, `npm run typecheck` (tsc --noEmit), and "
        "`npm test` (vitest run) all run on push and gate the PR.",
    ],
    secret_refs={
        # logical name -> env var name (value lives in the secret store, never here). The pod
        # actually opens/merges PRs via the ambient `gh` CLI + git ssh; this is the §3 reference.
        "github_token": "GARDENING_GITHUB_TOKEN",
    },
    # preview omitted — see module docstring (Clerk auth + no app container in the compose file).
)
