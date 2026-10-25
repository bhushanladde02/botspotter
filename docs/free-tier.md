# Running on free tiers

Verified 2026-10-25. Free tiers change — re-check before relying on a number.

## Where each piece lives

| Piece | Host | Free limit that matters | How we stay under it |
|---|---|---|---|
| Whole backend (Compose) | **Oracle Cloud Always Free** Ampere A1 | **2 OCPU / 12 GB RAM** (cut from 4/24 in mid-2026), 200 GB block storage | memory budget below |
| DNS, TLS, DDoS, cache, rate limit | **Cloudflare Free** | 1 custom rate-limit rule; Pages = unlimited static requests | rate-limit rule on `/v1/ingest`; snapshot cached by `Cache-Control` |
| Portal page (optional) | Cloudflare Pages | unlimited static | `web/index.html` with `window.BS_API` set to the VM origin |
| Batch jobs | GitHub Actions | 2,000 min/month | one 1-minute job per day |
| Object storage (daily exports) | Cloudflare R2 | 10 GB, **1M class-A writes/month** | we do NOT write per-second snapshots to R2 (would be 2.6M/mo); only daily exports |
| GeoIP | MaxMind GeoLite2 | free with account | optional; CF country header is the default |

## RAM budget on the 12 GB VM

| Service | Limit in compose | Notes |
|---|---|---|
| Redpanda | 1,280 MB | `--memory 1G --overprovisioned --smp 1` |
| Postgres + Timescale | 1,024 MB | `shared_buffers=256MB` |
| Redis | 320 MB | `maxmemory 256mb`, AOF on |
| ingest ×2 workers | 256 MB | |
| api | 256 MB | |
| aggregator | 256 MB | |
| Nginx | 64 MB | |
| **Total** | **~3.5 GB** | leaves ~8 GB for the OS, the swing bot, and growth |

Prometheus/Grafana/Airflow are deliberately **not** on the VM in v1 (see `growth.md` for when they come back).

## Things that bite

1. **Oracle idle reclamation** — Always-Free instances idle under ~10–15% CPU/memory for 7 days can be reclaimed. Redpanda + Postgres + a 1-second snapshot loop keep the box comfortably busy.
2. **Oracle capacity** — A1 shapes are often "out of capacity"; retry at odd hours or pick another home region when creating the tenancy.
3. **Cloudflare free cache** — caches per edge location, so expect a few origin hits per 2 s worldwide, not one. Nginx's local `proxy_cache` makes that one hit per 2 s at the origin anyway.
4. **Workers/Queues are not used.** We considered them (100k req/day, Queues 10k ops/day). The VM is cheaper to reason about and has no daily caps.
5. **Honeypot discovery** — `*.pages.dev` / `*.workers.dev` sit under wildcard certs and don't appear individually in Certificate Transparency logs. Buy one cheap domain (~$10/yr) and give each honeypot its own subdomain with its own cert; new crawlers find them in minutes.

## When free breaks

- Sustained ingest > ~1–2k hits/s → second VM or a $20–40/mo VPS (same images).
- Postgres > ~150 GB → raise retention policy from 90 → 30 days, or move to managed Timescale.
- Redis > 256 MB → the per-bot hourly keys are the bulk; shorten their 14-day TTL to 7.
