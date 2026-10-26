Architecture
============

   Keep the hot path tiny, cache everything the public reads, make every layer replaceable by config. All Python. (See ``decisions.md`` for *why*.)

::

                         sensors (other people's sites)              honeypots (ours)
        ┌──────────────┬────────────────┬───────────────┐          ┌─────────────┐
        │ Python        │ Nginx/Apache   │ portal itself  │          │ fresh domains│
        │ middleware    │ + Vector       │ (API service)  │          │ in CT logs   │
        └──────┬───────┴───────┬────────┴───────┬───────┘          └──────┬──────┘
               │  POST /v1/ingest  (X-BS-Key)   │                          │
               ▼                                ▼                          ▼
      ┌────────────────────────────────────────────────────────────────────────────┐
      │ edge: Cloudflare (TLS, DDoS, rate-limit, cache)  →  Nginx (deploy/nginx.conf)│
      └──────────────┬─────────────────────────────────────────────┬───────────────┘
                     │ /v1/ingest, /v1/beacon                      │ everything else
                     ▼                                             ▼
           ┌──────────────────┐                          ┌──────────────────┐
           │ ingest (FastAPI) │  auth → validate →        │ api (FastAPI)    │  reads Redis only
           │ N replicas       │  classify → fingerprint → │ + portal page    │  Cache-Control 2s
           │ no DB access     │  geo → DROP IP → publish  │ + self-sensor    │
           └────────┬─────────┘                          └────────▲─────────┘
                    │ bot_events / portal_visits (Redpanda)         │
                    ▼                                               │
           ┌──────────────────┐   counters, map buckets,   ┌────────┴─────────┐
           │ aggregator       │──────────────────────────▶ │ Redis            │
           │ (one consumer    │   discovery, snapshot/1s   │ live state       │
           │  group)          │                             └──────────────────┘
           │                  │   COPY events, upsert bots ┌──────────────────┐
           │                  │──────────────────────────▶ │ Postgres +       │
           └──────────────────┘                             │ TimescaleDB      │
                                                            │ history / truth  │
      batch (GitHub Actions cron → later Airflow):          └──────────────────┘
      registry sync · GeoIP refresh · UA clustering · weekly digest

Components
----------

+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+
| Component         | Path                                                       | Runs as                                     | Talks to                                     |
+===================+============================================================+=============================================+==============================================+
| Ingest            | ``botspotter/ingest/app.py``                               | Granian, 2 workers, N replicas              | Redpanda (produce)                           |
+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+
| Aggregator        | ``botspotter/aggregator/run.py``                           | 1 process (scale by partitions)             | Redpanda (consume), Redis, Postgres          |
+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+
| Read API + portal | ``botspotter/api/app.py``                                  | Granian, 1 worker                           | Redis (read), Redpanda (self-sensor produce) |
+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+
| Edge              | ``deploy/nginx.conf``                                      | Nginx                                       | ingest, api                                  |
+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+
| Core library      | ``botspotter/{fingerprint,detect,geo,enrich,counters}.py`` | imported                                    | —                                            |
+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+
| Sensors           | ``sensors/``                                               | on other servers                            | ingest                                       |
+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+
| Batch jobs        | ``jobs/``                                                  | GitHub Actions                              | registry file / Postgres                     |
+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+
| Frontend          | ``web/index.html``                                         | static (served by api, or Cloudflare Pages) | api, ingest (beacon)                         |
+-------------------+------------------------------------------------------------+---------------------------------------------+----------------------------------------------+

Data flow for one request
-------------------------

1. A crawler hits a sensor site. The sensor records **only**: header *names* in order, User-Agent, client IP, ``CF-IPCountry``, method, status, and whether the path was ``robots.txt``. Browsers are filtered on the sensor side so they never leave the site.
2. Sensor batches ≤500 hits and POSTs ``{"events":[…]}`` with ``X-BS-Key``.
3. Ingest: key → ``site_id``; ``detect.classify()`` → known / heuristic / human; humans dropped; ``fingerprint.make_fingerprint()`` → ``fp`` (identity) and ``fam`` (family); ``geo.lookup()`` → country + lat/lon; **IP discarded**; ``BotEvent`` published keyed by ``fp`` (same bot → same partition → ordered).
4. Aggregator consumes batches of ≤500: one Redis pipeline updates ~15 keys per event; new fingerprints are registered; unknown bots seen on ≥3 sites in 24h become *discovered*; events ``COPY``'d to Postgres; offsets committed.
5. Every second the aggregator rewrites ``bs:snapshot`` (counters, top lists, feed, sparkline).
6. The portal polls ``/v1/snapshot`` every 2s. Nginx/Cloudflare serve it from cache, so viewer count never reaches Python.

Identity model
--------------

-  **Fingerprint** = blake2b(UA family + ordered header names + network/16 [unknown bots only] + JA4 [if provided]) — see ``fingerprint.py`` docstring.
-  **Name** = deterministic ``Adjective Animal #NNNN`` from the fingerprint (40M name space, append-only word lists).
-  **Family** = hash of UA family alone. Two fingerprints in one family = the same software from different networks or with changed headers → "evolved from" lineage.
-  Known crawlers (GPTBot, ClaudeBot, …) are **one identity regardless of network**, because the operator is public. Unknown automation includes the network, so "python-requests from DigitalOcean" and "python-requests from Hetzner" are different bots.

What the portal shows per bot
-----------------------------

``/v1/bots/{slug}``: name, operator, category (training / search / agent / scraper / seo / unknown), software family, first spotted (when + country), **last spotted** (ago + country + sensor), hits, sites crawled, top countries, 24h sparkline, **recent sightings** (last 25: time, country, sensor), lineage ("evolved from"). "Where is it now" = last sighting; a crawler is never in one place, it runs from many machines at once, so presence is only as fine as the sensor network.

``/v1/sightings`` and ``snapshot.sightings``: global live ticker of the last 100 hits across all sensors.

Privacy boundary
----------------

Everything after step 3 contains no IP, no raw UA, no path, no cookies. Humans on the portal are counted by ``blake2b(secret + UTC-day + ip + ua)`` → unlinkable across days, uniqueness via HyperLogLog. There is nothing to leak. See ``privacy.md``.

Failure modes
-------------

+-------------------------------------------+---------------------------------------------------------------------------------------------------------------+-------------------------------------------------------------------------+
| Failure                                   | Effect                                                                                                        | Recovery                                                                |
+===========================================+===============================================================================================================+=========================================================================+
| Redis lost                                | counters reset                                                                                                | ``scripts/rebuild_counters.py`` from Postgres                           |
+-------------------------------------------+---------------------------------------------------------------------------------------------------------------+-------------------------------------------------------------------------+
| Aggregator crash mid-batch                | ≤500 events double-counted in Redis once                                                                      | rebuild if it matters; Postgres is exact                                |
+-------------------------------------------+---------------------------------------------------------------------------------------------------------------+-------------------------------------------------------------------------+
| Postgres write fails (bad SQL, disk full) | history for that batch lost; counters unaffected; ``bs:errors:<op>`` increments and the log has the traceback | fix, redeploy; replay from Redpanda (7 days) if the gap matters         |
+-------------------------------------------+---------------------------------------------------------------------------------------------------------------+-------------------------------------------------------------------------+
| Redpanda down                             | ingest returns 500, sensors retry/drop                                                                        | sensors buffer up to 10k hits in memory                                 |
+-------------------------------------------+---------------------------------------------------------------------------------------------------------------+-------------------------------------------------------------------------+
| Postgres down                             | counters keep updating; history gap                                                                           | Redpanda retains 7 days → replay by resetting the consumer group offset |
+-------------------------------------------+---------------------------------------------------------------------------------------------------------------+-------------------------------------------------------------------------+
| Ingest overloaded                         | Cloudflare/Nginx rate-limit 429                                                                               | ``docker compose up -d --scale ingest=3``                               |
+-------------------------------------------+---------------------------------------------------------------------------------------------------------------+-------------------------------------------------------------------------+

Live map (since 2026-10-26)
---------------------------

The map is the product's front door, so it shows *motion*, not pins. Every sighting is an arc from where the bot
lives to the site it is crawling, drawn within about a second of the hit.

- **Origin**: GeoLite2-City (``BS_GEOIP_DB``) gives city-level coordinates and the city name; GeoLite2-ASN
  (``BS_GEOIP_ASN_DB``) gives the network, reduced to a hosting label (``geo.cloud_label``: AWS, Azure, GCP, OCI,
  Hetzner, …). Without the databases the origin is the country centroid, as before. Both files are downloaded on the
  VM by ``deploy/refresh-data.sh`` (MaxMind account, EULA forbids redistribution) and refreshed weekly by cron.
- **Destination**: the sensor site's city from ``sensors.json`` (``botspotter/data/`` packaged, ``/data`` on the VM
  overrides). Owners give a city when they request a key; never an address.
- **Impostors** (``verify.py``): operators that publish their crawler IP ranges (OpenAI, Google, Microsoft, Apple,
  Perplexity, DuckDuckGo) are checked in-process before the IP is discarded. ``BotEvent.verified`` is ``verified``,
  ``impostor`` or ``""`` (no published ranges, so no claim). Ranges are built by ``jobs/crawler_ranges.py`` from the
  official URLs; the packaged copy is empty so a fresh checkout never calls anything an impostor.
- **Stream**: the aggregator publishes each sighting on Redis pub/sub ``bs:live`` (origin, destination, name, slug,
  category, verified, cloud, city). ``GET /v1/live`` relays it as server-sent events; Caddy proxies it unbuffered
  (``flush_interval -1``, no compression for ``text/event-stream``). Viewers reconnect automatically.
- **Rendering**: a 2-D canvas over MapLibre (``web/index.html``, "LIVE" section). Great-circle arcs lifted in screen
  space, colored by category (impostors red), a comet head with the label, a ripple on arrival, sensor sites as
  diamonds that flare when hit. Everything is re-projected per frame, so panning and zooming cost nothing extra;
  400 arcs cap. Hovering a bot anywhere on the page ("follow") dims every other arc. Optional sound.
- New snapshot fields: ``trust`` (verified/impostor counters, impostors by claimed operator), ``by_cloud``,
  ``network.sites``. ``GET /v1/sites`` lists sensor locations.
