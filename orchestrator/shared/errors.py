"""Exception types whose names appear in the activity retry policy (workflows/common.py).

Temporal matches non-retryable errors by *type name*, so these names are load-bearing:
`AuthError` and `NonRetryableAgentError` are listed in DEFAULT_RETRY.non_retryable_error_types.
"""


class AuthError(Exception):
    """Authentication/authorization failure — never retried (a retry won't fix bad creds)."""


class NonRetryableAgentError(Exception):
    """The agent gave up deterministically (e.g. malformed output after bounded re-asks).

    Distinct from a transient parse hiccup, which IS retried. Raising this stops retries."""
