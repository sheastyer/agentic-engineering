"""Human I/O — the real channel behind the workflows' human gates (M5, D1: Slack).

Two halves, mirroring the notify/signal split in the workflows:
- outbound: ``notify.notify_gate_slack`` — the live twin of the ``notify_gate`` stub
  (swapped in by ``ORG_SLACK=1`` on the worker); posts each gate to a Slack channel
  with approve/reject buttons.
- inbound: ``slack_listener`` — a Socket Mode listener (``python -m
  orchestrator.humanio``) that turns an allowlisted human's button click into the
  matching Temporal signal. Client-side glue like ``orchestrator/intake.py`` — zero
  workflow code.

``gates.py`` is the single source of truth both halves share: which buttons each gate
shows and which workflow signal each click maps to.
"""
