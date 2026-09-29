#!/usr/bin/env bash
set -euo pipefail

deployment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$deployment_dir"

docker compose run --rm --no-deps certbot renew --quiet
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile
