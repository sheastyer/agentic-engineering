---
name: run-org
description: Run the simulated product/engineering org on a piece of feedback end-to-end — brings up Temporal + a worker, drives a FeatureRequest/Bug workflow through every stage and human gate, babysits failures, reports a per-stage summary, and surfaces optimization opportunities. Use when the user wants to "run the org/workflow", "ship a feature", "fix a bug through the org", or gives a feedback-shaped instruction like "add dark mode to meal-planner" / "the weekly-plan save button errors out".
---

# Run the org on a piece of feedback

This repo is a simulated software org (PM, council, architect, engineers, QA, research) wired
on **Temporal** (orchestration) + the **Claude Agent SDK** (coding). This skill drives one
piece of feedback through the whole thing and reports back. Read `CLAUDE.md` (architecture +
invariants) and `PLAN.md` ("Current state & how to continue") if anything here is unclear —
**the code is the source of truth**; if a flag or path below no longer matches, trust the repo
and tell the user.

## What "run" means here (defaults)

- **Default mode = Full live → real PR.** Reasoning on the Vercel gateway, coding on the
  Claude subscription, opens a **real GitHub PR** on the target. Real cost (~$2/feature) and a
  real outward side-effect.
- The skill **manages infra itself** (Temporal dev server + worker), starting them if down.
- `cli.run` auto-plays every human gate (council, budget, sign-off, deploy) — a steel-thread run.

Other modes the user may ask for:
- **Cheap** ("dry run", "no PR", "cheap"): live reasoning, **mock coding**, no PR
  (`CODING_PR_TARGET=local`, drop `USE_AGENT_CODING`). ~$0.25.
- **$0 stub** ("stub", "just the flow"): no `USE_AGENT_*` flags at all. 0 tokens, no PR.

## Step 0 — Parse the request and confirm (do this first)

1. Extract from the user's instruction:
   - **title** — the feedback one-liner (e.g. "Add a dark mode theme toggle to the app").
   - **kind** — *bug* if it describes something broken ("errors", "crashes", "wrong"),
     otherwise *feature*. Bugs use `--bug`.
   - **project** — the Project Profile id, default `meal-planner`.
2. **A full-live run spends real money and opens a real PR, and needs `bypassPermissions`
   (an autonomous host coding agent).** Unless the user already clearly authorized a live run
   in this turn, confirm before launching: state the mode, the rough cost, that it will open a
   PR on `<project>`, and that the coding agent runs with `bypassPermissions`. If they want
   cheap/stub instead, adjust the flags per "Other modes" above. Also tell them that **every**
   run (all modes) writes an audit folder to `runs/<project>/…` in *this* repo and **auto-opens
   an audit PR on `agentic-engineering`** (Step 4) — a second, internal side-effect distinct
   from the product PR.
3. For a live PR run, check the **target repo is on a clean `main`** so the PR has a clean base:
   `git -C ~/Projects/<project> status -sb`. If it's dirty or off `main`, surface that and ask
   before proceeding (a messy base produces a messy PR).

## Step 1 — Bring up infra (skill manages it)

Source env in the **same** command that uses it — shell state does not persist between Bash
calls. The repo venv is `./.venv`; Python 3.14.

1. **Temporal dev server** — start only if `:7233` is down. Use an **absolute** `--db-filename`
   so every startup persists to the SAME sqlite file and *all* prior runs stay visible in
   `temporal workflow list` / the trace tools. A relative path (or omitting the flag) silently
   forks history: started from another CWD it writes a different file, and with no flag the dev
   server is in-memory and every prior run vanishes on restart.
   ```bash
   nc -z localhost 7233 2>/dev/null || { \
     mkdir -p "$HOME/Projects/agentic-engineering/.localdata" && \
     ~/.temporalio/bin/temporal server start-dev \
       --db-filename "$HOME/Projects/agentic-engineering/.localdata/temporal-dev.db"; }
   ```
   Start it with `run_in_background` (it stays up across the run). The db file is gitignored
   (local-only) — it's the machine's cumulative run history, not a committed artifact.

2. **Worker** — start in the background, logging to a file you can tail for the babysit step.
   The `env -u …` strip is **required**: a worker launched from inside a Claude Code session
   otherwise leaks `CLAUDECODE`/`CLAUDE_CODE_*` into the spawned `claude`, which fails with the
   misleading **"error result: success"** (see memory `agent-sdk-env-nesting`). For a **full
   live** run:
   ```bash
   set -a; . ./.env; set +a
   env -u CLAUDECODE -u CLAUDE_CODE_SSE_PORT -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION \
       -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH -u AI_AGENT -u CLAUDE_EFFORT -u ANTHROPIC_API_KEY \
     MODEL_PROVIDER=vercel USE_AGENT_BRIEF=1 USE_AGENT_COUNCIL=1 USE_AGENT_PRD_AUTHOR=1 \
       USE_AGENT_PRD_REVISE=1 USE_AGENT_ARCH_REVIEW=1 USE_AGENT_RESEARCH=1 USE_AGENT_STORY_PLAN=1 \
       USE_AGENT_BUG_PRIORITY=1 USE_AGENT_TRIAGE=1 USE_AGENT_REVIEW=1 USE_AGENT_QA=1 \
       USE_AGENT_CODING=1 CODING_AGENT=claude CODING_SANDBOX=container CODING_PR_TARGET=github \
       CODING_PERMISSION_MODE=bypassPermissions \
     ./.venv/bin/python -m worker.main > .localdata/worker.log 2>&1
   ```
   Run with `run_in_background`. For **cheap** mode, drop `USE_AGENT_CODING …` and set
   `CODING_PR_TARGET=local`; for **$0 stub**, drop all `USE_AGENT_*` flags.
   - `CODING_PR_TARGET=local` = no-push dry run; the coding cost caps live in `config.py`
     (`CODING_MAX_TURNS`/`CODING_MAX_BUDGET_USD`) — don't lower them so far the agent can't
     finish (see memory `coding-pod-cost-cap`).
3. Wait until `.localdata/worker.log` shows the worker connected to task queue `agentic-org`
   before driving. Use Monitor with an until-condition rather than a fixed sleep.

## Step 2 — Drive the run and babysit

Start the driver in the background, capturing output; it prints `▶ started <workflow-id> …`
then streams each stage and auto-approves gates until a terminal state:
```bash
./.venv/bin/python -u -m cli.run --project <project> [--bug] --title "<title>" \
  > .localdata/run.log 2>&1
```
**Capture the `<workflow-id>`** from the first line — Step 3 needs it.

While it runs (a full coding run can take ~10–15 min), **babysit both logs** (`.localdata/run.log`
and `.localdata/worker.log`). Watch for and react to:
- `"error result: success"` → env nesting; a worker env strip was missed. Stop, fix the worker
  launch, restart.
- A reasoning/coding **timeout** or repeated transient query failures → note it; the driver is
  resilient to transient query races, but a hung stage means investigate the worker log.
- A **budget gate** trip (`budget_gate…`) → `cli.run` auto-approves it, but flag that the run
  exceeded the per-workflow ceiling (it's a real cost signal, see §10 of CLAUDE.md).
- A **failed story** / partial diff / soft-stop (turn or budget cap hit) → the run continues
  with a partial result; record it for the report. A coding error returns a *failed story*, it
  must not raise — if you see a raise/retry storm, that's a regression worth flagging.
- If a step fails 2–3× the same way, **stop and report** to the user rather than looping.

Do not lower the coding caps or strip safety to force a green run; report honestly if it fell short.

## Step 3 — Per-stage summary + write the audit folder

Once the workflow reaches a terminal state, decode + persist the reasoning trace **and write
the committed audit folder** in one call:
```bash
./.venv/bin/python -m cli.trace <workflow-id> --project <project> \
  --save .localdata/artifacts.db --audit runs
```
This walks the parent + `-research-0` + `-pod` children and prints each activity's output;
the full coding diff is written to `/tmp/steelthread-<workflow-id>.diff`; and `--audit runs`
writes `runs/<project>/<YYYY-MM-DD>-<workflow-id>/` with `report.md` (outcome, council votes,
PRD↔architect iterations, research, stories, pod, cost), `prd.md`, `trace.json`, and
`coding.diff`. **Note the run-dir path it prints** — Step 4 commits it. From the trace, give
the user a tight **stage-by-stage summary**:
- **Brief** (problem, ui_impacting), **Council** (each voter + rationale), **PRD** (version,
  gist) + **architect review** passes (approved?, concerns), **Research** (sentiment, notes),
  **Story plan** (complexity, story count + titles), **Pod** (story statuses, cost, QA pass),
  **PR/deploy** (URL, opened?).
- The **final result** line from `run.log`: status, total `$cost`, summary.
Lead with the outcome (shipped/PR URL, or where it stalled), then the per-stage detail.

## Step 4 — Publish the audit PR (this repo)

On **every** terminal run, commit the audit folder from Step 3 and open a PR **against this
repo** (`agentic-engineering`) — the org's own record of the run. This is separate from the
product PR the org opened on the target app. Run these from the repo root:

1. Branch off current HEAD (never commit on `main`); use a short workflow id (last segment):
   ```bash
   git checkout -b "org-run/<project>/$(date +%Y-%m-%d)-<short-wfid>"
   ```
2. Stage **only** the new run dir so unrelated working-tree changes aren't swept in:
   ```bash
   git add runs/<project>/<run-dir>          # the path Step 3 printed
   ```
   (First run only: also `git add runs/README.md` if it isn't tracked yet.)
3. Commit with the repo's trailer:
   ```bash
   git commit -m "org run: <title> (<project>) — <status>" \
     -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
   ```
4. Push and open the PR; the body summarizes outcome, council votes, PRD/architect
   iterations, story/pod results, and cost, and **links the product PR** (the `open_pr.url`
   from the trace) when there is one:
   ```bash
   git push -u origin "$(git branch --show-current)"
   gh pr create --title "Org run: <title> (<project>)" --body "<summary + product PR link>"
   ```
5. Switch back to the original branch so the working state is restored.

If `gh` or the remote is unavailable, stop after the push (or after the commit) and report
the branch name — never fabricate a PR URL. Tell the user the audit PR URL alongside the
product PR URL in the final report.

## Step 5 — Optimization report

After the summary, scan the trace for optimization opportunities in **three areas** (skip
latency unless asked):

- **Cost** — per-stage $ from the trace + the final total. Flag: a stage on a higher tier than
  it needs (Opus where Sonnet would do — note the `complexity` downgrade exists), **architect
  over-decomposition** (story count vs. complexity ceiling 3/6/10), redundant PRD↔architect
  passes, research fan-out width. Name the **single biggest spend lever** first; the pod
  dominates a feature's cost.
- **Quality / prompts** — stage outputs that look weak: a partial/failed-story diff, missed
  stories (feature shipped without its UI control), a PRD the architect rejected repeatedly, a
  persona that ignored or echoed injected text. Point to the persona/prompt (`agents/registry/`,
  coding prompt in `coding/claude_sdk.py`) and suggest a concrete tweak.
- **Orchestration shape** — loop bounds that were hit (could the cap or the prompt change to
  avoid the extra pass?), gates that added no value, stages that could be skipped for this
  feature class (e.g. UX mocks when `ui_impacting` is false), anything that could parallelize.

Keep it actionable: each finding = what you observed in *this* run + the file to change + the
expected effect (cheaper / better diff / fewer passes). Don't invent problems; if the run was
clean and cheap, say so and note the one thing you'd watch next time.

## Invariants — do not violate (CLAUDE.md §9)

- This skill only *drives* the org; it never edits workflow code to force a result.
- Deploys/PRs stay behind the human gate the workflow already models — don't bypass them.
- Secrets come from `.env`/the secret store only; never echo `ANTHROPIC_API_KEY`,
  `AI_GATEWAY_API_KEY`, or other credentials into chat or logs. The audit folder/PR (Steps
  3–4) holds persona outputs (brief, votes, PRD, diff) — not env — but it gets committed to
  this repo, so glance at `report.md`/`trace.json` and don't commit anything that looks like a
  credential.
- The audit PR is the org's own record (it does not gate the product deploy); the workflow's
  human gates still own the actual ship.
- Clean up: the dev server + worker you started can keep running for follow-up runs, but tell
  the user they're up (and how to stop them — killing the bg processes exits 144 = normal) so
  they're not left burning the subscription window.
