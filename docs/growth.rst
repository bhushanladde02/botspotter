.. _growth-plan--what-changes-and-the-number-that-triggers-it:

Growth plan — what changes, and the number that triggers it
===========================================================

**Rule: add a component only when a measured number forces it. Not before.** Update this file whenever a trigger fires or a step is done.

Traffic tiers
-------------

+-------------------------+---------------------------------------+-----------------------------------------------------------------------------------------------------------+---------------------+
| Tier                    | Signal                                | Change                                                                                                    | Status              |
+=========================+=======================================+===========================================================================================================+=====================+
| 0 — launch              | a few sites + honeypots, <100 hits/s  | this repo as-is on one Oracle VM                                                                          | ✅ built 2026-10-25 |
+-------------------------+---------------------------------------+-----------------------------------------------------------------------------------------------------------+---------------------+
| 1 — people watch        | >10k concurrent viewers               | nothing (snapshot is CDN-cached). Pin ``web/`` on Cloudflare Pages so the portal survives a VM reboot     | ☐                   |
+-------------------------+---------------------------------------+-----------------------------------------------------------------------------------------------------------+---------------------+
| 2 — many sensors        | >1k hits/s sustained, ingest CPU >70% | ``--scale ingest=3``; move Redpanda to 2 partitions per topic; run 2 aggregators                          | ☐                   |
+-------------------------+---------------------------------------+-----------------------------------------------------------------------------------------------------------+---------------------+
| 3 — the VM is the limit | RAM >9 GB or disk >150 GB             | second machine ($20–40/mo VPS or Oracle paid): Redpanda + Postgres there, app services on the free VM     | ☐                   |
+-------------------------+---------------------------------------+-----------------------------------------------------------------------------------------------------------+---------------------+
| 4 — real product        | sensors > 500, API consumers exist    | Kubernetes (same images), managed Timescale, ClickHouse for ad-hoc analytics, Prometheus+Grafana, on-call | ☐                   |
+-------------------------+---------------------------------------+-----------------------------------------------------------------------------------------------------------+---------------------+

Features that make people come back (ordered by payoff/effort)
--------------------------------------------------------------

1.  **Shareable bot cards** — ``/bot/<slug>`` already works; add OG image generation (Pillow, in ``api``) so links unfurl on X/LinkedIn/WhatsApp. ☐

2.  **"New bot discovered" alerts** — RSS feed from ``bs:feed`` + a weekly email digest job. ☐

3.  **Sensor leaderboard with claim** — site owners "claim" their sensor to show a name/logo; drives installs. ☐

4.  **Public API keys** — read API already cache-friendly; add key issuance + per-key rate limit so people build on the data. ☐

5.  **Country / operator pages** — ``/country/US``, ``/operator/openai`` from existing ZSETs. ☐

6.  **JA4 fingerprints** — pass ``X-BS-JA4`` from Nginx (``ngx_http_ja4`` module) or Cloudflare (``cf.bot_management.ja4``, Enterprise); tightens identity for unknown bots. ☐

7.  **City-level dots** — mount GeoLite2-City on the VM, set ``BS_GEOIP_DB``. ☐

8.  **WordPress plugin** (PHP, forwards to ``/v1/ingest``) — biggest install base on the web. ☐

9.  **Keyed basemap** — move from OSM public tiles to MapTiler/Stadia free tier before real traffic (OSM tile policy). ☐

10. **Vector-less Nginx** — a tiny Go/Rust binary that tails logs, for people who won't install Vector. ☐

11. **Legal review of ``/terms``** before the first non-owned sensor goes live; add a DPA template if an EU business asks. ☐

12. **Automated key issuance** — replace the mailto form with a signup that stores ``sha256(key)`` in the ``sensors`` table and records terms acceptance + timestamp. ☐

.. _batch-jobs--airflow:

Visibility checklist (launch)
-----------------------------

Shipped: OG/Twitter cards + JSON-LD, ``robots.txt`` that welcomes bots, ``sitemap.xml`` (includes discovered bot pages), ``feed.xml`` RSS of discoveries, live SVG **badge per sensor** (backlink engine), ``/claim`` (gives operators a reason to link to their bot page).

To do, by payoff:

1. Submit ``https://botspotter.app/sitemap.xml`` to Google Search Console and Bing Webmaster Tools (free; also triggers Googlebot/Bingbot visits = more named bots on day one).
2. Launch post on LinkedIn + X + Hacker News ("Show HN: BotSpotter — live map of the AI bots crawling the web") + r/webdev; the referrer card shows which one works.
3. Add the badge to your own three sites; each is a backlink and a sensor.
4. Submit to directories that list live dashboards / internet observatories; email the maintainers of ai.robots.txt and the Dark Visitors blog — they cover exactly this.
5. Weekly "state of the crawl" post generated from the data (top bots, new discoveries) — recurring content is what search engines reward.
6. OG image per bot (``/bot/<slug>`` cards unfurl with the name + stats) once there's a PNG renderer.

Evidence of reach (keep for the record)
---------------------------------------

The ``/stats`` page is the public, verifiable record of audience. For a durable paper trail: download ``/v1/stats/humans.csv?days=1100`` monthly into ``private/evidence/`` (gitignored), alongside screenshots of ``/stats``, press mentions, the GitHub star count and Cloudflare's own analytics export. Independent third-party numbers (Cloudflare Analytics, Search Console) corroborate the self-reported ones.

Batch jobs → Airflow
--------------------

Today: ``jobs/*.py`` on GitHub Actions cron. Move to Airflow on the VM when **either** there are ≥5 jobs **or** a job needs retries/backfills/dependencies. The scripts are already DAG-shaped (idempotent, single entry point).

Rewrite triggers (be honest)
----------------------------

-  Ingest is CPU-bound above ~20–40k req/s per process. If we ever sustain that, rewrite ``ingest`` in Go or Rust — it is 300 lines, the contract is ``/v1/ingest`` + the ``BotEvent`` schema. Not Java. Nothing else changes.
-  Redis single-primary becomes a risk above ~50k events/s → Redis Cluster or move counters to Redpanda-side materialization (ksqlDB-like) — far away.
