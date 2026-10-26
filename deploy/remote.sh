#!/usr/bin/env bash
# Runs ON THE VM. Called by deploy/oracle.sh (from a laptop) and by
# .github/workflows/deploy.yml (CI/CD). Idempotent; never deletes anything.
#
#   REPO=<git url> CADDY_CONTAINER=deploy-caddy-1 GIT_REF=main bash deploy/remote.sh
set -euo pipefail
REPO="${REPO:-https://github.com/bhushanladde02/botspotter.git}"
CADDY_CONTAINER="${CADDY_CONTAINER:-deploy-caddy-1}"
GIT_REF="${GIT_REF:-main}"
APP_DIR="${APP_DIR:-$HOME/botspotter}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.vm.yml"

step() { printf '\n== %s\n' "$*"; }

step "1/7 code ($GIT_REF)"
PREV_REV=""
if [ -d "$APP_DIR/.git" ]; then
  PREV_REV=$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null || true)
  git -C "$APP_DIR" fetch --quiet origin
  git -C "$APP_DIR" checkout --quiet "$GIT_REF"
  git -C "$APP_DIR" pull --ff-only --quiet origin "$GIT_REF" || true
else
  git clone --quiet --branch "$GIT_REF" "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"
echo "at $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"

step "2/7 secrets (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^PG_PASSWORD=.*|PG_PASSWORD=$(openssl rand -hex 24)|" .env
  sed -i "s|^BS_NAMING_SALT=.*|BS_NAMING_SALT=$(openssl rand -hex 24)|" .env
  sed -i "s|^BS_VISITOR_SECRET=.*|BS_VISITOR_SECRET=$(openssl rand -hex 24)|" .env
  sed -i "s|^BS_SENSOR_KEYS=.*|BS_SENSOR_KEYS=$(openssl rand -hex 16):bhushan-profile,$(openssl rand -hex 16):recovery-routes,$(openssl rand -hex 16):sharpen|" .env
  echo "generated new .env — back it up (the naming salt must never change)"
else
  echo ".env exists, keeping it"
fi
NET=$(docker inspect "$CADDY_CONTAINER" -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' | head -1)
# Added after the first deploy: generate once, keep forever (it is the owner's ledger key).
if ! grep -q '^BS_ADMIN_KEY=' .env; then
  echo "BS_ADMIN_KEY=$(openssl rand -hex 24)" >> .env
  echo "generated BS_ADMIN_KEY — copy it from .env into private/notes.md (it opens /admin)"
fi
if grep -q '^CADDY_NETWORK=' .env; then sed -i "s|^CADDY_NETWORK=.*|CADDY_NETWORK=$NET|" .env; else echo "CADDY_NETWORK=$NET" >> .env; fi
echo "caddy network: $NET"

# Any failure from here on (health, smoke) rebuilds the previous revision so a bad
# deploy costs minutes, not hours (2026-10-26: a migration bug left the site down
# until a manual checkout). Disable with ROLLBACK=0 when debugging on the box.
rollback() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ -n "$PREV_REV" ] && [ "${ROLLBACK:-1}" = 1 ] && [ "$(git rev-parse HEAD)" != "$PREV_REV" ]; then
    echo "!! deploy failed (exit $rc) — rolling back to ${PREV_REV:0:8}"
    git checkout --quiet "$PREV_REV"
    $COMPOSE up -d --build --remove-orphans || true
    echo "rolled back; fix forward and push again"
  fi
  exit "$rc"
}
trap rollback EXIT

step "3/7 build + start"
$COMPOSE up -d --build --remove-orphans

step "4/7 wait for health"
for i in $(seq 1 60); do
  if $COMPOSE exec -T api python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/healthz',timeout=2).read().decode())" 2>/dev/null | grep -q '"ok"'; then
    echo "api healthy"; break
  fi
  sleep 3
  if [ "$i" = 60 ]; then echo "api not healthy"; $COMPOSE logs --tail 50 api; exit 1; fi
done
$COMPOSE ps --format 'table {{.Name}}\t{{.Status}}'

step "5/7 caddy site block"
# Sharpen's Caddy mounts its config dir at /etc/caddy and a drop-in dir at /etc/caddy/extra.
CADDY_DIR=$(docker inspect "$CADDY_CONTAINER" -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy"}}{{.Source}}{{end}}{{end}}')
EXTRA_DIR=$(docker inspect "$CADDY_CONTAINER" -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy/extra"}}{{.Source}}{{end}}{{end}}')
if [ -z "$EXTRA_DIR" ]; then EXTRA_DIR="$CADDY_DIR/extra"; mkdir -p "$EXTRA_DIR"; fi
echo "caddy dir: $CADDY_DIR  drop-ins: $EXTRA_DIR"
if ! grep -qE 'import +extra' "$CADDY_DIR/Caddyfile"; then
  cp "$CADDY_DIR/Caddyfile" "$CADDY_DIR/Caddyfile.bak.$(date +%Y%m%d%H%M%S)"
  printf '\n# drop-in sites (added by botspotter/deploy/remote.sh)\nimport extra/*\n' >> "$CADDY_DIR/Caddyfile"
  echo "added 'import extra/*' to Caddyfile (backup kept)"
fi
if ! cmp -s deploy/Caddyfile.botspotter "$EXTRA_DIR/botspotter.caddy"; then
  cp deploy/Caddyfile.botspotter "$EXTRA_DIR/botspotter.caddy"
  docker exec "$CADDY_CONTAINER" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
  docker exec "$CADDY_CONTAINER" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  echo "site block installed + caddy reloaded"
else
  echo "site block unchanged"
fi

step "6/7 smoke test (end to end through the real pipeline)"
BEFORE=$($COMPOSE exec -T api python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://localhost:8000/v1/snapshot'))['humans']['views'])")
$COMPOSE exec -T ingest python -c "
import urllib.request;req=urllib.request.Request('http://localhost:8001/v1/beacon',data=b'{}',method='POST',headers={'content-type':'application/json','user-agent':'botspotter-smoke','x-forwarded-for':'203.0.113.9'})
print('beacon', urllib.request.urlopen(req,timeout=5).status)"
sleep 4
AFTER=$($COMPOSE exec -T api python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://localhost:8000/v1/snapshot'))['humans']['views'])")
echo "views before=$BEFORE after=$AFTER"
if [ "$AFTER" -le "$BEFORE" ]; then
  echo "!! aggregator is not consuming — last 40 log lines:"
  $COMPOSE logs --tail 40 aggregator
  $COMPOSE exec -T redpanda rpk group describe aggregator || true
  exit 1
fi
# The aggregator must also still be alive and writing snapshots a little later: on
# 2026-10-26 it counted the smoke hit, then died in write_snapshot() on every loop
# (missing package data) and the site showed a frozen snapshot for 10 hours.
sleep 6
AGE=$($COMPOSE exec -T api python -c "import urllib.request,json,time;print(int(time.time()-json.load(urllib.request.urlopen('http://localhost:8000/v1/snapshot'))['ts']))")
STATE=$($COMPOSE ps --format '{{.Service}} {{.Status}}' | awk '$1=="aggregator"{print $2}')
echo "snapshot age=${AGE}s aggregator=$STATE"
if [ "$AGE" -gt 10 ] || [ "$STATE" != "Up" ]; then
  echo "!! aggregator is not writing snapshots — last 40 log lines:"
  $COMPOSE logs --tail 40 aggregator
  exit 1
fi
echo "pipeline OK (ingest -> redpanda -> aggregator -> redis -> api)"

step "6b/7 nightly backup cron"
chmod +x deploy/backup.sh
CRON_LINE="17 9 * * * $APP_DIR/deploy/backup.sh >> $HOME/botspotter-backups/backup.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'botspotter/deploy/backup.sh'; echo "$CRON_LINE" ) | crontab -
echo "cron: $CRON_LINE"

trap - EXIT
step "7/7 done"
echo "sensor keys (site owners):"; grep '^BS_SENSOR_KEYS=' .env | sed 's/^BS_SENSOR_KEYS=//' | tr ',' '\n' | sed 's/^/  /'
