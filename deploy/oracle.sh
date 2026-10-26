#!/usr/bin/env bash
# One-shot deploy/upgrade of BotSpotter on the shared Oracle A1 VM, run FROM YOUR MAC.
#
#   deploy/oracle.sh                      # uses the defaults below
#   VM=ubuntu@1.2.3.4 KEY=~/.ssh/other deploy/oracle.sh
#
# Idempotent: safe to re-run for every upgrade (pulls main, rebuilds, restarts).
# Never deletes anything; Caddyfile is backed up before the one-line import is added.
set -euo pipefail
VM="${VM:-ubuntu@129.213.153.181}"
KEY="${KEY:-$HOME/.ssh/sharpen_vm}"
REPO="${REPO:-https://github.com/bhushanladde02/botspotter.git}"
CADDY_CONTAINER="${CADDY_CONTAINER:-deploy-caddy-1}"

ssh -i "$KEY" "$VM" bash -s <<REMOTE
set -euo pipefail
echo "== 1/6 code"
if [ -d ~/botspotter/.git ]; then git -C ~/botspotter pull --ff-only; else git clone "$REPO" ~/botspotter; fi
cd ~/botspotter

echo "== 2/6 secrets (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^PG_PASSWORD=.*|PG_PASSWORD=\$(openssl rand -hex 24)|" .env
  sed -i "s|^BS_NAMING_SALT=.*|BS_NAMING_SALT=\$(openssl rand -hex 24)|" .env
  sed -i "s|^BS_VISITOR_SECRET=.*|BS_VISITOR_SECRET=\$(openssl rand -hex 24)|" .env
  sed -i "s|^BS_SENSOR_KEYS=.*|BS_SENSOR_KEYS=\$(openssl rand -hex 16):bhushan-profile,\$(openssl rand -hex 16):recovery-routes,\$(openssl rand -hex 16):sharpen|" .env
  echo "generated new .env (keep it; the naming salt must never change)"
else
  echo ".env exists, keeping it"
fi
NET=\$(docker inspect "$CADDY_CONTAINER" -f '{{range \$k,\$v := .NetworkSettings.Networks}}{{\$k}}{{end}}' | head -1)
grep -q '^CADDY_NETWORK=' .env && sed -i "s|^CADDY_NETWORK=.*|CADDY_NETWORK=\$NET|" .env || echo "CADDY_NETWORK=\$NET" >> .env
echo "caddy network: \$NET"

echo "== 3/6 build + start (first build on A1 takes ~5 min)"
docker compose -f docker-compose.yml -f docker-compose.vm.yml up -d --build --remove-orphans

echo "== 4/6 wait for health"
for i in \$(seq 1 60); do
  if docker compose exec -T api python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/healthz',timeout=2).read().decode())" 2>/dev/null | grep -q '"ok"'; then echo "api healthy"; break; fi
  sleep 3; [ \$i = 60 ] && { echo "api not healthy; docker compose logs api"; exit 1; }
done
docker compose ps --format 'table {{.Name}}\t{{.Status}}'

echo "== 5/6 caddy site block"
SRC=\$(docker inspect "$CADDY_CONTAINER" -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}')
if [ -z "\$SRC" ]; then SRC=\$(docker inspect "$CADDY_CONTAINER" -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy"}}{{.Source}}{{end}}{{end}}')/Caddyfile; fi
echo "Caddyfile: \$SRC"
CDIR=\$(dirname "\$SRC")
if ! grep -q 'botspotter.app' "\$SRC"; then
  cp "\$SRC" "\$SRC.bak.\$(date +%Y%m%d%H%M%S)"
  cp ~/botspotter/deploy/Caddyfile.botspotter "\$CDIR/Caddyfile.botspotter"
  printf '\n# BotSpotter (added by botspotter/deploy/oracle.sh)\nimport /etc/caddy/Caddyfile.botspotter\n' >> "\$SRC"
  echo "import line appended (backup kept next to it)"
else
  cp ~/botspotter/deploy/Caddyfile.botspotter "\$CDIR/Caddyfile.botspotter"
  echo "site block refreshed"
fi
docker exec "$CADDY_CONTAINER" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
echo "caddy reloaded"

echo "== 6/6 sensor keys (give these to the matching sites; the portal needs none)"
grep '^BS_SENSOR_KEYS=' .env | tr ',' '\n' | sed 's/^BS_SENSOR_KEYS=//'
REMOTE
echo
echo "Done. Next: Cloudflare DNS A @ -> \${VM#*@} (proxied), SSL mode Full; then open https://botspotter.app"
