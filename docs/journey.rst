The journey: idea to production in one day
==========================================

This is the narrative record of how BotSpotter went from a question to a live site on 25 October 2026. Every decision here is expanded in :doc:`decisions`; every procedure in :doc:`runbook`. Read this first if you are new to the project.

.. _1-the-idea:

1. The idea
-----------

   "Is it possible to detect the AI bots along with their identity, start counting on the portal… a counter and below that a map… give them some name in a handshake so someone can call them in future… just for fun so people will track it."

Four requirements fell out of that:

1. A **live counter** of AI bot visits, with a **zoomable map** by region.
2. Bots are **named, not exposed** — a generated name people can search.
3. **New bots must appear automatically** as they launch.
4. Count **human visitors** too.

And two constraints added during design: **all Python** ("complete Python house") and **free-tier hosting** to start.

.. _2-what-is-actually-detectable-and-what-is-not:

2. What is actually detectable (and what is not)
------------------------------------------------

Nobody sees the whole internet. What a site *can* see per request: the User-Agent, the set and order of header names, the client IP (→ network and country), behaviour (robots.txt, rate, JS execution), the TLS fingerprint at the edge, and — for a few newer agents — a cryptographic Web Bot Auth signature. From these we derive three things:

-  **classification** — known crawler (registry) / heuristic automation / human;
-  **category** — training · search · agent · scraper · seo · unknown;
-  **identity** — a fingerprint hash → a deterministic name.

The honest framing became a product rule: the portal says *"AI bot visits spotted across N sensors in M countries"*, never "all bots in the world". Growth is more sensors, not bigger claims.

.. _3-architecture-decisions-in-order:

3. Architecture decisions, in order
-----------------------------------

+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| #   | Question                                  | Answer                                  | Why                                                                           |
+=====+===========================================+=========================================+===============================================================================+
| 001 | Java core or all-Python?                  | **All-Python**                          | one toolchain, solo developer, portfolio fit; ceiling is years away           |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 002 | Queue: Kafka / Redis Streams / Redpanda?  | **Redpanda**                            | durable partitioned log without the JVM; Streams would need a migration later |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 003 | Snapshot to R2 every second?              | **No — Redis + ``Cache-Control``**      | R2 free tier is 1M writes/month; CDN cache makes viewers free                 |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 004 | Airflow + Prometheus on the VM?           | **Not in v1**                           | 2+ GB RAM for idle services; GitHub Actions cron and logs suffice             |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 005 | Known bots: one identity across networks? | **Yes**                                 | GPTBot is GPTBot from any data centre; unknown bots keep the ``/16``          |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 006 | UA normalisation                          | keep parentheses                        | ``Mozilla/5.0 (compatible; XBot)`` is identified by what's inside             |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 007 | Claim                                     | "visits spotted", sensor count public   | credibility                                                                   |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 008 | Names                                     | deterministic, append-only word lists   | the whole point is permanence                                                 |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 009 | Basemap                                   | OpenFreeMap vector dark                 | CARTO now needs a key; raster OSM looked flat                                 |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 010 | "Where is the bot now?"                   | last sighting + recent list             | a crawler is never in one place                                               |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 011 | Public demo                               | Render free tier, in-memory             | zero-cost URL before buying a domain                                          |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+
| 012 | Production                                | shared Oracle A1 behind Sharpen's Caddy | the free A1 allowance was already in use                                      |
+-----+-------------------------------------------+-----------------------------------------+-------------------------------------------------------------------------------+

Full text: :doc:`decisions`.

.. _4-build:

4. Build
--------

The repository was scaffolded in one pass:

-  ``botspotter/`` — core library (``fingerprint``, ``detect``, ``geo``, ``enrich``, ``counters``) and three services (``ingest``, ``aggregator``, ``api``), with swappable ``stream/`` (memory \| Redpanda) and ``store/`` (memory \| Postgres) backends so the whole pipeline runs in one process for tests.
-  ``web/index.html`` — the portal, one static file, MapLibre, no build step.
-  ``sensors/`` — zero-dependency Python middleware (ASGI, WSGI, Django), an Nginx log format and a Vector config.
-  ``jobs/registry_sync.py`` — daily merge of public AI-bot lists into the registry, as a PR.
-  ``scripts/dev.py`` (single-process dev/demo server), ``seed_traffic.py`` (seed + load test), ``rebuild_counters.py``.
-  ``db/schema.sql`` — Postgres + TimescaleDB with retention and continuous aggregates.
-  30 tests, end-to-end in-process with fakeredis.

Two real bugs were found by running it, not by reading it: all ``Mozilla/5.0 (compatible; …)`` crawlers collapsed into one identity (006), and known bots fragmented into dozens of names by network (005).

.. _5-features-added-on-first-viewing:

5. Features added on first viewing
----------------------------------

-  Category chips (training / search / agents / scrapers / unidentified) with counts, filter and a stacked 24-hour chart.
-  Live sightings ticker; per-bot "last spotted" and last-25 sightings.
-  ``/about``, ``/sensors`` (key request form with terms acceptance) and ``/terms`` pages, client-side routed.
-  Map: CARTO → OSM raster → CSS-invert hack → OpenFreeMap vector (009); glow

   -  sonar-pulse dots; map init guarded so a CDN failure can't kill the counters.

.. _6-publish:

6. Publish
----------

1. ``git init``, first commit, ``gh repo create`` → https://github.com/bhushanladde02/botspotter.
2. Render Blueprint (``render.yaml``) → https://botspotter-demo.onrender.com. Within two minutes two unidentified crawlers had found the URL and been named — the self-sensor working on real traffic.
3. Lesson: the free Render service sleeps after 15 min and forgets everything (011). Fine for a demo, unfit for sharing.

.. _7-production:

7. Production
-------------

1. Domain: ``botspotter.app`` at Cloudflare Registrar ($8.20 first year, $14.20/yr renewal). ``.com`` variants were all taken; ``.io`` renews at $50.
2. VM reality check: the swing-bot VM is a 1 GB AMD micro (too small); the Sharpen VM is an A1 with 1 OCPU / 6 GB, 4.6 GB free, Docker and Caddy already on :80/:443. → deploy **alongside Sharpen** (012).
3. ``docker-compose.vm.yml`` overlay (no edge, join Caddy's network, memory trims ≈ 2.4 GB), ``deploy/Caddyfile.botspotter`` site block, ``deploy/remote.sh`` + ``deploy/oracle.sh``.
4. Cloudflare: ``A @ → 129.213.153.181`` proxied, ``CNAME www``, SSL Full, then Full (strict) once Caddy had its Let's Encrypt certificates.
5. First deploy hiccup: the Caddy drop-in directory is ``caddy-extra/`` on the host (mounted as ``/etc/caddy/extra``); the first script looked for ``extra/``. Fixed in ``remote.sh``, which now resolves the mount from Docker.
6. CI/CD added: tests on every push, auto-deploy to the VM on ``main``, docs to GitHub Pages. See :doc:`cicd`.

.. _8-whats-next:

8. What's next
--------------

See :doc:`growth` — triggers and backlog. Immediate items: legal review of ``/terms``, the ``hello@botspotter.app`` mailbox, first real sensor on the résumé site, keyed basemap before real traffic.
