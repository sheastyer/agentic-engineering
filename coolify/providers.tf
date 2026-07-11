# Auth comes from the environment so no secret lives in this repo:
#   export COOLIFY_ENDPOINT="http://192.168.1.60:8000"
#   export COOLIFY_TOKEN="$(... from the homelab secrets/homelab.sops.yaml ...)"
# See README.md. The provider reads COOLIFY_ENDPOINT / COOLIFY_TOKEN directly.
provider "coolify" {}
