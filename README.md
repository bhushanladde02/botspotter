# 🛰️ BotSpotter

**Every AI bot on the internet, counted and named.**

A live counter and world map of AI crawlers and agents (GPTBot, ClaudeBot, PerplexityBot, Bytespider, and the ones nobody has named yet) as seen by a crowd-sourced network of sensor sites and honeypots. Every bot gets a deterministic name — *Copper Heron #0042* — that stays the same on every site, forever, so you can search it, share it, and watch it evolve. Humans are counted too, without cookies.

> "AI bot visits spotted across N sensors in M countries." Nobody sees the whole internet; we say so on the page and grow the sensor count instead.

## Quick start (no Docker)
```bash
pip install -e ".[dev]" && pytest -q
python scripts/dev.py                      # http://localhost:8000
python scripts/seed_traffic.py --url http://localhost:8000/v1/ingest --key devkey --hits 2000
```

## Full stack (Oracle free VM or any Docker host)
```bash
cp .env.example .env && docker compose up -d --build   # see docs/runbook.rst
```

## How it works (30 seconds)
sensor → `POST /v1/ingest` → classify (known registry / heuristics) → fingerprint (UA family + header order + network for unknowns) → **drop the IP** → Redpanda → aggregator → Redis counters + Postgres/Timescale history → `/v1/snapshot` every second → CDN-cached → the page polls it.

Full detail: [`docs/architecture.rst`](docs/architecture.rst).

**Check any site** — [botspotter.app/check](https://botspotter.app/check): what any domain's `robots.txt` asks 16 AI crawlers to do, and whether an IP in your logs is really the bot it claims to be.

## Docs
Full documentation (Sphinx): https://bhushanladde02.github.io/botspotter/ — built from `docs/` by CI.

| | |
|---|---|
| [architecture.rst](docs/architecture.rst) | components, data flow, identity model, failure modes |
| [free-tier.rst](docs/free-tier.rst) | exactly how this fits Oracle Always Free + Cloudflare Free, RAM budget |
| [growth.rst](docs/growth.rst) | what changes at each traffic tier and the number that triggers it; feature backlog |
| [decisions.rst](docs/decisions.rst) | why Python, why Redpanda not Kafka/Redis Streams, why no R2 snapshots… |
| [runbook.rst](docs/runbook.rst) | deploy, keys, honeypots, rotate secrets, rebuild counters |
| [sensors.rst](docs/sensors.rst) | add your site (Python middleware, Nginx+Vector, raw wire format) |
| [cicd.rst](docs/cicd.rst) | tests → auto-deploy to the VM → docs to GitHub Pages |
| [journey.rst](docs/journey.rst) | the whole story, idea to production |
| [privacy.rst](docs/privacy.rst) | what is and isn't stored |

## Layout
```
botspotter/        core library + the three services (ingest, aggregator, api)
  fingerprint.py   identity + naming        detect.py   bot registry + heuristics
  enrich.py        hit → anonymized event   counters.py Redis live state
  stream/          memory | kafka(Redpanda) store/      memory | postgres
sensors/           python-middleware (pip package), vector/, nginx/
web/index.html     the portal (MapLibre, no build step)
jobs/              batch jobs (GitHub Actions cron → Airflow later)
scripts/           dev.py, seed_traffic.py (also load test), rebuild_counters.py
db/schema.sql      Postgres + TimescaleDB
deploy/nginx.conf  edge
tests/             29 tests, run end-to-end in-process
```

## Stack
Python 3.10+ · FastAPI · Granian/uvloop · Redpanda · Redis · PostgreSQL + TimescaleDB · MapLibre GL · Docker Compose · Cloudflare.

## Credits
Built by Bhushan Ladde. Architecture, analysis and testing are his own; AI assistance (Claude) was used for drafting code and docs.

## Support
Questions or integration help: open an issue, or email bhushanladde02@gmail.com.

## License
MIT © 2026 Bhushan Arun Ladde — see [LICENSE](LICENSE).
