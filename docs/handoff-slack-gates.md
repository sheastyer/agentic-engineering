# Handoff: Slack human gates (M5, decision D1)

> **For a fresh agent picking this up cold.** Goal: replace the demo driver's
> auto-approval with **real human gates over Slack** — the org posts to a channel when a
> workflow hits a gate, and a human's button click becomes the Temporal signal that
> resumes it. This is one half of M5 (the other half, real intake, comes after). Read
> `CLAUDE.md` (architecture + hard invariants §9) and `PLAN.md` → "Current state" first;
> **the code is the source of truth** — verify anything here against it before building.

## Where the org stands (2026-07-02)

The steel thread is real and validated on both paths: feedback → reasoning chain (Vercel
gateway, `ORG_LIVE=1`) → engineering pod (Claude subscription, `USE_AGENT_CODING=1`) →
QA agent → reviewed PR → real CI → gated merge (meal-planner PRs #49 feature / #50 bug).
The **only fake part left is the human**: `cli.run`'s `drive_feature`/`drive_bug` polls
workflow state and auto-signals every gate. Your job is to make those signals come from
Slack instead, without touching the workflows' gate semantics.

## The gates (all already modeled as signals with timeouts — §9.4)

| Workflow | Signal | Gate | Timeout behavior |
|---|---|---|---|
| `FeatureRequestWorkflow` | `submit_human_vote(approve, voter)` | exec council (stage `exec_council`) | 72h → agent advisory majority |
| `FeatureRequestWorkflow` | `submit_pm_signoff(decision)` (`"approve"`/`"revise"`) | PM sign-off (stage `pm_signoff`) | 7d → treated as revise |
| `FeatureRequestWorkflow` / `BugWorkflow` | `submit_deploy_approval(approve)` | deploy/merge (stage `deploy_approval`) | 7d → `ESCALATED` |
| `FeatureRequestWorkflow` / `BugWorkflow` | `submit_budget_decision(approve)` | budget override (stage `budget_gate (...)`) | 7d → `OVER_BUDGET` halt |
| `BugWorkflow` | `submit_user_clarification(text)` | reporter clarification (stage `await_clarification`) | 7d → proceed |

Both workflows expose `get_state()` (queryable: stage, status, cost, log) — that's what
`cli/run.py` polls today and what your notifier can use for message context. The deploy
gate should show the human the PR URL + QA/review/CI verdicts (`PodResult` carries
`pr_url`, `qa`, `review_notes`, `ci_url` — but note the parent workflow currently keeps
`PodResult` internal to `_execute`; surfacing what you need into `get_state`/the notify
call is part of this work).

## Recommended design

**Outbound — a `notify_gate` activity.** A new activity (thin, blocking Slack Web API
`chat.postMessage` with Block Kit buttons) called by the workflow right before each
`workflow.wait_condition(...)`. Message = gate name, workflow id, feedback title, cost so
far, gate-specific context (council: brief summary + agent votes; deploy: PR URL + QA/CI
verdicts). Button `action_id`/`value` encodes `{workflow_id, gate, decision}`.
- **This changes workflow shape → R6 applies.** Use `workflow.patched()` or drain
  in-flight executions (local dev server: draining is fine — say so in the PR).
- Stub it like every other activity (`stubs.py` no-op + a live twin behind an env flag,
  e.g. `ORG_SLACK=1`), so `$0` stub runs and tests never post to Slack.
- Make the live twin a **sync `def`** — it's blocking HTTP. See the module docstring in
  `orchestrator/activities/agent_backed.py`: async activities block the worker's event
  loop; sync ones run in the worker's `ThreadPoolExecutor` (`worker/main.py`).

**Inbound — Socket Mode listener → Temporal signal.** A new small entrypoint (suggest
`orchestrator/humanio/slack_listener.py` + `python -m humanio` or similar) using
`slack_sdk`'s Socket Mode client. **Socket Mode, not an HTTP webhook** — this runs on a
laptop/homelab with no public ingress, and Socket Mode needs none. On a button
interaction: verify it's our app (Socket Mode implies an app-level token; still check the
payload), map `{workflow_id, gate, decision}` → `client.get_workflow_handle(wf_id).signal(...)`,
then update the Slack message ("✅ approved by @shea") so the channel shows the decision.
This is client-side glue exactly like `orchestrator/intake.py` — **zero workflow code**.

**Identity (M5 SEC requirement).** Approvals must carry who approved. `submit_human_vote`
already takes `voter`; the deploy/budget/signoff signals don't — extend them with a
defaulted `approver: str = "unknown"` arg (additive, replay-safe) and record it in the
stage log. Gate *authorization*: keep a `SLACK_APPROVER_IDS` allowlist (env) and ignore
clicks from anyone else — an inbound path must not be able to forge a gate signal (§ M5
SEC).

**Config (references only, §9.3):** `SLACK_BOT_TOKEN` (xoxb-, scopes: `chat:write`),
`SLACK_APP_TOKEN` (xapp-, Socket Mode), `SLACK_CHANNEL_ID`, `SLACK_APPROVER_IDS`. Add to
`.env.example` as commented placeholders; never commit values. `slack_sdk` goes in a
`[slack]` extra or core dep — your call, justify it.

**`cli.run` keeps auto-approve** as the test/demo mode. Suggested interplay: when
`ORG_SLACK=1` the driver should NOT auto-signal (it would race the human) — add a
`--auto-gates` flag or key off the env.

## Suggested implementation order

1. Signal identity args + surfacing gate context into `get_state` (pure workflow change,
   R6-safe/additive; tests).
2. `notify_gate` stub + workflow call sites (R6: `patched()` or drain; replay tests must
   stay green — `tests/test_replay.py`).
3. Live Slack notifier (sync activity, `ORG_SLACK=1`) + Socket Mode listener with
   allowlist + signal mapping. `$0` DET tests with a fake Slack client both directions.
4. `MAN` round-trip: stub-mode workflow, real Slack — message posts, click approves,
   workflow proceeds, approver recorded.
5. Live steel-thread run with at least the deploy gate approved by a real human click
   (the run-org skill drives everything else; tell it not to auto-approve deploy).

## Exit gate (definition of done)

- All existing tests green (`./.venv/bin/python -m pytest -q`, 119 as of handoff) + new
  DET tests; replay (R2) green across parent + children with the notify activity in the
  histories.
- MAN round-trip above, with the approver's identity visible in the workflow's stage log
  and the audit report.
- No secrets in source (R4); a non-allowlisted Slack user's click does nothing.
- `PLAN.md` updated: D1 resolved → Slack (Socket Mode), M5 human-I/O half done.

## Gotchas this project has already paid for (don't re-learn them)

- **Blocking HTTP in an async activity stalls the whole worker** — sync `def` + the
  worker's thread pool. (Fixed 2026-07-02; test pins it.)
- **`(str, Enum)` fields decode as char lists** across the activity boundary (temporalio
  1.28) — use `StrEnum` for anything serialized; `tests/test_serialization.py` pins it.
- **Sonnet-tier structured personas on the Vercel gateway need 10k+ `max_tokens`** — the
  gateway force-enables extended thinking into the same budget; truncation looks like
  schema drift. (Not directly relevant to Slack, but don't copy old small budgets.)
- **Any persona/activity that runs after the coding pass must return a result, never
  raise** (§10) — a raise discards paid-for work. Advisory steps degrade to non-blocking;
  hard gates (QA) degrade to fail-safe (`passed=False`).
- Worker launch from inside a Claude Code session needs the `env -u CLAUDECODE …` strip
  (see the run-org skill / `.env.example`), and the worker **fails fast** if `ORG_LIVE=1`
  without `AI_GATEWAY_API_KEY` — mirror that pattern for `ORG_SLACK=1` without tokens.
- The repo's agent sessions **cannot merge PRs** (permission-gated) — open the PR and
  hand the merge to the human.

## Open decisions for the human (ask before building past step 2)

1. Slack workspace/channel to use; who's on the approver allowlist besides Shea.
2. One channel for all gates vs. per-gate channels (start with one).
3. Should council votes stay human-decisive-with-72h-fallback as-is? (Recommend yes —
   don't change governance while changing transport.)
