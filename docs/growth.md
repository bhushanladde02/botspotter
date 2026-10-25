# Growth plan — what changes, and the number that triggers it

**Rule: add a component only when a measured number forces it. Not before.**
Update this file whenever a trigger fires or a step is done.

## Traffic tiers

| Tier | Signal | Change | Status |
|---|---|---|---|
| 0 — launch | a few sites + honeypots, <100 hits/s | this repo as-is on one Oracle VM | ✅ built 2026-10-25 |
| 1 — people watch | >10k concurrent viewers | nothing (snapshot is CDN-cached). Pin `web/` on Cloudflare Pages so the portal survives a VM reboot | ☐ |
| 2 — many sensors | >1k hits/s sustained, ingest CPU >70% | `--scale ingest=3`; move Redpanda to 2 partitions per topic; run 2 aggregators | ☐ |
| 3 — the VM is the limit | RAM >9 GB or disk >150 GB | second machine ($20–40/mo VPS or Oracle paid): Redpanda + Postgres there, app services on the free VM | ☐ |
| 4 — real product | sensors > 500, API consumers exist | Kubernetes (same images), managed Timescale, ClickHouse for ad-hoc analytics, Prometheus+Grafana, on-call | ☐ |

## Features that make people come back (ordered by payoff/effort)

1. **Shareable bot cards** — `/bot/<slug>` already works; add OG image generation (Pillow, in `api`) so links unfurl on X/LinkedIn/WhatsApp. ☐
2. **"New bot discovered" alerts** — RSS feed from `bs:feed` + a weekly email digest job. ☐
3. **Sensor leaderboard with claim** — site owners "claim" their sensor to show a name/logo; drives installs. ☐
4. **Public API keys** — read API already cache-friendly; add key issuance + per-key rate limit so people build on the data. ☐
5. **Country / operator pages** — `/country/US`, `/operator/openai` from existing ZSETs. ☐
6. **JA4 fingerprints** — pass `X-BS-JA4` from Nginx (`ngx_http_ja4` module) or Cloudflare (`cf.bot_management.ja4`, Enterprise); tightens identity for unknown bots. ☐
7. **City-level dots** — mount GeoLite2-City on the VM, set `BS_GEOIP_DB`. ☐
8. **WordPress plugin** (PHP, forwards to `/v1/ingest`) — biggest install base on the web. ☐
9. **Keyed basemap** — move from OSM public tiles to MapTiler/Stadia free tier before real traffic (OSM tile policy). ☐
10. **Vector-less Nginx** — a tiny Go/Rust binary that tails logs, for people who won't install Vector. ☐

10. **Legal review of `/terms`** before the first non-owned sensor goes live; add a DPA template if an EU business asks. ☐
11. **Automated key issuance** — replace the mailto form with a signup that stores `sha256(key)` in the `sensors` table and records terms acceptance + timestamp. ☐

## Batch jobs → Airflow

Today: `jobs/*.py` on GitHub Actions cron. Move to Airflow on the VM when **either** there are ≥5 jobs **or** a job needs retries/backfills/dependencies. The scripts are already DAG-shaped (idempotent, single entry point).

## Rewrite triggers (be honest)

- Ingest is CPU-bound above ~20–40k req/s per process. If we ever sustain that, rewrite `ingest` in Go or Rust — it is 300 lines, the contract is `/v1/ingest` + the `BotEvent` schema. Not Java. Nothing else changes.
- Redis single-primary becomes a risk above ~50k events/s → Redis Cluster or move counters to Redpanda-side materialization (ksqlDB-like) — far away.
