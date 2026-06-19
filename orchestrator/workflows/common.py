"""Shared workflow-side helpers.

Imported inside the Temporal sandbox, so this module must stay deterministic: no I/O,
no clocks, no randomness. It only builds the (deterministic) retry policy and the
activity-invocation helper every workflow uses.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

# Default activity execution knobs. Auth-type failures and explicit "give up" errors are
# non-retryable; transient and rate-limit errors retry with backoff (CLAUDE.md §9.7).
# Real activities (M3+) raise ApplicationError with one of these *type names* to opt out
# of retries. Note: we do NOT list ValueError here — a transient parse hiccup from a
# model response is exactly the case we want to re-ask/retry, so genuine give-ups must
# use the dedicated NonRetryableAgentError type, not the broad ValueError.
# Reasoning activities are a single structured LLM call. 30s was too tight for Opus
# authoring (PRD/architecture) with extended thinking over a gateway — it tripped
# StartToClose timeouts and wasteful retries. 180s is a realistic ceiling for one call;
# the engineering pod's coding activities override this with CODING_ACTIVITY_TIMEOUT.
ACTIVITY_TIMEOUT = timedelta(seconds=180)
DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
    # Auth, billing, and other 4xx client errors are permanent — retrying wastes calls.
    # (The Anthropic SDK raises these type names; a "credit balance too low" 400 is a
    # BadRequestError. Verified live, 2026-06-14.)
    non_retryable_error_types=[
        "AuthError",
        "NonRetryableAgentError",
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "NotFoundError",
    ],
)


async def run_activity(fn, *args, timeout: timedelta | None = None):
    """Execute an activity with the org's standard retry/timeout policy.

    Returns the activity's result unchanged. Cost accounting is handled by the caller
    (workflows accumulate ``result.cost_tokens``) so this stays a thin, generic wrapper.
    ``timeout`` overrides the default start-to-close (the engineering pod's coding
    activities need minutes, not the 30s reasoning default).
    """
    return await workflow.execute_activity(
        fn,
        args=list(args),
        start_to_close_timeout=timeout or ACTIVITY_TIMEOUT,
        retry_policy=DEFAULT_RETRY,
    )
