# Local setup

## Requirements

- Python 3.10+ (3.12 used in CI and production)
- macOS note: python.org builds ship without root certificates; run
  `/Applications/Python 3.12/Install Certificates.command` once or
  `pip install certifi` (scripts use certifi when present).
- Docker + Compose v2 only for the full stack; the dev server needs nothing.

## Clone and install

```bash
git clone https://github.com/bhushanladde02/botspotter.git
cd botspotter
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # quote the brackets in zsh
python -m pytest -q              # expect 30 passed
```

Use `python -m pytest`, not bare `pytest`, if another pytest is on your PATH.

## Run the single-process dev server

```bash
python scripts/dev.py            # http://localhost:8000
python scripts/seed_traffic.py --url http://localhost:8000/v1/ingest --key devkey --hits 2000
```

`dev.py` runs portal + API + ingest + aggregator in one process with an
in-memory stream, fakeredis and an in-memory store. Data lives until Ctrl-C.
`devkey` → site `dev-site` is the default when `BS_SENSOR_KEYS` is unset.

## Run the full stack locally

```bash
cp .env.example .env             # edit CHANGE values
docker compose up -d --build     # http://localhost/
```

## PyCharm

Settings → Project → Python Interpreter → Add Interpreter → Add Local →
Virtualenv → **New**, location `<repo>/.venv`, base Python 3.12. Then install
in the PyCharm terminal as above. Run configurations: `scripts/dev.py`
(script) and `tests` (pytest).

## Layout

```
botspotter/        core library + the three services
  fingerprint.py   identity + naming        detect.py   bot registry + heuristics
  enrich.py        hit → anonymized event   counters.py Redis live state
  geo.py geohash.py models.py config.py names.py
  stream/          memory | kafka(Redpanda) store/      memory | postgres
  ingest/ api/ aggregator/
web/index.html     the portal (MapLibre, no build step)
sensors/           python-middleware (pip package), vector/, nginx/
jobs/              batch jobs (GitHub Actions cron)
scripts/           dev.py, seed_traffic.py, rebuild_counters.py
db/schema.sql      Postgres + TimescaleDB
deploy/            remote.sh (runs on VM), oracle.sh (laptop wrapper), Caddyfile.botspotter, nginx.conf (standalone)
docker-compose.yml          standalone stack with its own Nginx edge
docker-compose.vm.yml       overlay for the shared VM behind Caddy
render.yaml        free ephemeral demo
docs/              this documentation (Sphinx + MyST)
tests/             30 tests, end-to-end in-process
private/           local-only notes and credentials (gitignored)
```

## Environment variables

All settings live in `botspotter/config.py` with safe defaults. The ones you
set in production (`.env`):

| Variable | Purpose |
|---|---|
| `PG_PASSWORD` | Postgres password used by Compose and the DSN |
| `BS_SENSOR_KEYS` | `key:site_id,...` — sensor authentication |
| `BS_NAMING_SALT` | part of every bot name; **set once, never rotate** |
| `BS_VISITOR_SECRET` | secret prefix of the daily human-visitor hash |
| `BS_GEOIP_DB` | optional GeoLite2-City path for city-level dots |
| `BS_DISCOVERY_MIN_SITES` / `BS_DISCOVERY_WINDOW_S` | discovery rule (3 sites / 24 h) |
| `CADDY_NETWORK` | shared-VM only: Caddy's docker network (filled by `remote.sh`) |
| `BS_STREAM`, `BS_STORE`, `BS_REDIS_URL`, `BS_KAFKA_BOOTSTRAP`, `BS_PG_DSN` | backends; set by Compose |
