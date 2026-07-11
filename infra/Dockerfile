# ──────────────────────────────────────────────────────────────────────────────
# The org's process image — one image, three services (docker-compose.coolify.yml):
#   worker           python -m worker.main            (Temporal worker, both planes live)
#   slack-listener   python -m orchestrator.humanio   (Socket Mode: button click → signal)
#   intake           python -m orchestrator.intake_http (POST /feedback → workflow)
#
# It's deliberately fat: the coding plane shells out to the `claude` CLI (Node), opens
# PRs with `gh`, drives the D9 container sandbox + the preview stack through the host's
# Docker (socket-mounted docker CLI + compose plugin), and screenshots with Playwright
# Chromium. Only the worker uses most of this; sharing one image keeps the compose
# build simple and the layers cached across services.
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.14-slim

# System deps + third-party apt repos: Docker CLI/compose plugin (sandbox + preview via
# the mounted socket), gh (PR open/merge), Node 22 (runtime for the claude CLI).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl git gnupg; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc; \
    echo "deb [signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list; \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin gh nodejs; \
    rm -rf /var/lib/apt/lists/*

# The coding plane's CLI (the Agent SDK spawns `claude`; auth via CLAUDE_CODE_OAUTH_TOKEN).
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

WORKDIR /app
COPY pyproject.toml README.md ./
COPY orchestrator/ orchestrator/
COPY worker/ worker/
COPY cli/ cli/
RUN pip install --no-cache-dir ".[agent-sdk,slack,preview,intake]"

# Chromium + its system libraries for the post-QA screenshot capture ([preview]).
RUN python -m playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

COPY infra/entrypoint.sh /usr/local/bin/org-entrypoint
RUN chmod +x /usr/local/bin/org-entrypoint

ENTRYPOINT ["org-entrypoint"]
CMD ["python", "-m", "worker.main"]
