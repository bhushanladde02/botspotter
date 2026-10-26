#!/usr/bin/env bash
# Refresh runtime data on the VM (weekly cron + on every deploy). Nothing here is in git.
#   MAXMIND_ACCOUNT_ID=... MAXMIND_LICENSE_KEY=... bash deploy/refresh-data.sh
# Writes to $DATA_DIR (default ~/botspotter-data), mounted read-only at /data in the
# containers (docker-compose.vm.yml):
#   GeoLite2-City.mmdb, GeoLite2-ASN.mmdb  (MaxMind GeoLite2, free account; EULA: no redistribution)
#   crawler_ranges.json                     (operators' published crawler IP ranges, jobs/crawler_ranges.py)
# Ingest re-reads these at start, so the script recreates it when something changed.
set -euo pipefail
APP_DIR="${APP_DIR:-$HOME/botspotter}"
DATA_DIR="${DATA_DIR:-$HOME/botspotter-data}"
mkdir -p "$DATA_DIR"
changed=0

if [ -n "${MAXMIND_LICENSE_KEY:-}" ] && [ -n "${MAXMIND_ACCOUNT_ID:-}" ]; then
  for ed in GeoLite2-City GeoLite2-ASN; do
    tmp=$(mktemp -d)
    if curl -fsSL -u "$MAXMIND_ACCOUNT_ID:$MAXMIND_LICENSE_KEY" \
         "https://download.maxmind.com/geoip/databases/$ed/download?suffix=tar.gz" -o "$tmp/$ed.tgz"; then
      tar -xzf "$tmp/$ed.tgz" -C "$tmp"
      f=$(find "$tmp" -name "$ed.mmdb" | head -1)
      if [ -n "$f" ] && ! cmp -s "$f" "$DATA_DIR/$ed.mmdb"; then cp "$f" "$DATA_DIR/$ed.mmdb"; changed=1; echo "$ed: updated"; else echo "$ed: unchanged"; fi
    else
      echo "$ed: download failed (check MAXMIND_* secrets); keeping existing file"
    fi
    rm -rf "$tmp"
  done
else
  echo "MAXMIND_ACCOUNT_ID/MAXMIND_LICENSE_KEY not set: GeoIP stays at country level"
fi

before=$(md5sum "$DATA_DIR/crawler_ranges.json" 2>/dev/null | cut -d' ' -f1 || true)
python3 "$APP_DIR/jobs/crawler_ranges.py" "$DATA_DIR/crawler_ranges.json" || echo "crawler ranges: refresh failed; keeping existing"
after=$(md5sum "$DATA_DIR/crawler_ranges.json" 2>/dev/null | cut -d' ' -f1 || true)
[ "$before" != "$after" ] && changed=1

[ -f "$DATA_DIR/sensors.json" ] || cp "$APP_DIR/botspotter/data/sensors.json" "$DATA_DIR/sensors.json"
chmod -R a+rX "$DATA_DIR"
ls -la "$DATA_DIR"
if [ "$changed" = 1 ] && [ "${RESTART:-1}" = 1 ]; then
  cd "$APP_DIR" && docker compose -f docker-compose.yml -f docker-compose.vm.yml up -d --no-deps --force-recreate ingest api aggregator
  echo "containers recreated with fresh data"
fi
