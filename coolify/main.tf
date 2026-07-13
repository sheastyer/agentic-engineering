# Coolify resources for the agentic org, managed as code (created via the Coolify
# API on 2026-07-11, then imported here so `tofu plan` is a no-op — "match what
# exists"; same pattern as meal-planner/gardening-assistant `coolify/`).
#
# Scope: the project + the compose application (push-to-deploy from `main` via the
# private GitHub App, docker-compose build pack → docker-compose.coolify.yml).
# Environment VARIABLES are intentionally NOT managed here — they hold secrets
# (AI_GATEWAY_API_KEY, SLACK_*, CLAUDE_CODE_OAUTH_TOKEN, GH_TOKEN, INTAKE_TOKEN)
# and stay in the Coolify UI, so nothing secret lands in this repo or in state.
#
# Discovered identifiers (Coolify 4.1.2, server `localhost`):
#   project  "Agentic Org"      k104sk57ns0nl2lsxx20ubv2  (env: production)
#   server   localhost          c45wjiiswkdtqmaj68uy01w1
#   GitHub App (private)        x1w5tagxn9sy2e95asatk048
#   application agentic-org     c8gl5dii5zswe6k6g71114y7
#
# Compose-service domains (set via the API, driven from Coolify, not managed here —
# see the docker_compose_domains ignore below):
#   ui     → http://temporal.styer.dev     (Temporal Web UI; Access-gated at the edge)
#   intake → http://org-intake.styer.dev   (POST /feedback; Access + path bypass)
# Both hostnames are routed by the homelab edge stack (services/registry.yaml there).

locals {
  project_uuid = "k104sk57ns0nl2lsxx20ubv2"
  server_uuid  = "c45wjiiswkdtqmaj68uy01w1"
}

# ── Application: git repo + private GitHub App, docker-compose build pack ──────
resource "coolify_application_github_app" "app" {
  project_uuid    = local.project_uuid
  server_uuid     = local.server_uuid
  github_app_uuid = "x1w5tagxn9sy2e95asatk048"

  git_repository = "https://github.com/sheastyer/agentic-engineering"
  build_pack     = "dockercompose"
  # Coolify-assigned default at create time; inert for a compose app (routing is
  # Traefik Host-header via the compose SERVICE_FQDN_*, not this port). Kept
  # as-is so plan stays a no-op against the live resource.
  ports_exposes  = "80"

  lifecycle {
    # Same provider/API quirks as the meal-planner stack (see its coolify/README.md):
    # `github_app_uuid` is rejected on update and not returned on read;
    # `docker_compose_domains` reads back as an object but the write API demands an
    # array (and it's hidden unless the token is root). Domains are driven by the
    # compose SERVICE_FQDN_* + Coolify, so we don't manage them here.
    ignore_changes = [github_app_uuid, docker_compose_domains]
  }
}

import {
  to = coolify_application_github_app.app
  # compound form auto-populates project/server/environment (avoids a spurious
  # replace on the RequiresReplace project_uuid/server_uuid fields)
  id = "k104sk57ns0nl2lsxx20ubv2:c45wjiiswkdtqmaj68uy01w1:production:c8gl5dii5zswe6k6g71114y7"
}
