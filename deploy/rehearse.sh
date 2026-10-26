#!/usr/bin/env bash
# Deploy rehearsal: bring the REAL stack up from scratch on a throwaway machine (CI)
# and run the same checks remote.sh runs on the VM — health, migrations, end-to-end
# smoke through Redpanda, snapshot freshness, aggregator alive — so a bad change turns
# red on the PR instead of on botspotter.app. Also validates the Caddy site block.
#   bash deploy/rehearse.sh            (needs docker compose; ~2 min)
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=bsrehearse
COMPOSE="docker compose -f docker-compose.yml"
# A developer's real .env is set aside for the run and restored afterwards (CI has none).
[ -f .env ] && mv .env .env.pre-rehearse
cleanup() {
  $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true   # gate:allow throwaway project "bsrehearse" in CI, never production volumes
  rm -f .env .env.rehearse; [ -f .env.pre-rehearse ] && mv .env.pre-rehearse .env; true; }
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }
step "env"
cp .env.example .env.rehearse
sed -i "s|^BS_SENSOR_KEYS=.*|BS_SENSOR_KEYS=rehearsekey:rehearse-site|" .env.rehearse
echo "BS_ADMIN_KEY=rehearse-admin" >> .env.rehearse
cp .env.rehearse .env

step "caddy site block parses"
docker run --rm -v "$PWD/deploy/Caddyfile.botspotter:/etc/caddy/Caddyfile:ro" caddy:2 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile

step "build + start (fresh volumes: schema.sql + migrations.sql both run)"
$COMPOSE up -d --build --scale edge=0

step "health"
for i in $(seq 1 60); do
  if $COMPOSE exec -T api python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/healthz',timeout=2).read().decode())" 2>/dev/null | grep -q '"ok"'; then echo "api healthy"; break; fi
  sleep 3; [ "$i" = 60 ] && { echo "api not healthy"; $COMPOSE logs --tail 60 api; exit 1; }
done

step "smoke: bot hit + human beacon through the real pipeline"
$COMPOSE exec -T ingest python -c "
import urllib.request, json
req=urllib.request.Request('http://localhost:8001/v1/ingest', data=json.dumps({'events':[{'ua':'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)','headers':['host','user-agent','accept','accept-encoding'],'ip':'20.15.241.9','cf_country':'US','path':'/'}]}).encode(),method='POST',headers={'content-type':'application/json','x-bs-key':'rehearsekey'})
print('ingest', urllib.request.urlopen(req,timeout=5).status)
req=urllib.request.Request('http://localhost:8001/v1/beacon',data=b'{}',method='POST',headers={'content-type':'application/json','user-agent':'Mozilla/5.0 (Macintosh) Chrome/129 Safari/537.36','x-forwarded-for':'203.0.113.9','cf-ipcountry':'US','cf-ipcity':'Folsom'})
print('beacon', urllib.request.urlopen(req,timeout=5).status)"
sleep 6
$COMPOSE exec -T api python -c "
import urllib.request, urllib.error, json, time, sys
s=json.load(urllib.request.urlopen('http://localhost:8000/v1/snapshot'))
age=time.time()-s['ts']; print('snapshot age', round(age,1), 'bot hits', s['bots']['total_hits'], 'views', s['humans']['views'])
assert age < 10, 'snapshot stale: aggregator not writing'
assert s['bots']['total_hits'] >= 1 and s['humans']['views'] >= 1, 'pipeline did not count the smoke hits'
req=urllib.request.Request('http://localhost:8000/v1/admin/visitors', headers={'x-bs-admin':'rehearse-admin'})
led=json.load(urllib.request.urlopen(req)); assert led['summary']['views'] >= 1, 'ledger empty: migrations/visits broken'
print('ledger ok', led['summary']['views'], 'view(s)')
sites=json.load(urllib.request.urlopen('http://localhost:8000/v1/sites')); assert sites['sites'], 'no sensor sites'
me=json.load(urllib.request.urlopen('http://localhost:8000/v1/me')); assert me['user'] is None and 'oauth_enabled' in me, 'enrollment endpoint broken'
try:
    urllib.request.urlopen('http://localhost:8000/v1/sites/mine'); raise SystemExit('enrollment must require sign-in')
except urllib.error.HTTPError as e:
    assert e.code == 401, e.code
print('rehearsal OK')"
STATE=$($COMPOSE ps --format '{{.Service}} {{.Status}}' | awk '$1=="aggregator"{print $2}')
[ "$STATE" = "Up" ] || { echo "aggregator is $STATE"; $COMPOSE logs --tail 60 aggregator; exit 1; }
$COMPOSE logs aggregator 2>&1 | grep -i "migrations.sql failed" && { echo "!! migration failed (non-fatal in prod, fatal here)"; exit 1; } || true
echo; echo "ALL GOOD — this change would deploy cleanly"
