#!/usr/bin/env bash
# Nightly backup of BotSpotter data on the VM: Redis (RDB snapshot) + Postgres dump.
# Keeps 7 days under ~/botspotter-backups. Installed into cron by deploy/remote.sh.
# Restore Redis:    docker compose stop aggregator api; docker compose cp <file> redis:/data/dump.rdb; docker compose restart redis; start aggregator api
# Restore Postgres: gunzip -c <file> | docker compose exec -T postgres psql -U botspotter botspotter
set -euo pipefail
APP_DIR="${APP_DIR:-$HOME/botspotter}"
OUT="${OUT:-$HOME/botspotter-backups}"
mkdir -p "$OUT"
cd "$APP_DIR"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.vm.yml"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
$COMPOSE exec -T redis redis-cli BGSAVE >/dev/null
sleep 5
$COMPOSE cp redis:/data/dump.rdb "$OUT/redis-$STAMP.rdb"
$COMPOSE exec -T postgres pg_dump -U botspotter botspotter | gzip > "$OUT/pg-$STAMP.sql.gz"
find "$OUT" -type f -mtime +7 -delete
echo "backup ok: $(ls -1 "$OUT" | wc -l) files in $OUT"
