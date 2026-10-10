#!/usr/bin/env bash
set -euo pipefail

deployment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$deployment_dir"

docker compose run --rm --no-deps certbot renew --quiet
# IP certificates are short-lived. Caddy skips an identical configuration by
# default, which leaves the previously loaded certificate in memory even after
# Certbot replaces the PEM files. Force reprovisioning so the renewed
# certificate is served immediately.
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --force
