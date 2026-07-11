# Deploying the org on Coolify

This runbook takes the org from "laptop + `cli.run`" to a standing deployment:
Temporal (with its Web UI) runs as a Coolify compose stack, the worker runs **both
planes live** (reasoning on the Vercel AI Gateway, coding on the Claude
subscription), gates go through **Slack**, and feedback enters through an **HTTP
intake endpoint** instead of the CLI.

- **Build pack:** Docker Compose → [`docker-compose.coolify.yml`](../docker-compose.coolify.yml)
- **Stack:** `postgres` + `temporal` + `ui` + `worker` + `slack-listener` + `intake`
  (the last three share one image, [`infra/Dockerfile`](../infra/Dockerfile))

## 1. Create the resource

New Resource → your GitHub App source → this repo → **Docker Compose** build pack →
compose file `docker-compose.coolify.yml`, branch `main`.

## 2. Environment variables

Set on the compose resource (compose reads them via `${...}` / bare pass-through).
Mark secrets as such:

| Variable | Value | Secret |
|---|---|---|
| `AI_GATEWAY_API_KEY` | Vercel AI Gateway key — the reasoning plane | ✔ |
| `CLAUDE_CODE_OAUTH_TOKEN` | run `claude setup-token` on your machine, paste the token — the coding pod's subscription auth. Leave `ANTHROPIC_API_KEY` unset (D3: no API credit) | ✔ |
| `GH_TOKEN` | GitHub PAT with `repo` scope on the target repos — clone/push/PR/merge (`gh` + the entrypoint's ssh→https rewrite) | ✔ |
| `SLACK_BOT_TOKEN` | `xoxb-…`, scopes `chat:write` + `files:write` | ✔ |
| `SLACK_APP_TOKEN` | `xapp-…`, Socket Mode (listener only) | ✔ |
| `SLACK_CHANNEL_ID` | the gates/progress channel, `C0…` | |
| `SLACK_APPROVER_IDS` | comma-separated Slack user ids allowed to move gates | |
| `INTAKE_TOKEN` | strong shared secret; callers send `Authorization: Bearer <token>` | ✔ |
| `SERVICE_PASSWORD_POSTGRES` | leave to Coolify — generated | (✔) |

Optional: `CODING_AGENT` (default `claude`; `mock` for a $0 smoke deploy),
`CODING_PR_TARGET` (default `github`; `local` for no-push dry runs),
`CODING_SANDBOX_IMAGE`, `CODING_AGENT_IMAGE`, `MEALPLANNER_GITHUB_TOKEN`.

## 3. Domains

- **`ui` (port 8080)** — the Temporal Web UI. ⚠ It ships **no authentication**:
  put the domain behind Cloudflare Access (like the meal-planner app) or keep it
  LAN-only. From it you can watch every run: workflow → History shows each stage,
  pending activities, and the signals the gates wait on.
- **`intake` (port 8000)** — the feedback endpoint. Bearer-token-protected but not
  rate-limited; keep it behind Access / LAN-only (see the security model section
  below — feedback text ultimately reaches a coding agent).
- `temporal` also publishes **gRPC 7233 on the host** so `cli.run` / `cli.trace` /
  `evals.run` on the LAN can drive the deployed org
  (`TEMPORAL_TARGET=<coolify-host>:7233 python -m cli.trace …`). There is no auth
  on that port — remove the `ports:` mapping if the host is exposed beyond the LAN.

## 4. Host prerequisites (one-time, on the Coolify server)

The worker drives the **host's Docker** through the mounted socket — that's the D9
container sandbox and the screenshot preview stack. Two consequences:

- `/var/lib/agentic-org/work` is bind-mounted at the **same path** in the worker so
  the paths the worker passes to `docker run -v` resolve on the host. Docker creates
  the directory on first deploy; nothing to do unless you relocate it (then change
  both sides of the mount **and** `TMPDIR` together).
- Workspaces are ephemeral but not auto-pruned on crash; an occasional
  `rm -rf /var/lib/agentic-org/work/agentic-*` on the host is fine housekeeping.

## 5. Submitting feedback

```bash
curl -sS https://intake.example.com/feedback \
  -H "Authorization: Bearer $INTAKE_TOKEN" -H "Content-Type: application/json" \
  -d '{
        "project": "meal-planner",
        "kind": "feature",
        "title": "Add a dark mode toggle",
        "body": "As a user I want …",
        "id": "optional-idempotency-key"
      }'
# → 202 {"workflow_id":"feedback-…","run_id":"…"}   (409 on a re-posted id)
```

`kind` is `feature` or `bug`; `project` must be a registered Project Profile
(`GET /healthz` lists them). From there the run is Slack-native: the thread shows
each stage, and council/sign-off/budget/deploy park on their Slack buttons — the
worker never auto-approves anything. A Kafka (or webhook) intake later is a new
consumer calling the same `orchestrator.intake.route()`; this service is that seam.

## 6. Smoke test, cheapest first

1. Deploy with `CODING_AGENT=mock` and `CODING_PR_TARGET=local` set, watch a
   feedback POST run through every stage in the UI + Slack for ~$0.30 of
   reasoning (no coding spend, no push).
2. Clear both overrides (falls back to `claude` + `github`) and run a real
   feature end-to-end: fund the coding-budget gate in Slack, approve the deploy
   gate, and check the PR on the target repo.

## ⚠ Security model — read before exposing the intake

The default `CODING_AGENT=claude` runs the coding agent's own process (its Bash
tool included) **inside the worker container**, not inside the D9 sandbox — only
the target's *test command* is containerized (`CODING_SANDBOX=container`). The
worker container mounts the host Docker socket (root-equivalent on the host) and
holds every org secret, and the intake endpoint feeds attacker-controlled
feedback text toward that agent's prompt. Prompt-injection hygiene is real but
is a soft boundary. Consequences:

- **Do not expose `/feedback` to the open internet.** Keep it behind Cloudflare
  Access / LAN-only and treat `INTAKE_TOKEN` as a real secret. Feedback should
  come from sources you trust at least as much as a bug tracker.
- The hardened option is `CODING_AGENT=claude_container` (the agent process
  itself inside the D9 boundary): set it plus `CODING_AGENT_IMAGE` (an image
  carrying the `claude` CLI + the target's runtime; none is published — build
  per deployment) and pass credentials via `CODING_AGENT_CRED_ENV`. Trade-off:
  it currently runs single-session (no orchestrator mode / per-story tiers).
- Scope `GH_TOKEN` to the target repos only (fine-grained PAT), never an
  org-wide classic token.

## Gotchas

- **Crash-loops right after deploy are normal** — worker/listener/intake fail fast
  until `temporal` finishes auto-setup (~30 s on first boot while schemas load).
  A *persistent* worker crash-loop means a missing env var — its log names it.
- **Post-QA screenshots degrade in this topology** (advisory by design): the
  preview stack publishes its port on the *host*, and profiles point at
  `localhost`, which inside the worker container is not the host. Runs proceed
  with `captured=False`; fixing it means routing profile preview URLs at the
  host gateway (follow-up).
- **The 5-hour subscription window is shared** with your own Claude Code usage —
  the deployed pod draws on the same subscription the `CLAUDE_CODE_OAUTH_TOKEN`
  belongs to.
- **Don't run a laptop worker against the deployed Temporal** (or vice versa) —
  stale workers on the shared task queue steal activities (the PLAN.md
  stale-worker gotcha, now cross-host).
