#!/bin/sh
# Shared entrypoint for the org's deployed processes (worker / slack-listener / intake).
# Wires git + gh auth for the coding plane, then execs the service command. Idempotent
# and safe for processes that never touch git (listener/intake): it only writes config.
set -e

# The pod clones targets and pushes PR branches over https with GH_TOKEN. Project
# Profiles may pin ssh remotes (git@github.com:...) — rewrite them to token'd https so
# the container needs no ssh keys, and let gh install its credential helper for plain
# https remotes.
if [ -n "${GH_TOKEN:-}" ]; then
    # --replace-all first, --add second: idempotent across container RESTARTS (same
    # filesystem, entrypoint re-runs — a plain `git config` on an existing multi-value
    # key fails and crash-loops the service; found by the compose smoke test).
    git config --global --replace-all "url.https://x-access-token:${GH_TOKEN}@github.com/.insteadOf" "git@github.com:"
    git config --global --add "url.https://x-access-token:${GH_TOKEN}@github.com/.insteadOf" "ssh://git@github.com/"
    gh auth setup-git >/dev/null 2>&1 || true
fi

# The pod lead checkpoint-commits accepted stories — commits need an identity.
git config --global user.name  "${GIT_AUTHOR_NAME:-agentic-org}"
git config --global user.email "${GIT_AUTHOR_EMAIL:-agentic-org@users.noreply.github.com}"

exec "$@"
