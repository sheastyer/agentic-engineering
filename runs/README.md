# Org run audit trail

Durable, reviewable record of every org run, one folder per run, organized by target app:

```
runs/<project>/<YYYY-MM-DD>-<workflow-id>/
  report.md    # outcome, council votes, PRD↔architect iterations, research, stories, pod, cost
  prd.md       # the PM's PRD + revision history
  trace.json   # the full decoded trace (every stage's raw payload)
  coding.diff  # the engineering pod's diff (when the pod ran)
```

These folders are written by `cli.trace --audit` and published as an **audit PR against
this repo** at the end of each run — see `.claude/skills/run-org` (Step 4). The audit PR is
the org's internal record of *how* a change was decided and built; it is distinct from the
**product PR** the org opens on the target app, which `report.md` links to.

Regenerate (or write) a run's audit by hand:

```bash
./.venv/bin/python -m cli.trace <workflow-id> --project <project> --audit runs
```
