#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    printf 'Please run this script as root.\n' >&2
    exit 1
fi

if [[ "$#" -ne 1 ]]; then
    printf 'Usage: %s <public-ip-address>\n' "$0" >&2
    printf 'Example: %s 203.0.113.10\n' "$0" >&2
    exit 1
fi

relay_ip="$1"
if [[ ! "$relay_ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    printf 'Invalid IPv4 address: %s\n' "$relay_ip" >&2
    exit 1
fi

deployment_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$deployment_dir"

export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null 2>&1; then
    apt-get update
    apt-get install -y docker.io docker-compose-v2 ca-certificates curl
    systemctl enable --now docker
fi

if ! docker compose version >/dev/null 2>&1; then
    apt-get update
    apt-get install -y docker-compose-v2
fi

mkdir -p certbot/www certbot/conf
chmod 700 certbot/conf
cp Caddyfile.http Caddyfile.runtime

if [[ ! -f .env ]]; then
    umask 077
    RELAY_IP="$relay_ip" python3 <<'PY'
import json
import os
import secrets
from pathlib import Path

relay_ip = os.environ["RELAY_IP"]
master_token = secrets.token_urlsafe(48)
devices = {f"slave-{number:02d}": secrets.token_urlsafe(48) for number in range(1, 11)}

env_lines = [
    f"RELAY_IP_ADDRESS={relay_ip}",
    "HOYO_DATABASE_PATH=/data/hoyo-relay.sqlite3",
    "HOYO_RETENTION_DAYS=30",
    f"HOYO_MASTER_TOKEN={master_token}",
    "HOYO_DEVICE_TOKENS=" + json.dumps(devices, ensure_ascii=True, separators=(",", ":")),
]
Path(".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
Path("deployment-credentials.json").write_text(
    json.dumps(
        {
            "site_address": f"https://{relay_ip}",
            "master_token": master_token,
            "device_tokens": devices,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY
    chmod 600 .env deployment-credentials.json
else
    sed -i '/^RELAY_SITE_ADDRESS=/d' .env
    if grep -q '^RELAY_IP_ADDRESS=' .env; then
        sed -i "s#^RELAY_IP_ADDRESS=.*#RELAY_IP_ADDRESS=${relay_ip}#" .env
    else
        printf '\nRELAY_IP_ADDRESS=%s\n' "$relay_ip" >> .env
    fi
    RELAY_IP="$relay_ip" python3 <<'PY'
import json
import os
from pathlib import Path

path = Path("deployment-credentials.json")
if path.exists():
    credentials = json.loads(path.read_text(encoding="utf-8"))
    credentials["site_address"] = f"https://{os.environ['RELAY_IP']}"
    path.write_text(json.dumps(credentials, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
fi

docker compose up -d --build --remove-orphans relay caddy

certificate_path="certbot/conf/live/${relay_ip}/fullchain.pem"
if [[ ! -f "$certificate_path" ]]; then
    docker compose run --rm --no-deps certbot certonly \
        --non-interactive \
        --agree-tos \
        --register-unsafely-without-email \
        --preferred-profile shortlived \
        --webroot \
        --webroot-path /var/www/certbot \
        --ip-address "$relay_ip"
fi

cp Caddyfile.https Caddyfile.runtime
docker compose up -d --force-recreate caddy

chmod 700 renew-cert.sh
cat > /etc/systemd/system/hoyo-relay-cert-renew.service <<EOF
[Unit]
Description=Renew Hoyo relay public IP TLS certificate
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=${deployment_dir}
ExecStart=${deployment_dir}/renew-cert.sh
EOF

cat > /etc/systemd/system/hoyo-relay-cert-renew.timer <<'EOF'
[Unit]
Description=Daily renewal check for Hoyo relay TLS certificate

[Timer]
OnCalendar=*-*-* 03:17:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now hoyo-relay-cert-renew.timer

for _attempt in $(seq 1 30); do
    if curl --fail --silent --show-error --max-time 5 \
        --resolve "${relay_ip}:443:127.0.0.1" "https://${relay_ip}/healthz" >/dev/null; then
        printf 'Hoyo relay is healthy at https://%s\n' "$relay_ip"
        printf 'Certificate renewal timer is active.\n'
        printf 'Credentials are stored in %s/deployment-credentials.json\n' "$deployment_dir"
        exit 0
    fi
    sleep 2
done

docker compose ps
docker compose logs --tail=100 relay caddy
printf 'HTTPS deployment did not become healthy in time.\n' >&2
exit 1
