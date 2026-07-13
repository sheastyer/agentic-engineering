# coolify/ — Coolify resources as code

Manages this org's Coolify resources with the
[`coolify-terraform/coolify`](https://search.opentofu.org/provider/coolify-terraform/coolify)
OpenTofu provider, imported from the live instance so `tofu plan` is a **no-op**
("match what exists"). Same pattern as the meal-planner and gardening-assistant
`coolify/` stacks — see meal-planner's README for the full provider-quirks
write-up.

**Managed here:** the application (`coolify_application_github_app`,
docker-compose build pack → [`docker-compose.coolify.yml`](../docker-compose.coolify.yml),
push-to-deploy from `main` via the private GitHub App).

**NOT managed here:** environment variables (all secrets — `AI_GATEWAY_API_KEY`,
`SLACK_*`, `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN`, `INTAKE_TOKEN`, …) stay in the
Coolify UI; compose-service domains (`ui` → temporal.styer.dev, `intake` →
org-intake.styer.dev) are driven by Coolify + the homelab edge stack
(`services/registry.yaml` in that repo).

## Apply

Auth is read from the environment; the token lives in the homelab repo's SOPS
(`secrets/homelab.sops.yaml`, key `coolify_token`):

```bash
export COOLIFY_ENDPOINT="http://192.168.1.60:8000"          # base URL; the provider adds /api/v1
export COOLIFY_TOKEN="$(SOPS_AGE_KEY_FILE=$HOME/.config/sops/age/keys.txt \
  sops -d --extract '["coolify_token"]' ~/Projects/homelab/secrets/homelab.sops.yaml)"

tofu init
tofu plan     # expect: 1 to import, no changes
```

State is **local and gitignored**. Re-import on a fresh checkout with the
`import {}` block in `main.tf` (always the compound id form — a plain uuid
forces a destroy/recreate).

## Deployment runbook

The operational how-to (env vars, domains, host prerequisites, smoke test,
security model) is [`docs/COOLIFY.md`](../docs/COOLIFY.md).
